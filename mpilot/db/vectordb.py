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

from typing import TYPE_CHECKING

from mpilot.models import Chunk, CaseDetail, CitationKind, ErrorCode

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
    ...


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
        DependencyError: 임베딩 생성이나 저장소 쓰기에 실패했을 때. 이 경우 앱 시작을 중단한다.
    """
    ...
