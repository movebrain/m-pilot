"""근거 검색과 근거 충분성 판단.

역할:
    질문에 맞는 근거를 `manual`·`history` 컬렉션에서 찾아 `SearchHit`으로 돌려준다.
    고객 신고에서는 검색하지 않고, 허용된 매뉴얼 절차 원문만 직접 조회한다.

책임:
    - 모든 검색에 설비 필터를 건다. 다른 설비의 같은 이름 에러를 근거로 돌려주지 않는다.
    - 엔지니어 대화는 `manual`과 `history`를 검색한다. 고객 안내는 `customer_procedure_ref_ids`의 ID로
      `manual` 원문을 직접 조회하며 유사도 임계값을 적용하지 않는다. `history`는 고객 안내에 쓰지 않는다.
    - `history`의 `case_id` 중복은 색인이 막는다(`history:<case_id>` ID로 upsert). 검색에서 다시 거르지 않는다.
    - 검색 결과가 근거로 충분한지 판단한다(임계값은 사람이 평가 결과를 보고 정한다).
    - 답변을 만들거나 DB에 쓰지 않는다.

의존: `db.vectordb`, `models`만 import한다(의존 규칙 §2.5). `db.rdb`는 부르지 않는다.
호출: `rag.pipeline`.
소유: RAG 파트
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mpilot.db import vectordb
from mpilot.models import (
    CitationKind,
    DependencyError,
    Equipment,
    ErrorCode,
    NotFoundError,
    SearchHit,
    ValidationError,
)

if TYPE_CHECKING:
    from langchain_core.documents import Document


def search_manual(equipment: Equipment, query: str, k: int) -> list[SearchHit]:
    """설비 필터로 매뉴얼 근거를 검색한다.

    Args:
        equipment: 검색 대상 설비. 이 설비의 문서만 찾는다.
        query: 검색 질의 문장.
        k: 가져올 상위 건수.

    Returns:
        list[SearchHit]: `manual` 컬렉션의 상위 `k`건. 벡터 거리(`distance`)가 채워지며 가까운 순이다.
            코드 표 행(`error:...`)과 매뉴얼 본문(`manual:...`)이 함께 나올 수 있다.

    Raises:
        ValidationError: 질의가 비었거나 `k`가 1보다 작을 때.
        DependencyError: 임베딩 생성이나 저장소 검색에 실패했을 때.
    """
    return _search_by_equipment(CitationKind.MANUAL, equipment, query, k)


def search_history(equipment: Equipment, query: str, k: int) -> list[SearchHit]:
    """설비 필터로 확정 이력 근거를 검색한다.

    Args:
        equipment: 검색 대상 설비. 이 설비의 이력만 찾는다.
        query: 검색 질의 문장.
        k: 가져올 상위 건수.

    Returns:
        list[SearchHit]: `history` 컬렉션의 상위 `k`건. 거리가 가까운 순이다.

    Raises:
        ValidationError: 질의가 비었거나 `k`가 1보다 작을 때.
        DependencyError: 임베딩 생성이나 저장소 검색에 실패했을 때.
    """
    return _search_by_equipment(CitationKind.HISTORY, equipment, query, k)


def _search_by_equipment(
    kind: CitationKind, equipment: Equipment, query: str, k: int
) -> list[SearchHit]:
    """설비 필터를 건 유사도 검색의 공통부. `search_manual`과 `search_history`가 쓴다.

    Args:
        kind: 검색할 컬렉션. `CitationKind.MANUAL` 또는 `CitationKind.HISTORY`.
        equipment: 검색 대상 설비. 메타데이터 `equipment`로 걸러 이 설비의 문서만 찾는다.
        query: 검색 질의 문장. 공백만 있으면 안 된다.
        k: 가져올 상위 건수. 1 이상이어야 한다.

    Returns:
        list[SearchHit]: 필터에 맞는 문서를 `distance` 오름차순으로 정렬한 근거. 없으면 빈 목록이다.

    Raises:
        ValidationError: 질의가 비었거나 공백뿐이거나 `k`가 1보다 작을 때. `get_store`가 낸 것은 그대로 올린다.
        DependencyError: 저장소 검색에 실패했거나 문서 메타데이터가 계약과 다를 때. `get_store`가 낸 것
            (예: `connect` 전 호출)은 변환 없이 그대로 올린다.
    """
    if not query.strip():
        raise ValidationError("검색 질의가 비어 있다")
    if k < 1:
        raise ValidationError(f"k는 1 이상이어야 한다: {k}")
    store = vectordb.get_store(kind)  # get_store의 오류는 계약된 예외라 변환하지 않는다
    try:
        scored = store.similarity_search_with_score(
            query, k=k, filter={"equipment": equipment.value}
        )
        scored = sorted(scored, key=lambda pair: pair[1])  # 거리가 None이면 여기서 TypeError
    except Exception as e:
        raise DependencyError(f"{kind.value} 검색에 실패했다") from e
    return [_to_hit(doc, distance) for doc, distance in scored]


def fetch_customer_procedures(error_code: ErrorCode) -> list[SearchHit]:
    """고객이 수행할 매뉴얼 절차 원문을 가져온다.

    `error_code.customer_procedure_ref_ids`에 적힌 ID의 `manual` 원문만 직접 조회한다. 검색하지 않으며
    유사도 임계값도 적용하지 않는다.

    Args:
        error_code: 신고한 에러코드 표의 행. 허용 절차 ID 목록을 읽는다.

    Returns:
        list[SearchHit]: 허용 절차 원문. 직접 조회한 근거이므로 `distance`는 `None`이다.
            허용 ID가 비어 있으면 빈 목록.

    Raises:
        DependencyError: 저장소 조회에 실패했을 때(허용 ID의 원문이 없는 경우 포함).
    """
    hits = []
    for ref_id in error_code.customer_procedure_ref_ids:
        try:
            doc = vectordb.get_document(CitationKind.MANUAL, ref_id)
        except NotFoundError as e:
            # 허용 절차 원문이 없으면 조용히 건너뛰지 않고 오류로 알린다.
            raise DependencyError(f"허용 절차 원문을 찾을 수 없다: {ref_id}") from e
        hits.append(_to_hit(doc, None))
    return hits


def _to_hit(doc: Document, distance: float | None) -> SearchHit:
    """저장소 문서 한 건을 `SearchHit`으로 바꾼다.

    문서 ID(`Document.id`)의 접두(`history:`)로 컬렉션 종류를 가리고, 메타데이터에서 설비·코드·출처 표기를 읽는다.
    출처 표기(`locator`)는 매뉴얼이 `<매뉴얼 ID> <쪽>쪽 <절>`, 이력이 `접수 <case_id> <코드>`다.

    Args:
        doc: 저장소가 돌려준 문서. `id`와 메타데이터 `equipment`를 읽고, 매뉴얼은 `manual_id`·`page`·`section`,
            이력은 `case_id`·`code`도 읽는다.
        distance: 질의와의 벡터 거리. 직접 조회한 근거는 `None`.

    Returns:
        SearchHit: 변환된 근거. `code`는 코드 표 행과 이력에만 있고 매뉴얼 본문은 `None`이다.

    Raises:
        DependencyError: 문서 ID가 없거나 필요한 메타데이터가 빠졌거나 설비 값이 4종 밖일 때
            (저장소가 계약과 다른 문서를 돌려준 경우).
    """
    meta = doc.metadata
    try:
        ref_id = doc.id
        kind = CitationKind.HISTORY if ref_id.startswith("history:") else CitationKind.MANUAL
        if kind is CitationKind.HISTORY:
            locator = f"접수 {meta['case_id']} {meta['code']}"
        else:
            locator = f"{meta['manual_id']} {meta['page']}쪽 {meta['section']}"
        return SearchHit(
            ref_id=ref_id,
            kind=kind,
            equipment=Equipment(meta["equipment"]),
            code=meta.get("code"),
            text=doc.page_content,
            locator=locator,
            distance=distance,
        )
    except (AttributeError, KeyError, ValueError) as e:
        raise DependencyError(f"검색 문서의 메타데이터가 계약과 다르다: {doc.id}") from e


def has_enough_evidence(hits: list[SearchHit], threshold: float) -> bool:
    """검색 결과가 근거로 충분한지 판단한다.

    판단 기준은 각 근거의 `distance`와 `threshold`의 비교다. 비교 방식과 결합 규칙은
    보류 정확도·정상 답변율 평가 결과를 보고 정한다.

    Args:
        hits: 검색으로 얻은 근거 목록.
        threshold: 근거 충분성 임계값(`SearchSettings.evidence_threshold`).

    Returns:
        bool: 근거로 충분하면 `True`, 부족하면 `False`(근거 부족 판정의 입력이 된다).

    Raises:
        ValidationError: `threshold`가 허용 범위를 벗어났을 때.
    """
    ...
