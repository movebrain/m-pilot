"""검색 자료 저장소 관리(Chroma, 메모리): 색인 생성·이력 반영·원문 조회.

역할:
    `manual`, `history` 두 컬렉션을 만들고 관리한다. 검색 근거와 인용 원문은 모두 여기서 나온다.

컬렉션과 문서 ID(spec §3.4):
    - `manual`: 코드 표 행 `error:<equipment>:<code>`, 매뉴얼 본문 `manual:<manual_id>:<page>:<chunk_no>`.
    - `history`: 확정 현장 조치 `history:<case_id>`.

책임:
    - 앱을 시작할 때마다 컬렉션을 삭제하고 DB·PDF 원자료에서 새로 만든다. 원자 교체는 없고, 실패하면 시작을 중단한다.
    - 색인을 만들 때 모든 `customer_procedure_ref_ids`가 `manual`에 실제로 있는지 검증하고 없으면 실패한다.
    - `history`에는 `resolutions` 행이 있는 건(시드 포함)만 넣고 `case_id`로 중복을 제거한다.
      같은 `case_id`는 upsert다. 자동 셀프 가이드와 대화는 넣지 않는다.
    - 모든 검색은 설비 필터가 필수다. 이 모듈은 그 필터를 쓸 수 있는 저장소를 돌려준다.
    - DB(`rdb`)의 확정 이력이 정본이다. 반영 실패는 건별로 저장하지 않고 앱 재시작의 전체 재생성으로 복구한다.
    - 같은 ID는 같은 원문을 유지한다. 과거 인용 원문은 현재 색인에서 조회하며 스냅샷은 저장하지 않는다.
    - 모듈을 import할 때 임베딩 API나 저장소에 접속하지 않는다. 연결은 `connect`가 맡는다.

의존: `models`만 import한다(의존 규칙 §2.5).
호출: `rag.retriever`(`get_store`, `get_document`), `service`(`index_history`, `rebuild_search_index`), `app`(`connect`).
소유: DB 파트
"""

from __future__ import annotations

from datetime import UTC
from typing import TYPE_CHECKING

from mpilot.models import (
    CaseDetail,
    CaseStatus,
    Chunk,
    CitationKind,
    DependencyError,
    ErrorCode,
    MpilotError,
    Route,
    ValidationError,
)

if TYPE_CHECKING:
    from langchain_core.documents import Document
    from langchain_core.embeddings import Embeddings
    from langchain_core.vectorstores import VectorStore


def connect(embeddings: Embeddings) -> None:
    """임베딩을 받아 검색 저장소를 연결한다.

    Args:
        embeddings: 문서와 질의를 벡터로 바꿀 LangChain 임베딩 객체.

    Returns:
        None: 모듈 안에 저장소 연결을 보관한다. 이후 함수는 이 연결을 쓴다.

    Raises:
        DependencyError: 저장소를 초기화할 수 없을 때.
    """
    ...


def get_store(kind: CitationKind) -> VectorStore:
    """`manual` 또는 `history` 저장소를 돌려준다.

    Args:
        kind: 가져올 컬렉션. `CitationKind.MANUAL` 또는 `CitationKind.HISTORY`.

    Returns:
        VectorStore: 그 컬렉션을 가리키는 LangChain 벡터 저장소. 검색할 때는 설비 필터를 반드시 붙인다.

    Raises:
        ValidationError: 지원하지 않는 종류일 때.
        DependencyError: 저장소에 접근할 수 없거나 `connect` 전에 불렀을 때.
    """
    ...


def get_document(kind: CitationKind, ref_id: str) -> Document:
    """문서 ID로 원문·메타데이터를 조회한다.

    답변의 인용 원문 확인과, 고객 안내용 허용 절차 직접 조회에 쓴다.

    Args:
        kind: 문서가 있는 컬렉션.
        ref_id: 문서 ID(예: `manual:<manual_id>:<page>:<chunk_no>`).

    Returns:
        Document: 원문(`page_content`)과 메타데이터(설비, 코드, 쪽·절 등)를 담은 LangChain 문서.

    Raises:
        NotFoundError: 그 ID의 문서가 컬렉션에 없을 때.
        DependencyError: 저장소 조회에 실패했을 때.
    """
    ...


# ---------------------------------------------------------------------------
# 검색 문서 만들기(색인 쓰기 쪽, 담당: 이재서)
#
# 문서 메타데이터 키(retriever가 필터·출처 표기에 쓴다):
#     공통      doc_type("error" | "manual" | "history"), equipment(Equipment 값)
#     error     code, manual_id, page, section
#     manual    manual_id, page, section, chunk_no, version
#     history   code, case_id, completed_at(UTC ISO 8601)
# Chroma 메타데이터에는 None을 넣을 수 없어서, 값이 없는 키는 아예 넣지 않는다.
#
# `get_store`(다른 담당)에 기대는 것 두 가지 — 바꾸면 이 쪽 함수도 함께 고쳐야 한다:
#     1. `langchain_chroma.Chroma` 객체를 돌려준다(재생성에 `reset_collection()`을 쓴다).
#     2. `connect` 때 만든 같은 객체를 매번 돌려준다. 같은 컬렉션을 가리키는 Chroma 객체를 따로
#        만들어 두면, 한쪽에서 컬렉션을 다시 만든 뒤 다른 쪽 객체로는 검색이 실패한다.
# ---------------------------------------------------------------------------

_ID_SEP = ":"


def _make_doc_id(prefix: str, *parts: object) -> str:
    """문서 ID(`<prefix>:<구성1>:<구성2>...`)를 만든다.

    Args:
        prefix: `error`, `manual`, `history` 중 하나.
        *parts: ID 구성 요소. 문자열로 바꿔 이어 붙인다.

    Returns:
        str: 예) `error:SRM:S101`, `manual:srm_spec:12:3`, `history:SRM-H-0001`.

    Raises:
        ValidationError: 구성 요소가 비었거나 `:`가 들어 있을 때(ID를 다시 쪼갤 수 없게 된다).
    """
    texts = [str(p) for p in parts]
    for text in texts:
        if not text or _ID_SEP in text:
            raise ValidationError(
                f"문서 ID 구성 요소가 비었거나 ':'가 들어 있습니다: {prefix}{_ID_SEP}{texts}"
            )
    return _ID_SEP.join([prefix, *texts])


def _error_document(code: ErrorCode) -> tuple[str, Document]:
    """에러코드 표 한 행을 `manual` 컬렉션 문서로 만든다(ID `error:<equipment>:<code>`).

    Args:
        code: 에러코드 표 한 행.

    Returns:
        tuple[str, Document]: 문서 ID와, 코드·원인·조치·출처를 담은 문서.

    Raises:
        ValidationError: ID 구성 요소가 비었거나 `:`가 들어 있을 때.
    """
    from langchain_core.documents import Document

    doc_id = _make_doc_id("error", code.equipment.value, code.code)
    text = "\n".join(
        [
            f"설비: {code.equipment.value}",
            f"에러코드: {code.code}",
            f"에러명: {code.name}",
            f"심각도: {code.severity.value}",
            f"조치 구분: {code.action_owner.value}",
            f"원인: {code.cause}",
            f"조치: {code.action}",
            f"출처: {code.manual_id} p.{code.page} {code.section}",
        ]
    )
    metadata = {
        "doc_type": "error",
        "equipment": code.equipment.value,
        "code": code.code,
        "manual_id": code.manual_id,
        "page": code.page,
        "section": code.section,
    }
    return doc_id, Document(page_content=text, metadata=metadata, id=doc_id)


def _manual_document(chunk: Chunk) -> tuple[str, Document]:
    """매뉴얼 청크 하나를 `manual` 컬렉션 문서로 만든다(ID `manual:<manual_id>:<page>:<chunk_no>`).

    본문은 청크 원문 그대로다. 인용 원문 확인 때 같은 글자를 보여 줘야 하므로 바꾸지 않는다.

    Args:
        chunk: `parser.parse_manuals`가 만든 매뉴얼 청크.

    Returns:
        tuple[str, Document]: 문서 ID와 청크 원문 문서.

    Raises:
        ValidationError: ID 구성 요소가 비었거나 `:`가 들어 있을 때.
    """
    from langchain_core.documents import Document

    doc_id = _make_doc_id("manual", chunk.manual_id, chunk.page, chunk.chunk_no)
    metadata = {
        "doc_type": "manual",
        "equipment": chunk.equipment.value,
        "manual_id": chunk.manual_id,
        "page": chunk.page,
        "section": chunk.section,
        "chunk_no": chunk.chunk_no,
        "version": chunk.version,
    }
    return doc_id, Document(page_content=chunk.text, metadata=metadata, id=doc_id)


def _history_document(case: CaseDetail) -> tuple[str, Document]:
    """확정 현장 조치 한 건을 `history` 컬렉션 문서로 만든다(ID `history:<case_id>`).

    본문은 설비·코드·증상 메모와 확정된 원인·조치·결과다(spec §3.4). 셀프 가이드·대화는 넣지 않는다.

    Args:
        case: 조치 기록이 채워진 신고 건.

    Returns:
        tuple[str, Document]: 문서 ID와 이력 문서.

    Raises:
        ValidationError: 조치 기록(`resolution`)이 없거나, 셀프 가이드 건이거나, 완료되지 않은 건일 때.
    """
    from langchain_core.documents import Document

    sc = case.service_case
    if case.resolution is None:
        raise ValidationError(f"{sc.case_id}: 조치 기록이 없는 건은 이력 색인에 넣지 않습니다.")
    if sc.route != Route.SUPPORT or sc.status != CaseStatus.COMPLETED or sc.completed_at is None:
        raise ValidationError(f"{sc.case_id}: 확정된 현장 조치 건만 이력 색인에 넣습니다.")
    doc_id = _make_doc_id("history", sc.case_id)
    text = "\n".join(
        [
            f"설비: {sc.equipment.value}",
            f"에러코드: {sc.code} ({case.error_code.name})",
            f"증상 메모: {sc.memo or '없음'}",
            f"원인: {case.resolution.cause}",
            f"조치: {case.resolution.action}",
            f"결과: {case.resolution.result}",
        ]
    )
    completed_at = sc.completed_at.astimezone(UTC).replace(microsecond=0).isoformat()
    metadata = {
        "doc_type": "history",
        "equipment": sc.equipment.value,
        "code": sc.code,
        "case_id": sc.case_id,
        "completed_at": completed_at,
    }
    return doc_id, Document(page_content=text, metadata=metadata, id=doc_id)


def _write_documents(store: VectorStore, docs: dict[str, Document], what: str) -> None:
    """문서를 저장소에 넣는다(같은 ID는 덮어쓴다). 비어 있으면 아무것도 하지 않는다.

    Args:
        store: 쓸 벡터 저장소(`get_store`가 돌려준 것).
        docs: 문서 ID → 문서.
        what: 오류 문구에 쓸 자료 이름(예: "매뉴얼", "이력").

    Returns:
        None: 문서가 저장소에 들어간다.

    Raises:
        DependencyError: 임베딩 생성이나 저장소 쓰기에 실패했을 때.
    """
    if not docs:
        return
    try:
        store.add_documents(list(docs.values()), ids=list(docs.keys()))
    except MpilotError:
        raise
    except Exception as e:  # OpenAI·Chroma 오류 종류가 많아 한곳에서 의존 오류로 바꾼다.
        raise DependencyError(f"{what} 색인에 쓰지 못했습니다({type(e).__name__}).") from None


def index_history(case: CaseDetail) -> None:
    """확정 현장 조치 한 건을 이력 색인에 반영한다.

    문서 본문은 설비·코드·증상 메모와 확정된 원인·조치·결과다. 같은 `case_id`는 덮어쓴다(upsert).

    Args:
        case: 조치 기록(`resolution`)이 채워진 신고 건. 셀프 가이드 건은 넣을 수 없다.

    Returns:
        None: `history:<case_id>` 문서가 `history` 컬렉션에 들어간다.

    Raises:
        ValidationError: `resolution`이 없거나 셀프 가이드 건일 때.
        DependencyError: 임베딩 생성이나 저장소 쓰기에 실패했을 때.
    """
    doc_id, doc = _history_document(case)
    _write_documents(get_store(CitationKind.HISTORY), {doc_id: doc}, "이력")


def rebuild_search_index(
    chunks: list[Chunk], codes: list[ErrorCode], histories: list[CaseDetail]
) -> None:
    """검색 색인을 새로 만든다.

    두 컬렉션을 삭제하고 다시 만든다. `manual`에는 코드 표 행(`error:...`)과 매뉴얼 본문(`manual:...`),
    `history`에는 확정 이력(`history:...`)을 넣는다. 모든 `customer_procedure_ref_ids`가 `manual`에 있어야 한다.

    Args:
        chunks: `parser.parse_manuals`가 만든 매뉴얼 청크.
        codes: 설비 4종의 에러코드 표 행(`rdb.fetch_error_codes` 결과를 합친 것).
        histories: 조치 기록이 있는 신고 목록(`rdb.fetch_resolved_cases` 결과).

    Returns:
        None: 두 컬렉션이 원자료에서 새로 채워진다.

    Raises:
        ValidationError: 문서 ID 구성 요소에 `:`가 있거나, 허용 절차 ID가 `manual`에 없을 때.
            허용 절차 ID가 매뉴얼 본문(`manual:`) 문서가 아니거나 다른 설비 문서일 때, 문서 ID가 겹칠 때도 같다.
        DependencyError: 임베딩 생성이나 저장소 쓰기에 실패했을 때. 이 경우 앱 시작을 중단한다.

    구현 메모: 허용 절차 ID는 같은 설비의 매뉴얼 본문(`manual:`) 문서만 인정한다. 컬렉션을 비우는 데
    `get_store`가 돌려준 Chroma 객체의 `reset_collection()`을 쓴다(위 '기대는 것' 주석).
    """
    # 1) 문서를 모두 먼저 만들고 검증한다. 검증에 실패하면 기존 색인을 지우지 않고 멈춘다.
    manual_docs: dict[str, Document] = {}
    for doc_id, doc in [_error_document(c) for c in codes] + [_manual_document(c) for c in chunks]:
        if doc_id in manual_docs:
            # 같은 ID는 같은 원문이어야 한다(spec §3.4). 겹치면 파싱·코드 표 오류다.
            raise ValidationError(f"manual 문서 ID가 겹칩니다: {doc_id}")
        manual_docs[doc_id] = doc

    # 허용 절차 ID는 매뉴얼 본문(`manual:`) 문서여야 하고, 그 에러코드와 같은 설비의 문서여야 한다
    # (spec §3.3 customer_procedure_ref_ids, §3.4 색인 검증). 코드 표 행(`error:`)은 절차가 아니다.
    problems = []
    for c in codes:
        for ref in c.customer_procedure_ref_ids:
            doc = manual_docs.get(ref)
            if doc is None or doc.metadata["doc_type"] != "manual":
                problems.append(f"{c.equipment.value}:{c.code} → {ref} (매뉴얼 본문 문서 없음)")
            elif doc.metadata["equipment"] != c.equipment.value:
                problems.append(f"{c.equipment.value}:{c.code} → {ref} (다른 설비 문서)")
    if problems:
        raise ValidationError("허용 절차 ID가 올바르지 않습니다: " + "; ".join(sorted(problems)))

    # history는 case_id로 중복을 없앤다. 같은 case_id면 나중 것이 남는다(DB 정본과 같은 내용).
    history_docs: dict[str, Document] = dict(_history_document(c) for c in histories)

    # 2) 두 컬렉션을 비우고(삭제 후 다시 만들기) 새로 채운다. 원자 교체는 없다.
    manual_store = get_store(CitationKind.MANUAL)
    history_store = get_store(CitationKind.HISTORY)
    try:
        manual_store.reset_collection()
        history_store.reset_collection()
    except MpilotError:
        raise
    except Exception as e:
        raise DependencyError(f"검색 색인을 비우지 못했습니다({type(e).__name__}).") from None
    _write_documents(manual_store, manual_docs, "매뉴얼")
    _write_documents(history_store, history_docs, "이력")
