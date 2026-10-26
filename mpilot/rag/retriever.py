"""근거 검색과 근거 충분성 판단.

역할:
    질문에 맞는 근거를 `manual`·`history` 컬렉션에서 찾아 `SearchHit`으로 돌려준다.
    고객 신고에서는 검색하지 않고, 허용된 매뉴얼 절차 원문만 직접 조회한다.

책임:
    - 모든 검색에 설비 필터를 건다. 다른 설비의 같은 이름 에러를 근거로 돌려주지 않는다.
    - 엔지니어 대화는 `manual`과 `history`를 검색한다. 고객 안내는 `customer_procedure_ref_ids`의 ID로
      `manual` 원문을 직접 조회하며 유사도 임계값을 적용하지 않는다. `history`는 고객 안내에 쓰지 않는다.
    - `history` 결과는 `case_id`로 중복을 제거한다.
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
    MpilotError,
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
    return _search_scoped(CitationKind.MANUAL, equipment, query, k)


def search_history(equipment: Equipment, query: str, k: int) -> list[SearchHit]:
    """설비 필터로 확정 이력 근거를 검색한다.

    Args:
        equipment: 검색 대상 설비. 이 설비의 이력만 찾는다.
        query: 검색 질의 문장.
        k: 가져올 상위 건수.

    Returns:
        list[SearchHit]: `history` 컬렉션의 상위 `k`건. `case_id`로 중복이 제거되고 거리가 가까운 순이다.

    Raises:
        ValidationError: 질의가 비었거나 `k`가 1보다 작을 때.
        DependencyError: 임베딩 생성이나 저장소 검색에 실패했을 때.
    """
    hits = _search_scoped(CitationKind.HISTORY, equipment, query, k)
    # 거리 오름차순이므로 같은 case_id는 처음 나온(가장 가까운) 것만 남는다.
    unique: dict[str, SearchHit] = {}
    for hit in hits:
        unique.setdefault(hit.ref_id.removeprefix("history:"), hit)
    return list(unique.values())


def _search_scoped(
    kind: CitationKind, equipment: Equipment, query: str, k: int
) -> list[SearchHit]:
    """설비 필터를 건 유사도 검색 공통부. 거리 오름차순 `SearchHit` 목록을 돌려준다."""
    if not query.strip():
        raise ValidationError("검색 질의가 비어 있다")
    if k < 1:
        raise ValidationError(f"k는 1 이상이어야 한다: {k}")
    try:
        store = vectordb.get_store(kind)
        scored = store.similarity_search_with_score(
            query, k=k, filter={"equipment": equipment.value}
        )
        hits = [_to_hit(doc, distance) for doc, distance in scored]
        return sorted(hits, key=lambda hit: hit.distance)
    except MpilotError:
        raise
    except Exception as e:
        raise DependencyError(f"{kind.value} 검색에 실패했다") from e


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
    ...


def _to_hit(doc: Document, distance: float | None) -> SearchHit:
    """저장소 문서 한 건을 `SearchHit`으로 바꾼다. 메타데이터가 계약과 다르면 `DependencyError`."""
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
