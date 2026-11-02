"""근거 검색.

역할:
    질문에 맞는 근거를 `manual`·`history` 컬렉션에서 찾아 `SearchHit`으로 돌려준다.
    고객 신고는 신고 코드의 `manual`만 검색한다.

책임:
    - 검색에 설비 필터를 건다. 다른 설비의 같은 이름 에러를 근거로 돌려주지 않는다. 필터 없는 검색(`equipment=None`)은
      신고 설비 자료로 답하지 못할 때의 폴백뿐이다.
    - 신고 코드를 주면 다른 에러코드의 문서를 뺀다.
    - 엔지니어 대화는 `manual`과 `history`를 검색한다. 고객 안내는 `manual`만 검색하고 `history`는 쓰지 않는다.
    - `history`의 `case_id` 중복은 색인이 막는다(`history:<case_id>` ID로 upsert). 검색에서 다시 거르지 않는다.
    - 근거가 충분한지는 판단하지 않는다. 판정은 모델이 하며(`rag.pipeline`), 거리는 `SearchHit.distance`로만 남긴다.
    - 답변을 만들거나 DB에 쓰지 않는다.

의존: `db.vectordb`, `models`만 import한다(의존 규칙 §2.5). `db.rdb`는 부르지 않는다.
호출: `rag.pipeline`.
소유: RAG 파트
"""

from __future__ import annotations

import re
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

# 에러코드 절은 모두 `발생 조건 > 고객사 … 절차 > 엔지니어 조치 절차 > 복구 확인 > …` 순서다. 고객 근거는 이 제목 앞까지다.
_ENGINEER_HEADING = re.compile(r"(?m)^\d+\. 엔지니어 조치 절차\s*$")
# 표는 파서가 쪽 본문 뒤에 따로 청크로 붙여 제목 뒤에 놓이지만, 원인 후보 표는 고객 육안 확인을 담아 남긴다.
_CAUSE_TABLE_PREFIX = "| 원인 후보 |"


def search(
    kind: CitationKind, equipment: Equipment | None, query: str, k: int, code: str | None = None
) -> list[SearchHit]:
    """설비 필터를 건 유사도 검색으로 근거를 찾는다.

    `code`를 주면 다른 에러코드의 문서(`SearchHit.code`가 `code`와 다른 매뉴얼 청크·코드 표 행)를 뺀다. 에러코드
    절마다 구성(원인 후보 표, 안전 확보 절차 등)이 비슷해서 같은 질문에 다른 코드의 청크가 상위에 섞이기 때문이다.
    검색 전에 메타데이터 `code`가 신고 코드이거나 `vectordb.COMMON_CODE`(공통 절)인 문서로 거르므로, 항상 맞는
    문서 중 상위 `k`건을 찾는다. 공통 절 청크는 둔다.

    Args:
        kind: 검색할 컬렉션. `CitationKind.MANUAL`(코드 표 행 `error:...`과 매뉴얼 본문 `manual:...`) 또는
            `CitationKind.HISTORY`(확정 이력).
        equipment: 검색 대상 설비. 메타데이터 `equipment`로 걸러 이 설비의 문서만 찾는다.
            `None`이면 거르지 않고 4종 전체에서 찾는다(신고 설비 자료로 답하지 못할 때 pipeline이 쓰는 폴백 검색).
        query: 검색 질의 문장. 공백만 있으면 안 된다.
        k: 가져올 상위 건수. 1 이상이어야 한다.
        code: 신고한 에러코드. 주면 다른 코드의 문서를 뺀다. `None`이면 코드로 거르지 않는다.

    Returns:
        list[SearchHit]: 필터에 맞는 문서(필터가 없으면 전체)를 `distance` 오름차순으로 정렬한 근거. 최대 `k`건이며
            맞는 문서가 모자라면 그만큼만 돌려준다. 없으면 빈 목록이다.

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
    where = {"equipment": equipment.value} if equipment else None
    try:
        if code:
            only = {"code": {"$in": [code, vectordb.COMMON_CODE]}}  # 신고 코드와 공통 절
            where = {"$and": [where, only]} if where else only
        scored = store.similarity_search_with_score(query, k=k, filter=where)
        scored = sorted(scored, key=lambda pair: pair[1])  # 거리가 None이면 여기서 TypeError
    except Exception as e:
        raise DependencyError(f"{kind.value} 검색에 실패했다") from e
    return [_to_hit(doc, distance) for doc, distance in scored]


def fetch_code_documents(equipment: Equipment, code: str) -> list[SearchHit]:
    """신고 코드의 고객용 `manual` 근거(코드 표 행과 그 절의 `엔지니어 조치 절차` 앞까지)를 유사도 검색 없이 가져온다.

    고객 안내는 신고 코드의 고객 절차를 빠짐없이 봐야 하므로 상위 k건이 아니라 절을 통째로 쓴다. 메타데이터
    `equipment`·`code`로 거르므로 임베딩을 부르지 않는다. 엔지니어 절차가 근거에 들어가면 모델이 그것을 인용하므로,
    `N. 엔지니어 조치 절차` 제목이 든 청크는 제목 앞 글만 남기고 그 뒤 청크는 뺀다. 단, 원인 후보 표는 파서가
    쪽 본문 뒤에 붙여 제목 뒤에 놓이므로 남긴다.

    Args:
        equipment: 신고 설비.
        code: 신고한 에러코드.

    Returns:
        list[SearchHit]: 코드 표 행을 먼저, 그다음 엔지니어 제목 앞까지의 매뉴얼 청크를 쪽·청크 번호 순으로 둔
            근거(`distance=None`). 없으면 빈 목록이다.

    Raises:
        DependencyError: 저장소 조회에 실패했거나 문서 메타데이터가 계약과 다를 때. `get_store`가 낸 것은 그대로
            올린다.
    """
    from langchain_core.documents import Document  # 모듈 위쪽은 타입 검사용으로만 가져온다

    store = vectordb.get_store(CitationKind.MANUAL)
    try:
        found = store.get(where={"$and": [{"equipment": equipment.value}, {"code": code}]})
    except Exception as e:
        raise DependencyError("manual 문서를 조회하지 못했다") from e
    docs = [
        Document(id=ref_id, page_content=text, metadata=meta or {})
        for ref_id, text, meta in zip(found["ids"], found["documents"], found["metadatas"])
    ]
    # 원문 순서대로 읽히도록 코드 표 행 다음에 쪽·청크 번호 순으로 둔다.
    docs.sort(key=lambda d: (not d.id.startswith("error:"), d.metadata.get("page", 0), d.metadata.get("chunk_no", 0)))
    kept: list[Document] = []
    cut = False
    for doc in docs:
        if cut:
            if doc.page_content.startswith(_CAUSE_TABLE_PREFIX):
                kept.append(doc)
            continue
        heading = _ENGINEER_HEADING.search(doc.page_content)
        if heading is None:
            kept.append(doc)
            continue
        cut = True
        # shortcut: 모델에 넘기는 글만 자른다. 인용 원문 화면은 청크 전체를 보일 수 있다(파서가 하위 절에서 청크를 나누면 해소).
        before = doc.page_content[: heading.start()].strip()
        if before:
            kept.append(Document(id=doc.id, page_content=before, metadata=doc.metadata))
    return [_to_hit(doc, None) for doc in kept]


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
            code=None if meta.get("code") == vectordb.COMMON_CODE else meta.get("code"),
            text=doc.page_content,
            locator=locator,
            distance=distance,
        )
    except (AttributeError, KeyError, ValueError) as e:
        raise DependencyError(f"검색 문서의 메타데이터가 계약과 다르다: {doc.id}") from e
