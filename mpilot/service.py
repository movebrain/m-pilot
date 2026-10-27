"""화면과 DB·RAG를 잇는 유즈케이스(접수·질문·완료 등).

역할:
    UI가 부르는 업무 흐름을 한 곳에 모은다. 화면은 이 모듈의 함수만 부르고, 이 모듈이
    `db`(parser·rdb·vectordb)와 `rag.pipeline`을 엮는다. 파트 사이를 잇는 유일한 곳이다.

책임:
    - 신고 접수: 접수번호(`SC-<UUID 앞 8자리>`)를 만들고, `models.is_self_guide_candidate`·`has_customer_procedure`로
      후보를 가린 뒤 후보이면 `pipeline.run_customer_guidance`를 제한 시간(`llm_timeout_seconds`) 안에 호출한다.
      결과에 따라 `answered`면 `save_self_guide_case`, 보류면 `save_support_case(..., held)`,
      장애·검증 실패면 `save_support_case(..., generation_failed)`로 저장한다(spec §2.10 경로 결정).
    - 엔지니어 질문: 완료 건을 막고(`models.raise_if_case_completed`) 대화 이력과 함께
      `pipeline.run_engineer_answer`를 호출한 뒤 `rdb.save_chat_turn`으로 저장한다. 저장은 이 모듈의 일이다.
    - 조치 확정: `rdb.save_confirmed_resolution` 뒤 `vectordb.index_history`로 검색 근거에 반영한다.
      반영이 실패해도 저장은 유지하고 실패 여부만 `bool`로 알린다(앱 재시작의 전체 재생성으로 복구).
    - 고객 화면에는 공개 필드(`CustomerCaseResult`)만 돌려준다. 내부 원인·`guide_failure`·내부 분석은 내보내지 않는다.
    - 시작할 때 검색 색인을 원자료로 새로 만든다(`prepare_search_index`).
    - 화면에 보여 줄 오류 문구는 정하지 않는다. 예외 종류만 구분해 던지고 문구는 `ui/common`이 맡는다.

화면–서비스 연결(spec §2.3.3):
    고객 신고 목록 → `list_customer_cases` / 고객 신고 작성 → `list_error_codes`, `submit_case` /
    고객 신고 상세 → `get_customer_case`, `get_citation_text` / 엔지니어 대시보드 → `list_engineer_cases` /
    장애 분석 대화 → `get_engineer_case`, `list_chat_turns`, `send_chat_question`, `get_citation_text` /
    이력 저장 → `get_engineer_case`, `confirm_resolution`

의존: `db.parser`, `db.rdb`, `db.vectordb`, `rag.pipeline`, `config`, `models`(의존 규칙 §2.5).
소유: UI·총괄 파트
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError

from mpilot.config import AppConfig
from mpilot.db import parser, rdb, vectordb
from mpilot.models import (
    Answer,
    CaseContext,
    CaseDetail,
    CaseInput,
    CaseStatus,
    ChatRequest,
    ChatTurn,
    Chunk,
    Citation,
    CustomerCaseResult,
    DependencyError,
    Equipment,
    ErrorCode,
    GuideFailure,
    MpilotError,
    Resolution,
    Source,
    ValidationError,
    Verdict,
    has_customer_procedure,
    is_self_guide_candidate,
    raise_if_case_completed,
)
from mpilot.rag import pipeline

MAX_QUESTION_LENGTH = 2000
"""엔지니어 질문의 최대 글자 수(앞뒤 공백 제거 후, `ChatRequest.question`과 같다)."""


def _to_customer_result(detail: CaseDetail) -> CustomerCaseResult:
    """`CaseDetail`에서 고객 공개 필드만 꺼낸다. 원인·`guide_failure`는 빼고 넘긴다."""
    case = detail.service_case
    resolution = detail.resolution
    return CustomerCaseResult(
        case_id=case.case_id, equipment=case.equipment, code=case.code,
        error_name=detail.error_code.name, memo=case.memo, status=case.status,
        created_at=case.created_at, guidance_answer=case.guidance_answer,
        action=resolution.action if resolution else None,
        result=resolution.result if resolution else None,
    )


def _sorted_newest(details: list[CaseDetail]) -> list[CaseDetail]:
    """신고를 최신순으로 정렬한다."""
    return sorted(details, key=lambda d: d.service_case.created_at, reverse=True)


# `set_config`가 보관한 앱 설정. 앱 시작 전에는 `None`이다.
_config: AppConfig | None = None


def set_config(config: AppConfig) -> None:
    """앱 설정을 보관한다.

    Args:
        config: `config.load_config`가 만든 앱 설정. 경로, 검색 설정, 모델 제한 시간을 여기서 읽는다.

    Returns:
        None: 모듈 안에 설정을 보관한다. 다른 함수는 이 설정을 쓴다.
    """
    global _config
    _config = config


def _require_config() -> AppConfig:
    """보관한 앱 설정을 돌려준다.

    Returns:
        AppConfig: `set_config`로 보관한 설정.

    Raises:
        DependencyError: `set_config` 전에 불렀을 때.
    """
    if _config is None:
        raise DependencyError("set_config 전에 호출됨")
    return _config


def list_customer_cases(status: CaseStatus | None = None) -> list[CustomerCaseResult]:
    """고객 본인의 신고를 공개 필드로 조회한다.

    직접 접수한 신고(`source=reported`)만 최신순으로 돌려준다. 시드 이력은 포함하지 않는다.

    Args:
        status: 지정하면 그 진행 상태의 신고만 돌려준다. `None`이면 모두.

    Returns:
        list[CustomerCaseResult]: 공개 필드만 담은 신고 목록(최신순).

    Raises:
        ValidationError: 허용되지 않은 상태 값일 때.
        DependencyError: DB 조회에 실패했을 때.
    """
    details = rdb.fetch_cases(status, Source.REPORTED)
    return [_to_customer_result(d) for d in _sorted_newest(details)]


def get_customer_case(case_id: str) -> CustomerCaseResult:
    """고객 공개 신고 상세를 조회한다.

    Args:
        case_id: 접수번호.

    Returns:
        CustomerCaseResult: 진행 상태와 셀프 가이드(단계·출처) 또는 완료된 조치 결과(`action`, `result`).
            내부 원인은 담지 않는다.

    Raises:
        NotFoundError: 접수번호가 없거나 고객이 접근할 수 없는 건(시드 이력)일 때.
        DependencyError: DB 조회에 실패했을 때.
    """
    # TODO: 실제 호출로 교체 (rdb.fetch_case_detail)
    ...


def list_error_codes(equipment: Equipment) -> list[ErrorCode]:
    """설비별 에러코드 선택지를 조회한다.

    Args:
        equipment: 고객이 고른 설비.

    Returns:
        list[ErrorCode]: 그 설비의 에러코드 표 행. 신고 작성 화면의 코드 선택지가 된다.

    Raises:
        ValidationError: 허용되지 않은 설비일 때.
        DependencyError: DB 조회에 실패했을 때.
    """
    return rdb.fetch_error_codes(equipment)


def submit_case(data: CaseInput) -> str:
    """신고를 접수하고 처리 경로를 확정한 뒤 접수번호를 반환한다.

    심각도 `상`이거나 고객이 조치할 수 없는 코드, 허용 절차가 없는 코드는 바로 엔지니어에게 접수한다.
    셀프 가이드 후보는 가이드를 만들어 보고, 만들 수 없으면 추측하지 않고 엔지니어에게 접수한다.

    Args:
        data: 고객이 제출한 설비·에러코드·증상 메모.

    Returns:
        str: 새로 발급한 접수번호(`SC-<UUID 앞 8자리>`).

    Raises:
        ValidationError: 미등록 코드이거나 메모가 2,000자를 넘을 때.
        DependencyError: DB 저장에 실패했을 때. 가이드 생성 실패는 예외가 아니라 엔지니어 접수로 처리한다.
    """
    error_code = _find_error_code(data)
    case_id = _new_case_id()

    if not is_self_guide_candidate(error_code):
        # 심각도 상이거나 엔지니어 필요 코드: 처음부터 엔지니어 처리 건이다.
        rdb.save_support_case(case_id, data, None)
        return case_id
    if not has_customer_procedure(error_code):
        # 허용 절차가 없으면 가이드를 만들 근거가 없다. pipeline도 같은 경우를 보류로 돌려주므로 held로 맞춘다.
        rdb.save_support_case(case_id, data, GuideFailure.HELD)
        return case_id

    answer = _run_guidance_within_limit(CaseContext(error_code=error_code, memo=data.memo))
    if answer is None:
        rdb.save_support_case(case_id, data, GuideFailure.GENERATION_FAILED)
    elif answer.verdict == Verdict.ANSWERED:
        rdb.save_self_guide_case(case_id, data, answer)
    else:
        rdb.save_support_case(case_id, data, GuideFailure.HELD)
    return case_id


def _new_case_id() -> str:
    """새 접수번호 `SC-<UUID 앞 8자리>`를 만든다. 충돌은 `rdb`가 오류로 알린다(재시도하지 않는다)."""
    return f"SC-{uuid.uuid4().hex[:8]}"


def _find_error_code(data: CaseInput) -> ErrorCode:
    """신고한 `(설비, 코드)`의 에러코드 표 행을 찾는다. 비슷한 코드로 대체하지 않는다.

    Args:
        data: 고객이 제출한 신고 입력.

    Returns:
        ErrorCode: 정확히 같은 코드의 행.

    Raises:
        ValidationError: 표에 없는 코드일 때.
        DependencyError: DB 조회에 실패했을 때.
    """
    for row in rdb.fetch_error_codes(data.equipment):
        if row.code == data.code:
            return row
    raise ValidationError("미등록 코드")


def _run_guidance_within_limit(context: CaseContext) -> Answer | None:
    """고객 안내를 제한 시간(`llm_timeout_seconds`) 안에 만든다.

    Args:
        context: 신고한 에러코드와 증상 메모.

    Returns:
        Answer | None: 만든 안내(보류 판정 포함). 장애·검증 실패·시간 초과면 `None`이다.
            가이드를 만들지 못한 것은 오류가 아니라 엔지니어 접수로 처리하므로 예외를 올리지 않는다.

    Raises:
        DependencyError: `set_config` 전에 불렀을 때.
    """
    timeout = _require_config().llm_timeout_seconds
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        return executor.submit(pipeline.run_customer_guidance, context).result(timeout=timeout)
    except FutureTimeoutError:
        return None  # 시간 초과: 늦게 끝난 결과는 쓰지 않는다
    except Exception:
        return None  # 모델·검색 장애와 검증 실패(DependencyError)를 포함해 신고는 엔지니어 접수로 넘긴다
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def list_engineer_cases(status: CaseStatus | None = None) -> list[CaseDetail]:
    """엔지니어용 전체 신고 목록을 조회한다.

    시드 이력과 신규 신고를 모두 포함해 최신순으로 돌려준다.

    Args:
        status: 지정하면 그 진행 상태의 신고만 돌려준다. `None`이면 모두.

    Returns:
        list[CaseDetail]: 신고·에러코드·조치 기록 목록(최신순).

    Raises:
        ValidationError: 허용되지 않은 상태 값일 때.
        DependencyError: DB 조회에 실패했을 때.
    """
    # TODO: 실제 호출로 교체 (rdb.fetch_cases(status))
    ...


def get_engineer_case(case_id: str) -> CaseDetail:
    """신고·에러코드·현장 조치를 조회한다.

    Args:
        case_id: 접수번호.

    Returns:
        CaseDetail: 신고 건, 신고한 에러코드 행, 조치 기록(있으면).

    Raises:
        NotFoundError: 접수번호가 없을 때.
        DependencyError: DB 조회에 실패했을 때.
    """
    return rdb.fetch_case_detail(case_id)


def list_chat_turns(case_id: str) -> list[ChatTurn]:
    """신고의 대화 이력을 조회한다.

    Args:
        case_id: 접수번호.

    Returns:
        list[ChatTurn]: 오래된 순 대화 목록. 대화가 없으면 빈 목록.

    Raises:
        NotFoundError: 접수번호가 없을 때.
        DependencyError: DB 조회에 실패했을 때.
    """
    rdb.fetch_case_detail(case_id)  # 없는 접수번호면 NotFoundError(fetch_chat_turns는 빈 목록을 돌려준다)
    return rdb.fetch_chat_turns(case_id)


def send_chat_question(case_id: str, question: str) -> None:
    """엔지니어 질문에 답하고 대화를 저장한다.

    저장된 대화와 함께 답변을 만들고, 보류 판정(근거 부족)도 대화로 저장한다.
    첫 질문이면 신고가 `in_progress`가 된다. 답변이 `DependencyError`로 실패하면 저장하지 않는다.

    Args:
        case_id: 접수번호.
        question: 엔지니어의 질문. 앞뒤 공백 제거 후 1~2,000자.

    Returns:
        None: 결과는 저장된 대화로 남으며, 화면은 `list_chat_turns`로 다시 읽는다.

    Raises:
        ValidationError: 질문이 비었거나 길이 범위를 벗어났을 때.
        NotFoundError: 접수번호가 없을 때.
        InvalidStateError: 이미 완료된 건이거나 셀프 가이드 건일 때('이미 완료').
        DependencyError: 모델·검색·DB 장애나 제한 시간 초과일 때.
    """
    detail = rdb.fetch_case_detail(case_id)
    raise_if_case_completed(detail.service_case)  # 완료 건·셀프 가이드 건은 모델을 부르기 전에 막는다
    question = question.strip()
    if not 1 <= len(question) <= MAX_QUESTION_LENGTH:
        raise ValidationError("질문 길이")
    request = ChatRequest(question=question, turns=rdb.fetch_chat_turns(case_id))
    context = CaseContext(error_code=detail.error_code, memo=detail.service_case.memo)
    answer = _run_engineer_answer_within_limit(context, request)
    rdb.save_chat_turn(case_id, question, answer)  # 첫 질문이면 rdb가 in_progress로 바꾼다


def _run_engineer_answer_within_limit(context: CaseContext, request: ChatRequest) -> Answer:
    """엔지니어 답변을 제한 시간(`llm_timeout_seconds`) 안에 만든다.

    고객 안내(`_run_guidance_within_limit`)와 달리 실패를 삼키지 않는다. 엔지니어 질문은 저장하지 않고
    `DependencyError`로 알려 화면이 다시 시도하게 한다.

    Args:
        context: 신고한 에러코드와 증상 메모.
        request: 현재 질문과 저장된 대화.

    Returns:
        Answer: 출처가 붙은 답변 또는 근거 부족 판정.

    Raises:
        ValidationError: 질문이나 검색 설정이 허용 범위를 벗어났을 때.
        DependencyError: 모델·검색 장애, 검증 실패, 제한 시간 초과, `set_config` 전에 불렀을 때.
    """
    config = _require_config()
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(pipeline.run_engineer_answer, context, request, config.search)
        return future.result(timeout=config.llm_timeout_seconds)
    except FutureTimeoutError:
        raise DependencyError("엔지니어 답변 시간 초과") from None  # 늦게 끝난 결과는 쓰지 않는다
    except MpilotError:
        raise
    except Exception as e:  # 모델 클라이언트 등의 예상 밖 오류도 화면에는 장애로 알린다
        raise DependencyError("엔지니어 답변 실패") from e
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def confirm_resolution(case_id: str, record: Resolution) -> tuple[CaseDetail, bool]:
    """조치 결과를 확정하고 검색 근거에 반영하며, 반환의 `bool`은 검색 반영 성공 여부다.

    Args:
        case_id: 접수번호.
        record: 엔지니어가 쓴 원인·실제 조치·결과. 각 필드는 앞뒤 공백 제거 후 1~5,000자.

    Returns:
        tuple[CaseDetail, bool]: 완료된 신고와 조치 기록, 그리고 검색 반영 성공 여부.
            반영이 실패해도 조치 결과 저장은 유지되며 `False`로 알린다.

    Raises:
        ValidationError: 필드가 비었거나 길이 범위를 벗어났을 때.
        NotFoundError: 접수번호가 없을 때.
        InvalidStateError: 이미 완료된 건이거나 셀프 가이드 건일 때('이미 완료').
        DependencyError: 조치 결과 저장(DB)에 실패했을 때.
    """
    # TODO: 실제 호출로 교체 (rdb.save_confirmed_resolution, vectordb.index_history)
    ...


def get_citation_text(citation: Citation) -> str:
    """인용 출처의 원문을 조회한다.

    Args:
        citation: 답변 단계가 가리키는 근거(`kind`, `ref_id`, `locator`).

    Returns:
        str: 그 문서의 원문. 화면이 출처를 펼칠 때 보여 준다.

    Raises:
        NotFoundError: 그 ID의 원문이 없을 때.
        DependencyError: 검색 저장소 조회에 실패했을 때.
    """
    # TODO: 실제 호출로 교체 (vectordb.get_document)
    ...


def prepare_search_index() -> None:
    """검색 색인을 원자료로 새로 만든다.

    매뉴얼 PDF를 청킹하고 설비 4종의 에러코드 표와 확정 이력(`rdb.fetch_resolved_cases`)을 읽어
    `vectordb.rebuild_search_index`로 두 컬렉션을 다시 만든다. 앱 시작 때 첫 화면을 열기 전에 끝나야 한다.

    Returns:
        None: 색인이 준비된다.

    Raises:
        ValidationError: 문서 ID 구성이나 허용 절차 ID 검증에 실패했을 때.
        DependencyError: PDF·DB·임베딩·저장소 장애일 때. 이 경우 앱 시작을 중단한다.
    """
    config = _require_config()
    chunks = parser.parse_manuals(config.manuals_dir)
    codes = [row for equipment in Equipment for row in rdb.fetch_error_codes(equipment)]
    _check_customer_procedure_ids(codes, {_chunk_ref_id(chunk) for chunk in chunks})
    histories = rdb.fetch_resolved_cases()
    vectordb.rebuild_search_index(chunks, codes, histories)


def _chunk_ref_id(chunk: Chunk) -> str:
    """매뉴얼 청크의 검색 문서 ID(`manual:<manual_id>:<쪽>:<chunk_no>`, spec §3.4)를 만든다."""
    return f"manual:{chunk.manual_id}:{chunk.page}:{chunk.chunk_no}"


def _check_customer_procedure_ids(codes: list[ErrorCode], chunk_ids: set[str]) -> None:
    """에러코드 표의 허용 절차 ID가 모두 매뉴얼 청크에 있는지 확인한다.

    Args:
        codes: 설비 4종의 에러코드 표 행.
        chunk_ids: 매뉴얼 청크의 문서 ID 모음.

    Returns:
        None: 모두 있으면 아무 일도 하지 않는다.

    Raises:
        ValidationError: 청크에 없는 허용 절차 ID가 하나라도 있을 때. 어느 코드인지만 알리고 고치지 않는다.
    """
    missing = [
        f"{row.equipment}:{row.code}"
        for row in codes
        if any(ref_id not in chunk_ids for ref_id in row.customer_procedure_ref_ids)
    ]
    if missing:
        raise ValidationError(f"매뉴얼에 없는 허용 절차 ID: {', '.join(missing)}")
