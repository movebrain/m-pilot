"""근거 검색.

역할:
    질문에 맞는 근거를 `manual`·`history` 컬렉션에서 찾아 `SearchHit`으로 돌려준다.
    고객 신고는 신고 코드의 `manual`만 검색한다.

책임:
    - 모든 검색에 설비 필터를 건다. 다른 설비의 같은 이름 에러를 근거로 돌려주지 않는다.
    - 엔지니어 대화는 `manual`과 `history`를 검색한다. 고객 안내는 `manual`만 검색하고 `history`는 쓰지 않는다.
    - `history`의 `case_id` 중복은 색인이 막는다(`history:<case_id>` ID로 upsert). 검색에서 다시 거르지 않는다.
    - 근거가 충분한지는 판단하지 않는다. 판정은 모델이 하며(`rag.pipeline`), 거리는 `SearchHit.distance`로만 남긴다.
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
    SearchHit,
    ValidationError,
)

if TYPE_CHECKING:
    from langchain_core.documents import Document


def search(kind: CitationKind, equipment: Equipment | None, query: str, k: int) -> list[SearchHit]:
    """설비 필터를 건 유사도 검색으로 근거를 찾는다.

    Args:
        kind: 검색할 컬렉션. `CitationKind.MANUAL`(코드 표 행 `error:...`과 매뉴얼 본문 `manual:...`) 또는
            `CitationKind.HISTORY`(확정 이력).
        equipment: 검색 대상 설비. 메타데이터 `equipment`로 걸러 이 설비의 문서만 찾는다.
            `None`이면 거르지 않고 4종 전체에서 찾는다(신고 설비 자료로 답하지 못할 때 pipeline이 쓰는 폴백 검색).
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
        distance: 질의와의 벡터 거리. 거리가 없으면 `None`.

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
