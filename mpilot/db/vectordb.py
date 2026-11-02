"""검색 자료 저장소 관리(Chroma, 메모리): 색인 생성·이력 반영·원문 조회.

역할:
    `manual`, `history` 두 컬렉션을 만들고 관리한다. 검색 근거와 인용 원문은 모두 여기서 나온다.

컬렉션과 문서 ID(spec §3.4):
    - `manual`: 코드 표 행 `error:<equipment>:<code>`, 매뉴얼 본문 `manual:<manual_id>:<page>:<chunk_no>`.
    - `history`: 확정 현장 조치 `history:<case_id>`.

책임:
    - 앱을 시작할 때마다 컬렉션을 삭제하고 DB·PDF 원자료에서 새로 만든다. 원자 교체는 없고, 실패하면 시작을 중단한다.
    - `history`에는 `resolutions` 행이 있는 건(시드 포함)만 넣고 `case_id`로 중복을 제거한다.
      같은 `case_id`는 upsert다. 자동 셀프 가이드와 대화는 넣지 않는다.
    - 모든 검색은 설비 필터가 필수다. 이 모듈은 그 필터를 쓸 수 있는 저장소를 돌려준다.
    - DB(`rdb`)의 확정 이력이 정본이다. 반영 실패는 건별로 저장하지 않고 앱 재시작의 전체 재생성으로 복구한다.
    - 같은 ID는 같은 원문을 유지한다. 과거 인용 원문은 현재 색인에서 조회하며 스냅샷은 저장하지 않는다.
    - 매뉴얼 본문 청크는 절 제목을 앞에 붙인 글로 임베딩하되 저장하는 원문은 청크 원문 그대로 둔다.
      에러코드 절(`<번호> <코드> <이름>`)의 청크에는 메타데이터 `code`를, 그 밖의 청크(공통 절)에는 `COMMON_CODE`를 단다.
    - 모듈을 import할 때 임베딩 API나 저장소에 접속하지 않는다. 연결은 `connect`가 맡는다.

의존: `models`만 import한다(의존 규칙 §2.5).
호출: `rag.retriever`(`get_store`, `get_document`), `service`(`index_history`, `rebuild_search_index`), `app`(`connect`).
소유: DB 파트
"""

from __future__ import annotations

import re
from datetime import UTC
from typing import TYPE_CHECKING

from langchain_core.documents import Document

from mpilot.models import (
    CaseDetail,
    CaseStatus,
    Chunk,
    CitationKind,
    DependencyError,
    ErrorCode,
    NotFoundError,
    Route,
    ValidationError,
)

if TYPE_CHECKING:
    from langchain_core.embeddings import Embeddings
    from langchain_core.vectorstores import VectorStore

_stores = {}  # connect가 만든 컬렉션별 저장소. 키는 CitationKind(manual, history)


def connect(embeddings: Embeddings) -> None:
    """임베딩을 받아 검색 저장소를 연결한다.

    Args:
        embeddings: 문서와 질의를 벡터로 바꿀 LangChain 임베딩 객체.

    Returns:
        None: 모듈 안에 저장소 연결을 보관한다. 이후 함수는 이 연결을 쓴다.

    Raises:
        DependencyError: 저장소를 초기화할 수 없을 때.
    """
    global _stores
    try:
        # import 순간 sqlite3 버전 검사(3.35 미만이면 RuntimeError)가 있어, 모듈 맨 위가 아니라 여기서 불러온다.
        # 그래야 이 환경에서도 앱(--fake 포함)은 켜지고, 실패는 아래에서 DependencyError로 바뀐다.
        from langchain_chroma import Chroma

        # client·persist_directory를 주지 않으면 메모리 Chroma다(spec §2.1).
        _stores = {kind: Chroma(collection_name=kind.value, embedding_function=embeddings)
                   for kind in CitationKind}
    except Exception as e:
        raise DependencyError("검색 저장소를 초기화할 수 없습니다.") from e


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
    try:
        kind = CitationKind(kind)
    except ValueError:
        raise ValidationError(f"지원하지 않는 저장소 종류입니다: {kind}") from None
    if kind not in _stores:
        raise DependencyError("검색 저장소가 연결되지 않았습니다. vectordb.connect를 먼저 부르세요.")
    return _stores[kind]


def get_document(kind: CitationKind, ref_id: str) -> Document:
    """문서 ID로 원문·메타데이터를 조회한다.

    답변의 인용 원문 확인에 쓴다.

    Args:
        kind: 문서가 있는 컬렉션.
        ref_id: 문서 ID(예: `manual:<manual_id>:<page>:<chunk_no>`).

    Returns:
        Document: 원문(`page_content`)과 메타데이터(설비, 코드, 쪽·절 등)를 담은 LangChain 문서.

    Raises:
        NotFoundError: 그 ID의 문서가 컬렉션에 없을 때.
        DependencyError: 저장소 조회에 실패했을 때.
    """
    from langchain_core.documents import Document

    try:
        store = get_store(kind)
    except ValidationError:  # 계약 예외에 맞춰 모르는 종류는 '그 컬렉션에 없음'으로 본다
        raise NotFoundError(f"검색 문서가 없습니다: {kind}/{ref_id}") from None
    try:
        found = store.get(ids=[ref_id])  # 유사도 검색이 아니라 ID로 정확히 찾는다
    except Exception as e:
        raise DependencyError("검색 문서를 조회하지 못했습니다.") from e
    if not found["ids"]:
        raise NotFoundError(f"검색 문서가 없습니다: {kind}/{ref_id}")
    return Document(id=ref_id, page_content=found["documents"][0],
                    metadata=found["metadatas"][0] or {})


# ---------------------------------------------------------------------------
# 검색 문서 만들기(색인 쓰기 쪽, 담당: 이재서)
#
# 문서 메타데이터 키(retriever가 필터·출처 표기에 쓴다):
#     공통      doc_type("error" | "manual" | "history"), equipment(Equipment 값)
#     error     code, manual_id, page, section
#     manual    code(에러코드 절이면 그 코드, 아니면 COMMON_CODE), manual_id, page, section, chunk_no, version
#     history   code, case_id, completed_at(UTC ISO 8601)
# Chroma 메타데이터에는 None을 넣을 수 없어서, 값이 없는 키는 아예 넣지 않는다.
#
# `get_store`(다른 담당)에 기대는 것 두 가지 — 바꾸면 이 쪽 함수도 함께 고쳐야 한다:
#     1. `langchain_chroma.Chroma` 객체를 돌려준다(재생성에 `reset_collection()`을 쓴다).
#     2. `connect` 때 만든 같은 객체를 매번 돌려준다. 같은 컬렉션을 가리키는 Chroma 객체를 따로
#        만들어 두면, 한쪽에서 컬렉션을 다시 만든 뒤 다른 쪽 객체로는 검색이 실패한다.
#     3. 매뉴얼 본문 청크는 저장 원문과 임베딩할 글이 달라 `Chroma.embeddings`로 벡터를 만들어
#        `_collection.upsert`로 직접 넣는다(`_write_documents`).
# ---------------------------------------------------------------------------

_ID_SEP = ":"  # 문서 ID 구성 요소 사이의 구분자

# 에러코드 절 제목 `<번호> <에러코드> <이름>`(예: `13.1 M401 주행 서보 과전류`)에서 에러코드를 읽는 규칙.
COMMON_CODE = "common"  # 에러코드 절이 아닌 매뉴얼 청크의 메타데이터 `code`(retriever가 필터에 쓴다)
_SECTION_CODE = re.compile(r"^\d+\.\d+ ([A-Z]\d{3}) ")

_UPSERT_BATCH = 1000  # Chroma 한 번 쓰기의 최대 건수(저장소 한도 안쪽)


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
    texts = []  # 문자열로 바꾼 구성 요소
    for part in parts:  # 구성 요소를 하나씩 본다
        texts.append(str(part))  # 쪽 번호·청크 번호는 숫자로 들어오므로 문자열로 바꾼다
    for text in texts:  # 바꾼 구성 요소를 하나씩 검사한다
        if not text or _ID_SEP in text:  # 비었거나 ':'가 있으면 나중에 ID를 ':'로 다시 쪼갤 수 없다
            raise ValidationError(
                f"문서 ID 구성 요소가 비었거나 ':'가 들어 있습니다: {prefix}{_ID_SEP}{texts}"
            )  # 추측하지 않고 적재 오류로 낸다
    return _ID_SEP.join([prefix] + texts)  # 예) "error" + ["SRM", "S101"] → "error:SRM:S101"


def _build_error_document(code: ErrorCode) -> tuple[str, Document]:
    """에러코드 표 한 행을 `manual` 컬렉션 문서로 만든다(ID `error:<equipment>:<code>`).

    Args:
        code: 에러코드 표 한 행.

    Returns:
        tuple[str, Document]: 문서 ID와, 코드·원인·조치·출처를 담은 문서.

    Raises:
        ValidationError: ID 구성 요소가 비었거나 `:`가 들어 있을 때.
    """
    doc_id = _make_doc_id("error", code.equipment.value, code.code)  # 예) error:SRM:S101
    text = "\n".join(  # 표 한 행을 "항목: 값" 줄로 풀어 본문을 만든다
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
    )  # 본문 끝
    metadata = {  # 설비 필터와 출처 표기에 쓰는 메타데이터
        "doc_type": "error",  # 문서 종류: 코드 표 행
        "equipment": code.equipment.value,  # 설비 필터에 쓴다
        "code": code.code,
        "manual_id": code.manual_id,
        "page": code.page,
        "section": code.section,
    }  # 메타데이터 끝
    return doc_id, Document(page_content=text, metadata=metadata, id=doc_id)  # ID와 문서를 함께 돌려준다


def _build_manual_document(chunk: Chunk) -> tuple[str, Document]:
    """매뉴얼 청크 하나를 `manual` 컬렉션 문서로 만든다(ID `manual:<manual_id>:<page>:<chunk_no>`).

    본문은 청크 원문 그대로다. 인용 원문 확인 때 같은 글자를 보여 줘야 하므로 바꾸지 않는다(임베딩할 글은
    `_embedding_text`가 따로 만든다). 절 제목이 `<번호> <에러코드> <이름>`이면 메타데이터 `code`에 에러코드를 단다.

    Args:
        chunk: `parser.parse_manuals`가 만든 매뉴얼 청크.

    Returns:
        tuple[str, Document]: 문서 ID와 청크 원문 문서.

    Raises:
        ValidationError: ID 구성 요소가 비었거나 `:`가 들어 있을 때.
    """
    doc_id = _make_doc_id("manual", chunk.manual_id, chunk.page, chunk.chunk_no)  # 예) manual:HMX-SRM-TSM-001:12:3
    metadata = {  # 설비 필터와 쪽·절 출처 표기에 쓰는 메타데이터
        "doc_type": "manual",  # 문서 종류: 매뉴얼 본문
        "equipment": chunk.equipment.value,  # 설비 필터에 쓴다
        "manual_id": chunk.manual_id,
        "page": chunk.page,
        "section": chunk.section,
        "chunk_no": chunk.chunk_no,
        "version": chunk.version,
    }
    section_code = _SECTION_CODE.match(chunk.section)
    # Chroma 필터에는 "키 없음" 조건이 없어, 공통 절도 값을 달아야 `code $in [신고 코드, COMMON_CODE]`로 고를 수 있다.
    metadata["code"] = section_code[1] if section_code else COMMON_CODE
    return doc_id, Document(page_content=chunk.text, metadata=metadata, id=doc_id)


def _build_history_document(case: CaseDetail) -> tuple[str, Document]:
    """확정 현장 조치 한 건을 `history` 컬렉션 문서로 만든다(ID `history:<case_id>`).

    본문은 설비·코드·증상 메모와 확정된 원인·조치·결과다(spec §3.4). 셀프 가이드·대화는 넣지 않는다.

    Args:
        case: 조치 기록이 채워진 신고 건.

    Returns:
        tuple[str, Document]: 문서 ID와 이력 문서.

    Raises:
        ValidationError: 조치 기록(`resolution`)이 없거나, 셀프 가이드 건이거나, 완료되지 않은 건일 때.
    """
    sc = case.service_case  # 신고 건(자주 쓰여서 짧은 이름으로 둔다)
    if case.resolution is None:  # 조치 기록이 없는 건이면
        raise ValidationError(f"{sc.case_id}: 조치 기록이 없는 건은 이력 색인에 넣지 않습니다.")  # 근거로 쓸 수 없어 거부한다
    not_confirmed = f"{sc.case_id}: 확정된 현장 조치 건만 이력 색인에 넣습니다."  # 아래 세 검사가 함께 쓰는 오류 문구
    if sc.route != Route.SUPPORT:  # 셀프 가이드 건이면
        raise ValidationError(not_confirmed)  # 자동 안내는 검색 근거로 다시 쓰지 않는다
    if sc.status != CaseStatus.COMPLETED:  # 아직 완료되지 않은 건이면
        raise ValidationError(not_confirmed)  # 확정 전이라 넣지 않는다
    if sc.completed_at is None:  # 완료 시각이 비어 있으면
        raise ValidationError(not_confirmed)  # 메타데이터를 만들 수 없어 넣지 않는다
    doc_id = _make_doc_id("history", sc.case_id)  # 예) history:SRM-H-0001
    text = "\n".join(  # 증상 메모와 확정된 원인·조치·결과로 본문을 만든다
        [
            f"설비: {sc.equipment.value}",
            f"에러코드: {sc.code} ({case.error_code.name})",
            f"증상 메모: {sc.memo or '없음'}",
            f"원인: {case.resolution.cause}",
            f"조치: {case.resolution.action}",
            f"결과: {case.resolution.result}",
        ]
    )  # 본문 끝
    completed_at = sc.completed_at.astimezone(UTC).replace(microsecond=0).isoformat()  # UTC 초 단위 문자열. 예) 2026-07-07T14:29:00+00:00
    metadata = {  # 설비 필터와 출처 표기에 쓰는 메타데이터
        "doc_type": "history",  # 문서 종류: 확정 현장 조치
        "equipment": sc.equipment.value,  # 설비 필터에 쓴다
        "code": sc.code,
        "case_id": sc.case_id,
        "completed_at": completed_at,
    }  # 메타데이터 끝
    return doc_id, Document(page_content=text, metadata=metadata, id=doc_id)  # ID와 문서를 함께 돌려준다


def _embedding_text(doc: Document) -> str:
    """문서를 벡터로 바꿀 글을 만든다.

    매뉴얼 본문 청크는 절 제목을 앞에 붙인다. 코드 이름이 본문에 없는 연속 청크도 어느 코드의 내용인지 벡터에
    담기게 해서, 같은 질문에 다른 코드의 비슷한 청크가 섞여 나오는 일을 줄이려는 것이다. 저장하는 원문
    (`page_content`)은 바꾸지 않는다. 코드 표 행과 이력은 원문 그대로 임베딩한다.

    Args:
        doc: 색인할 문서.

    Returns:
        str: 임베딩에 쓸 글. 매뉴얼 본문은 `<절>\\n<원문>`, 그 밖은 원문이다.
    """
    if doc.metadata.get("doc_type") == "manual":
        return f"{doc.metadata['section']}\n{doc.page_content}"
    return doc.page_content


def _write_documents(store: VectorStore, docs: dict[str, Document], what: str) -> None:
    """문서를 저장소에 넣는다(같은 ID는 덮어쓴다). 비어 있으면 아무것도 하지 않는다.

    임베딩할 글(`_embedding_text`)이 저장 원문과 다른 문서가 있으면 벡터를 직접 만들어 넣고, 모두 같으면
    저장소의 `add_documents`를 쓴다. 어느 쪽이든 저장되는 원문은 `page_content` 그대로다.

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
    ids = list(docs.keys())
    documents = list(docs.values())
    texts = [_embedding_text(doc) for doc in documents]
    try:
        if texts == [doc.page_content for doc in documents]:
            store.add_documents(documents, ids=ids)
        else:
            vectors = store.embeddings.embed_documents(texts)
            for start in range(0, len(ids), _UPSERT_BATCH):
                batch = slice(start, start + _UPSERT_BATCH)
                store._collection.upsert(
                    ids=ids[batch],
                    embeddings=vectors[batch],
                    documents=[doc.page_content for doc in documents[batch]],
                    metadatas=[doc.metadata for doc in documents[batch]],
                )
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
    doc_id, doc = _build_history_document(case)  # 확정 건인지 검사하고 문서를 만든다
    _write_documents(get_store(CitationKind.HISTORY), {doc_id: doc}, "이력")  # history 컬렉션에 넣는다. 같은 case_id면 덮어쓴다


def rebuild_search_index(
    chunks: list[Chunk], codes: list[ErrorCode], histories: list[CaseDetail]
) -> None:
    """검색 색인을 새로 만든다.

    두 컬렉션을 삭제하고 다시 만든다. `manual`에는 코드 표 행(`error:...`)과 매뉴얼 본문(`manual:...`),
    `history`에는 확정 이력(`history:...`)을 넣는다.

    Args:
        chunks: `parser.parse_manuals`가 만든 매뉴얼 청크.
        codes: 설비 4종의 에러코드 표 행(`rdb.fetch_error_codes` 결과를 합친 것).
        histories: 조치 기록이 있는 신고 목록(`rdb.fetch_resolved_cases` 결과).

    Returns:
        None: 두 컬렉션이 원자료에서 새로 채워진다.

    Raises:
        ValidationError: 문서 ID 구성 요소에 `:`가 있거나 문서 ID가 겹칠 때.
        DependencyError: 임베딩 생성이나 저장소 쓰기에 실패했을 때. 이 경우 앱 시작을 중단한다.

    구현 메모: 컬렉션을 비우는 데
    `get_store`가 돌려준 Chroma 객체의 `reset_collection()`을 쓴다(위 '기대는 것' 주석).
    """
    # 1) 문서를 모두 먼저 만들고 검증한다. 검증에 실패하면 기존 색인을 지우지 않고 멈춘다.
    built = []  # (문서 ID, 문서) 목록. 에러코드 표 행 → 매뉴얼 본문 순서
    for error_code in codes:  # 에러코드 표 행마다
        built.append(_build_error_document(error_code))  # error: 문서를 만든다
    for chunk in chunks:  # 매뉴얼 청크마다
        built.append(_build_manual_document(chunk))  # manual: 문서를 만든다
    manual_docs: dict[str, Document] = {}  # 문서 ID → 문서(manual 컬렉션에 넣을 것)
    for doc_id, doc in built:  # 만든 문서를 하나씩 모은다
        if doc_id in manual_docs:  # 같은 ID가 또 나오면(같은 ID는 같은 원문이어야 한다, spec §3.4)
            raise ValidationError(f"manual 문서 ID가 겹칩니다: {doc_id}")  # 파싱·코드 표 오류라 멈춘다
        manual_docs[doc_id] = doc  # 처음 나온 ID면 모은다

    history_docs: dict[str, Document] = {}  # 문서 ID → 이력 문서(history 컬렉션에 넣을 것)
    for case in histories:  # 조치 기록이 있는 신고마다
        doc_id, doc = _build_history_document(case)  # 이력 문서를 만든다
        history_docs[doc_id] = doc  # 같은 case_id면 나중 것이 남는다(DB 정본과 같은 내용)

    # 2) 두 컬렉션을 비우고(삭제 후 다시 만들기) 새로 채운다. 원자 교체는 없다.
    manual_store = get_store(CitationKind.MANUAL)  # manual 저장소
    history_store = get_store(CitationKind.HISTORY)  # history 저장소
    try:  # 컬렉션 비우기 오류만 따로 잡는다
        manual_store.reset_collection()  # manual 컬렉션을 지우고 빈 것으로 다시 만든다
        history_store.reset_collection()  # history 컬렉션도 같다
    except Exception as e:  # Chroma 오류는 종류가 많아 한곳에서 잡는다
        raise DependencyError(f"검색 색인을 비우지 못했습니다({type(e).__name__}).") from None  # 의존 오류로 바꿔 앱 시작을 멈춘다
    _write_documents(manual_store, manual_docs, "매뉴얼")  # 코드 표 행과 매뉴얼 본문을 넣는다
    _write_documents(history_store, history_docs, "이력")  # 확정 이력을 넣는다
