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
from datetime import datetime, timezone

from mpilot.config import AppConfig
from mpilot.db import parser, rdb, vectordb
from mpilot.models import (
    ActionOwner,
    Answer,
    AnswerStep,
    CaseDetail,
    CaseInput,
    CaseStatus,
    ChatTurn,
    Chunk,
    Citation,
    CitationKind,
    CustomerCaseResult,
    DependencyError,
    Equipment,
    ErrorCode,
    NotFoundError,
    Resolution,
    Route,
    ServiceCase,
    Severity,
    Source,
    ValidationError,
    Verdict,
    has_customer_procedure,
    is_self_guide_candidate,
    raise_if_case_completed,
)

# ---------------------------------------------------------------------------
# 가짜 데이터 (10/26). 10/27 실제 DB·RAG 호출로 바꾸면 이 구역을 지운다.
# 메모리에만 있으므로 앱을 다시 시작하면 처음 상태로 돌아간다.
# ---------------------------------------------------------------------------


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    """가짜 데이터용 UTC 시각을 만든다."""
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def _manual_citation(ref_id: str, locator: str) -> Citation:
    """가짜 매뉴얼 인용을 만든다."""
    return Citation(kind=CitationKind.MANUAL, ref_id=ref_id, locator=locator)


_FAKE_ERROR_CODES: list[ErrorCode] = [
    ErrorCode(
        equipment=Equipment.MINI_LOADER, code="E-101", name="포크 위치 센서 오감지",
        severity=Severity.LOW, action_owner=ActionOwner.CUSTOMER,
        cause="센서 표면 오염", action="센서 표면 청소 후 재기동",
        manual_id="mini_loader_rev1_1", page=12, section="3.2 센서 점검",
        customer_procedure_ref_ids=["manual:mini_loader_rev1_1:12:1"],
        required_safety_rules=["비상정지 버튼을 누른 뒤 작업한다"],
    ),
    ErrorCode(
        equipment=Equipment.MINI_LOADER, code="E-205", name="주행 모터 과전류",
        severity=Severity.HIGH, action_owner=ActionOwner.ENGINEER,
        cause="모터 구속 또는 드라이버 이상", action="엔지니어 점검",
        manual_id="mini_loader_rev1_1", page=30, section="5.1 구동부",
    ),
    ErrorCode(
        equipment=Equipment.SRM, code="S-310", name="리프트 엔코더 편차",
        severity=Severity.MEDIUM, action_owner=ActionOwner.ENGINEER,
        cause="엔코더 커플링 풀림", action="커플링 체결 확인",
        manual_id="srm_rev1_2", page=41, section="6.3 승강부",
    ),
    ErrorCode(
        equipment=Equipment.AGV, code="A-120", name="배터리 저전압",
        severity=Severity.LOW, action_owner=ActionOwner.CUSTOMER,
        cause="충전 부족", action="충전 스테이션 복귀 후 충전",
        manual_id="agv_rev1_1", page=8, section="2.4 배터리",
        customer_procedure_ref_ids=["manual:agv_rev1_1:8:2"],
        required_safety_rules=["충전 중에는 차량을 이동하지 않는다"],
    ),
    ErrorCode(
        equipment=Equipment.RTV, code="R-402", name="도어 인터락 해제",
        severity=Severity.MEDIUM, action_owner=ActionOwner.CUSTOMER,
        cause="안전 도어 열림", action="도어 닫힘 확인",
        manual_id="rtv_rev2_1", page=15, section="4.1 안전장치",
    ),
]

_FAKE_CITATION_TEXTS: dict[str, str] = {
    "manual:mini_loader_rev1_1:12:1": "[가상 원문] 3.2 센서 점검: 비상정지 버튼을 누른 뒤 마른 천으로 센서 표면을 닦는다.",
    "manual:agv_rev1_1:8:2": "[가상 원문] 2.4 배터리: 충전 스테이션으로 복귀시켜 충전한다. 충전 중에는 차량을 이동하지 않는다.",
    "history:SEED-0001": "[가상 원문] SEED-0001 / SRM S-310: 엔코더 커플링 풀림 → 재체결 → 정상 동작 확인.",
}


def _fake_answered(text: str, ref_id: str, locator: str) -> Answer:
    """단계 하나짜리 가짜 답변을 만든다."""
    return Answer(
        verdict=Verdict.ANSWERED,
        steps=[AnswerStep(text=text, citations=[_manual_citation(ref_id, locator)])],
        notice=None,
    )


def _find_code(equipment: Equipment, code: str) -> ErrorCode:
    """가짜 에러코드 표에서 `(설비, 코드)` 행을 찾는다.

    Raises:
        ValidationError: 표에 없는 코드일 때.
    """
    for row in _FAKE_ERROR_CODES:
        if row.equipment == equipment and row.code == code:
            return row
    raise ValidationError("미등록 코드")


def _fake_case(
    case_id: str, equipment: Equipment, code: str, *, source: Source, status: CaseStatus, route: Route,
    created_at: datetime, memo: str | None = None, guidance_answer: Answer | None = None,
    completed_at: datetime | None = None, resolution: Resolution | None = None,
) -> CaseDetail:
    """가짜 신고 한 건을 `CaseDetail`로 만든다."""
    return CaseDetail(
        service_case=ServiceCase(
            case_id=case_id, equipment=equipment, code=code, memo=memo, source=source,
            status=status, route=route, guide_failure=None, guidance_answer=guidance_answer,
            created_at=created_at, completed_at=completed_at,
        ),
        error_code=_find_code(equipment, code),
        resolution=resolution,
    )


# 접수번호 → 신고. 함수들이 이 딕셔너리를 읽고 고친다.
_FAKE_CASES: dict[str, CaseDetail] = {
    detail.service_case.case_id: detail
    for detail in [
        _fake_case(
            "SC-1a2b3c4d", Equipment.MINI_LOADER, "E-101", source=Source.REPORTED,
            status=CaseStatus.COMPLETED, route=Route.SELF_GUIDE, created_at=_utc(2026, 10, 25, 1),
            memo="포크가 가끔 멈춤",
            guidance_answer=_fake_answered(
                "비상정지 버튼을 누른 뒤 센서 표면을 마른 천으로 닦는다.",
                "manual:mini_loader_rev1_1:12:1", "p.12 · 3.2 센서 점검",
            ),
            completed_at=_utc(2026, 10, 25, 1, 1),
        ),
        _fake_case(
            "SC-5e6f7a8b", Equipment.MINI_LOADER, "E-205", source=Source.REPORTED,
            status=CaseStatus.RECEIVED, route=Route.SUPPORT, created_at=_utc(2026, 10, 26, 0, 30),
            memo="주행 중 정지 후 재기동 안 됨",
        ),
        _fake_case(
            "SC-9c0d1e2f", Equipment.SRM, "S-310", source=Source.REPORTED,
            status=CaseStatus.COMPLETED, route=Route.SUPPORT, created_at=_utc(2026, 10, 24, 5),
            completed_at=_utc(2026, 10, 24, 9),
            resolution=Resolution(cause="엔코더 커플링 풀림", action="커플링 재체결", result="정상 동작 확인"),
        ),
        _fake_case(
            "SEED-0001", Equipment.SRM, "S-310", source=Source.SEED,
            status=CaseStatus.COMPLETED, route=Route.SUPPORT, created_at=_utc(2026, 9, 1, 2),
            completed_at=_utc(2026, 9, 1, 6),
            resolution=Resolution(cause="엔코더 커플링 풀림", action="커플링 재체결", result="정상 동작 확인"),
        ),
    ]
}

# 접수번호 → 대화 목록.
_FAKE_TURNS: dict[str, list[ChatTurn]] = {}


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
    # TODO: 실제 호출로 교체 (rdb.fetch_cases(status, Source.REPORTED))
    details = [
        d for d in _FAKE_CASES.values()
        if d.service_case.source == Source.REPORTED and (status is None or d.service_case.status == status)
    ]
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
    detail = _FAKE_CASES.get(case_id)
    if detail is None or detail.service_case.source != Source.REPORTED:
        raise NotFoundError(case_id)
    return _to_customer_result(detail)


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
    # TODO: 실제 호출로 교체 (rdb.fetch_error_codes)
    return [row for row in _FAKE_ERROR_CODES if row.equipment == equipment]


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
    # TODO: 실제 호출로 교체 (pipeline.run_customer_guidance, rdb.save_self_guide_case / save_support_case)
    error_code = _find_code(data.equipment, data.code)
    case_id = f"SC-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    if is_self_guide_candidate(error_code) and has_customer_procedure(error_code):
        ref_id = error_code.customer_procedure_ref_ids[0]
        guidance = _fake_answered(
            f"{error_code.action} ({error_code.required_safety_rules[0] if error_code.required_safety_rules else '안전 수칙 확인'})",
            ref_id, f"p.{error_code.page} · {error_code.section}",
        )
        detail = _fake_case(
            case_id, data.equipment, data.code, source=Source.REPORTED, status=CaseStatus.COMPLETED,
            route=Route.SELF_GUIDE, created_at=now, memo=data.memo, guidance_answer=guidance, completed_at=now,
        )
    else:
        detail = _fake_case(
            case_id, data.equipment, data.code, source=Source.REPORTED, status=CaseStatus.RECEIVED,
            route=Route.SUPPORT, created_at=now, memo=data.memo,
        )
    _FAKE_CASES[case_id] = detail
    return case_id


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
    details = [d for d in _FAKE_CASES.values() if status is None or d.service_case.status == status]
    return _sorted_newest(details)


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
    # TODO: 실제 호출로 교체 (rdb.fetch_case_detail)
    detail = _FAKE_CASES.get(case_id)
    if detail is None:
        raise NotFoundError(case_id)
    return detail


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
    # TODO: 실제 호출로 교체 (rdb.fetch_chat_turns)
    get_engineer_case(case_id)  # 없는 접수번호면 NotFoundError
    return list(_FAKE_TURNS.get(case_id, []))


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
    # TODO: 실제 호출로 교체 (pipeline.run_engineer_answer, rdb.save_chat_turn)
    question = question.strip()
    if not 1 <= len(question) <= 2000:
        raise ValidationError("질문 길이")
    detail = get_engineer_case(case_id)
    raise_if_case_completed(detail.service_case)
    turns = _FAKE_TURNS.setdefault(case_id, [])
    answer = _fake_answered(
        f"[가짜 답변] '{question}'에 대한 점검 순서: {detail.error_code.action}",
        f"error:{detail.error_code.equipment}:{detail.error_code.code}",
        f"p.{detail.error_code.page} · {detail.error_code.section}",
    )
    turns.append(
        ChatTurn(turn_id=len(turns) + 1, case_id=case_id, question=question, answer=answer,
                 created_at=datetime.now(timezone.utc))
    )
    if detail.service_case.status == CaseStatus.RECEIVED:  # 첫 질문이면 조치 중으로
        new_case = detail.service_case.model_copy(update={"status": CaseStatus.IN_PROGRESS})
        _FAKE_CASES[case_id] = detail.model_copy(update={"service_case": new_case})


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
    detail = get_engineer_case(case_id)
    raise_if_case_completed(detail.service_case)
    new_case = detail.service_case.model_copy(
        update={"status": CaseStatus.COMPLETED, "completed_at": datetime.now(timezone.utc)}
    )
    completed = detail.model_copy(update={"service_case": new_case, "resolution": record})
    _FAKE_CASES[case_id] = completed
    return completed, True


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
    text = _FAKE_CITATION_TEXTS.get(citation.ref_id)
    if text is None:
        return f"[가상 원문] {citation.ref_id} 원문은 실제 연결 뒤에 보입니다."
    return text


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
