"""근거 검색과 근거 충분성 판단.

역할:
    질문에 맞는 근거를 `manual`·`history` 컬렉션에서 찾아 `SearchHit`으로 돌려준다.
    고객 신고는 신고 코드의 `manual`만 검색한다.

책임:
    - 모든 검색에 설비 필터를 건다. 다른 설비의 같은 이름 에러를 근거로 돌려주지 않는다.
    - 엔지니어 대화는 `manual`과 `history`를 검색한다. 고객 안내는 `manual`만 검색하고 `history`는 쓰지 않는다.
    - `history`의 `case_id` 중복은 색인이 막는다(`history:<case_id>` ID로 upsert). 검색에서 다시 거르지 않는다.
    - 검색 결과가 근거로 충분한지 판단한다(임계값은 사람이 평가 결과를 보고 정한다).
    - 답변을 만들거나 DB에 쓰지 않는다.

의존: `db.vectordb`, `models`만 import한다(의존 규칙 §2.5). `db.rdb`는 부르지 않는다.
호출: `rag.pipeline`.
소유: RAG 파트
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from mpilot.db import vectordb
from mpilot.models import (
    CitationKind,
    DependencyError,
    Equipment,
    SearchHit,
    ValidationError,
)

if TYPE_CHECKING:
    from langchain_core.documents import Document


def search_manual(equipment: Equipment | None, query: str, k: int) -> list[SearchHit]:
    """설비 필터로 매뉴얼 근거를 검색한다.

    Args:
        equipment: 검색 대상 설비. 이 설비의 문서만 찾는다. `None`이면 필터 없이 4종 전체에서 찾는다
            (신고 설비 자료만으로 근거가 부족할 때 pipeline이 쓰는 폴백 검색).
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


def search_history(equipment: Equipment | None, query: str, k: int) -> list[SearchHit]:
    """설비 필터로 확정 이력 근거를 검색한다.

    Args:
        equipment: 검색 대상 설비. 이 설비의 이력만 찾는다. `None`이면 필터 없이 4종 전체에서 찾는다
            (신고 설비 자료만으로 근거가 부족할 때 pipeline이 쓰는 폴백 검색).
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
    kind: CitationKind, equipment: Equipment | None, query: str, k: int
) -> list[SearchHit]:
    """설비 필터를 건 유사도 검색의 공통부. `search_manual`과 `search_history`가 쓴다.

    Args:
        kind: 검색할 컬렉션. `CitationKind.MANUAL` 또는 `CitationKind.HISTORY`.
        equipment: 검색 대상 설비. 메타데이터 `equipment`로 걸러 이 설비의 문서만 찾는다.
            `None`이면 거르지 않는다.
        query: 검색 질의 문장. 공백만 있으면 안 된다.
        k: 가져올 상위 건수. 1 이상이어야 한다.

    Returns:
        list[SearchHit]: 필터에 맞는 문서(필터가 없으면 전체)를 `distance` 오름차순으로 정렬한 근거. 없으면 빈 목록이다.

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
            query, k=k, filter={"equipment": equipment.value} if equipment else None
        )
        scored = sorted(scored, key=lambda pair: pair[1])  # 거리가 None이면 여기서 TypeError
    except Exception as e:
        raise DependencyError(f"{kind.value} 검색에 실패했다") from e
    return [_to_hit(doc, distance) for doc, distance in scored]


def _to_hit(doc: Document, distance: float | None) -> SearchHit:
    """저장소 문서 한 건을 `SearchHit`으로 바꾼다.

    문서 ID(`Document.id`)의 접두(`history:`)로 컬렉션 종류를 가리고, 메타데이터에서 설비·코드·출처 표기를 읽는다.
    출처 표기(`locator`)는 매뉴얼이 `<매뉴얼 ID> <쪽>쪽 <절>`, 이력이 `접수 <case_id> <코드>`다.

    Args:
        doc: 저장소가 돌려준 문서. `id`와 메타데이터 `equipment`를 읽고, 매뉴얼은 `manual_id`·`page`·`section`,
            이력은 `case_id`·`code`도 읽는다.
        distance: 질의와의 벡터 거리. 직접 조회한 근거는 `None`.

    Returns:
        SearchHit: 변환된 근거. `code`는 코드 표 행·이력과 에러코드 절(`13.n <코드> ...`)의 매뉴얼 청크에 있고,
            그 밖의 매뉴얼 본문은 `None`이다.

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
    보류 정확도·정상 답변율 평가 결과를 보고 정한다. 아래는 바뀔 수 있는 초기안이다(#49에서 확정).

    - 경계: `distance <= threshold`인 근거가 하나라도 있으면 충분하다. 같으면 충분하다.
    - `distance`가 `None`인 근거는 비교에서 제외한다.
    - `hits`가 비었거나 비교 가능한 근거가 모두 `threshold`보다 멀면 부족하다.
    - `threshold`는 유한한 0 이상의 실수여야 한다. 음수·NaN·무한대는 거부한다.
    - 거리 척도(L2·cosine)에 의존하지 않고 값의 대소만 비교한다.
    - 엔지니어 답변과 고객 안내가 같은 판정과 같은 임계값을 쓴다.

    Args:
        hits: 검색으로 얻은 근거 목록.
        threshold: 근거 충분성 임계값(`SearchSettings.evidence_threshold`).

    Returns:
        bool: 근거로 충분하면 `True`, 부족하면 `False`(근거 부족 판정의 입력이 된다).

    Raises:
        ValidationError: `threshold`가 허용 범위를 벗어났을 때.
    """
    if not math.isfinite(threshold) or threshold < 0:
        raise ValidationError(f"threshold는 유한한 0 이상이어야 합니다: {threshold}")
    return any(hit.distance is not None and hit.distance <= threshold for hit in hits)
