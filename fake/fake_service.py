"""UI 개발용 가짜 service. DB·RAG 없이 service.py와 같은 함수를 메모리 자료로 흉내 낸다.

책임: 원자료 CSV로 에러코드·시드 이력을 만들고, 허용 조합별 예시 신고를 둔다.
    `install()`은 `service`의 공개 함수를 이 모듈의 같은 이름 함수로 바꿔 끼운다(`app.py --fake`만 부른다).
의존: config, models, service(바꿔 끼울 대상)
소유: UI·총괄 파트
"""

from __future__ import annotations

import csv
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from mpilot.config import AppConfig
from mpilot.models import (
    ActionOwner,
    Answer,
    AnswerStep,
    CaseDetail,
    CaseInput,
    CaseStatus,
    ChatTurn,
    Citation,
    CitationKind,
    CustomerCaseResult,
    DependencyError,
    Equipment,
    ErrorCode,
    GuideFailure,
    NotFoundError,
    Resolution,
    Route,
    ServiceCase,
    Severity,
    Source,
    ValidationError,
    Verdict,
    is_self_guide_candidate,
    raise_if_case_completed,
)

_RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"  # 원자료 CSV 폴더
_TIME_FORMAT = "%Y-%m-%d %H:%M"  # 이력 CSV의 시각 형식
_MOCK = "(목)"  # 지어낸 값에 붙이는 표시


@dataclass
class _Store:
    """메모리 저장소."""

    error_codes: dict[tuple[Equipment, str], ErrorCode]  # (설비, 코드) → 에러코드
    cases: dict[str, ServiceCase]  # 접수번호 → 신고
    resolutions: dict[str, Resolution] = field(default_factory=dict)  # 접수번호 → 조치 기록
    chat_turns: dict[str, list[ChatTurn]] = field(default_factory=dict)  # 접수번호 → 대화 목록
    next_turn_id: int = 1  # 다음 대화 턴 번호


_config: AppConfig | None = None  # set_config로 받은 설정
_store: _Store | None = None  # 처음 쓸 때 만든다


def set_config(config: AppConfig) -> None:
    """앱 설정을 보관한다.

    Args:
        config: 앱 설정. 가짜 구현은 보관만 하고 쓰지 않는다(`service`와 시그니처를 맞추기 위해 둔다).
    """
    global _config
    _config = config


def list_customer_cases(status: CaseStatus | None = None) -> list[CustomerCaseResult]:
    """고객이 접수한 신고를 공개 필드로 최신순 조회한다.

    Args:
        status: 진행 상태 필터. None이면 모두.

    Returns:
        list[CustomerCaseResult]: 신고 목록(최신순).

    Raises:
        ValidationError: 허용되지 않은 상태 값일 때.
    """
    store = _get_store()
    # 시드 이력은 빼고 고객이 접수한 건만
    cases = [c for c in _filter_by_status(store.cases.values(), status) if c.source == Source.REPORTED]
    return [_to_customer_result(store, c) for c in _latest_first(cases)]


def get_customer_case(case_id: str) -> CustomerCaseResult:
    """고객 공개 신고 상세를 조회한다.

    Args:
        case_id: 접수번호.

    Returns:
        CustomerCaseResult: 신고 상세(공개 필드).

    Raises:
        NotFoundError: 접수번호가 없거나 시드 이력일 때.
    """
    store = _get_store()
    case = _get_case(store, case_id)
    # 고객은 시드 이력을 볼 수 없음
    if case.source != Source.REPORTED:
        raise NotFoundError(f"접수번호 {case_id}을(를) 찾을 수 없습니다.")
    return _to_customer_result(store, case)


def list_error_codes(equipment: Equipment) -> list[ErrorCode]:
    """설비별 에러코드를 조회한다.

    Args:
        equipment: 설비.

    Returns:
        list[ErrorCode]: 그 설비의 에러코드(원자료 순서).

    Raises:
        ValidationError: 허용되지 않은 설비일 때.
    """
    equipment = _parse_enum(Equipment, equipment, "설비")
    return [e for (eq, _), e in _get_store().error_codes.items() if eq == equipment]


def submit_case(data: CaseInput) -> str:
    """신고를 접수하고 접수번호를 돌려준다.

    Args:
        data: 설비·에러코드·증상 메모.

    Returns:
        str: 접수번호(SC-<UUID 앞 8자리>).

    Raises:
        ValidationError: 미등록 코드일 때.
    """
    store = _get_store()
    error_code = store.error_codes.get((data.equipment, data.code))
    # 표에 없는 코드는 접수하지 않음
    if error_code is None:
        raise ValidationError(f"{data.equipment} {data.code}은(는) 등록되지 않은 코드입니다.")
    case_id = f"SC-{uuid.uuid4().hex[:8]}"
    store.cases[case_id] = _new_reported_case(case_id, error_code, data.memo, datetime.now())
    return case_id


def list_engineer_cases(status: CaseStatus | None = None) -> list[CaseDetail]:
    """시드 이력과 신규 신고를 모두 최신순 조회한다.

    Args:
        status: 진행 상태 필터. None이면 모두.

    Returns:
        list[CaseDetail]: 신고·에러코드·조치 기록 목록(최신순).

    Raises:
        ValidationError: 허용되지 않은 상태 값일 때.
    """
    store = _get_store()
    cases = _latest_first(_filter_by_status(store.cases.values(), status))
    return [_to_detail(store, c) for c in cases]


def get_engineer_case(case_id: str) -> CaseDetail:
    """신고·에러코드·조치 기록을 조회한다.

    Args:
        case_id: 접수번호.

    Returns:
        CaseDetail: 신고 상세.

    Raises:
        NotFoundError: 접수번호가 없을 때.
    """
    store = _get_store()
    return _to_detail(store, _get_case(store, case_id))


def list_chat_turns(case_id: str) -> list[ChatTurn]:
    """신고의 대화를 오래된 순으로 조회한다.

    Args:
        case_id: 접수번호.

    Returns:
        list[ChatTurn]: 대화 목록. 없으면 빈 목록.

    Raises:
        NotFoundError: 접수번호가 없을 때.
    """
    store = _get_store()
    _get_case(store, case_id)  # 없는 접수번호면 여기서 예외
    return list(store.chat_turns.get(case_id, []))


def send_chat_question(case_id: str, question: str) -> None:
    """가짜 답변을 만들어 대화를 저장한다.

    질문에 [insufficient]를 넣으면 보류 답변, [fail]을 넣으면 장애가 난다.

    Args:
        case_id: 접수번호.
        question: 질문(공백 제거 후 1~2,000자).

    Raises:
        ValidationError: 질문 길이가 범위를 벗어날 때.
        NotFoundError: 접수번호가 없을 때.
        InvalidStateError: 이미 완료된 건일 때.
        DependencyError: 질문에 [fail]이 있을 때.
    """
    store = _get_store()
    case = _get_case(store, case_id)
    raise_if_case_completed(case)  # 완료 건은 질문 불가
    question = question.strip()
    if not 1 <= len(question) <= 2000:
        raise ValidationError("질문은 1~2,000자로 입력해 주세요.")
    # 모델 장애 흉내. 대화는 저장하지 않음
    if "[fail]" in question:
        raise DependencyError(f"{_MOCK} 답변 생성에 실패했습니다.")
    _add_chat_turn(store, case, question, _fake_engineer_answer(store, case, question), datetime.now())


def confirm_resolution(case_id: str, record: Resolution) -> tuple[CaseDetail, bool]:
    """조치 결과를 저장하고 신고를 완료한다.

    결과에 [fail]을 넣으면 검색 반영 실패(False)를 돌려준다.

    Args:
        case_id: 접수번호.
        record: 원인·조치·결과.

    Returns:
        tuple[CaseDetail, bool]: 완료된 신고, 검색 반영 성공 여부.

    Raises:
        NotFoundError: 접수번호가 없을 때.
        InvalidStateError: 이미 완료된 건일 때.
    """
    store = _get_store()
    case = _get_case(store, case_id)
    raise_if_case_completed(case)  # 완료 건은 다시 등록 불가
    store.resolutions[case_id] = record
    store.cases[case_id] = case.model_copy(
        update={"status": CaseStatus.COMPLETED, "completed_at": datetime.now()}
    )
    return _to_detail(store, store.cases[case_id]), "[fail]" not in record.result


def get_citation_text(citation: Citation) -> str:
    """인용 출처의 원문을 돌려준다.

    Args:
        citation: 인용.

    Returns:
        str: 원문. 매뉴얼 본문은 가짜 문장.

    Raises:
        NotFoundError: 그 ID의 문서가 없을 때.
    """
    store = _get_store()
    prefix, _, rest = citation.ref_id.partition(":")  # "error:AGV:A361" → "error", "AGV:A361"
    # 에러코드 표 행
    if prefix == "error":
        equipment, _, code = rest.partition(":")
        error_code = store.error_codes.get((equipment, code))
        if error_code is not None:
            return _error_code_text(error_code)
    # 확정 이력
    elif prefix == "history":
        resolution = store.resolutions.get(rest)
        if resolution is not None:
            case = store.cases[rest]
            return (
                f"[{case.case_id}] {case.equipment} {case.code}\n증상: {case.memo or '-'}\n"
                f"원인: {resolution.cause}\n조치: {resolution.action}\n결과: {resolution.result}"
            )
    # 매뉴얼 본문(아직 파싱 전이라 가짜 문장)
    elif prefix == "manual" and rest:
        return f"{_MOCK} 매뉴얼 원문({citation.locator}). 실제 원문은 DB 파트 구현 뒤에 보입니다."
    raise NotFoundError(f"{citation.ref_id}의 원문을 찾을 수 없습니다.")


def prepare_search_index() -> None:
    """가짜 자료를 미리 만든다. 색인은 만들지 않는다."""
    _get_store()


def reset_fake_data() -> None:
    """메모리 자료를 버린다. 테스트에서 쓴다."""
    global _store
    _store = None


def _get_store() -> _Store:
    """저장소를 돌려준다. 처음이면 원자료로 만든다."""
    global _store
    if _store is None:
        _store = _build_store()
    return _store


def _build_store() -> _Store:
    """에러코드·시드 이력·예시 신고로 저장소를 만든다."""
    store = _Store(error_codes=_load_error_codes(), cases={})
    _load_seed_history(store)
    _add_sample_reported_cases(store)
    return store


def _read_csv(path: Path) -> list[dict[str, str]]:
    """BOM이 붙은 UTF-8 CSV를 읽는다."""
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _load_error_codes() -> dict[tuple[Equipment, str], ErrorCode]:
    """에러코드 표 CSV를 읽는다."""
    codes: dict[tuple[Equipment, str], ErrorCode] = {}
    for row in _read_csv(_RAW_DIR / "error_code_table.csv"):
        equipment = Equipment(row["equipment"])
        action_owner = ActionOwner(row["action_owner"])
        manual_id = f"mock-{equipment.name.lower()}"  # 예: mock-agv
        codes[(equipment, row["code"])] = ErrorCode(
            equipment=equipment,
            code=row["code"],
            name=row["name"],
            severity=Severity(row["severity"]),  # 원자료 값 그대로
            action_owner=action_owner,  # 원자료 값 그대로
            cause=f"{_MOCK} 원인 미정",  # 이하 원자료에 없는 열
            action=f"{_MOCK} 조치 미정",
            manual_id=manual_id,
            page=1,
            section=_MOCK,
            required_safety_rules=[],
        )
    return codes


def _load_seed_history(store: _Store) -> None:
    """설비별 이력 CSV 200건을 완료된 시드 신고로 올린다."""
    for path in sorted(_RAW_DIR.glob("*_history_dummy.csv")):
        for row in _read_csv(path):
            case_id = row["이력ID"]  # 원본 ID 보존
            store.cases[case_id] = ServiceCase(
                case_id=case_id,
                equipment=Equipment(row["모델명"]),
                code=row["에러 코드"],
                memo=row["현장 증상"].strip() or None,
                source=Source.SEED,
                status=CaseStatus.COMPLETED,  # 시드는 모두 완료·support 고정
                route=Route.SUPPORT,
                guide_failure=None,
                guidance_answer=None,
                created_at=datetime.strptime(row["발생일시"], _TIME_FORMAT),
                completed_at=datetime.strptime(row["복구일시"], _TIME_FORMAT),
            )
            store.resolutions[case_id] = Resolution(
                cause=row["원인"], action=row["조치 내용"], result=row["결과"]
            )


def _add_sample_reported_cases(store: _Store) -> None:
    """허용 조합마다 예시 신고를 한 건씩 만든다."""
    customer = _first_code(store, lambda e: is_self_guide_candidate(e))  # 셀프 가이드 후보 코드
    engineer = _first_code(store, lambda e: not is_self_guide_candidate(e))  # 엔지니어 처리 코드
    now = datetime.now().replace(second=0, microsecond=0)
    # (접수번호, 코드, 메모, 몇 시간 전 접수)
    samples = [
        ("SC-mock0001", customer, f"{_MOCK} 셀프 가이드 예시", 6),
        ("SC-mock0002", engineer, f"{_MOCK} 엔지니어 처리 예시", 5),
        ("SC-mock0003", engineer, f"{_MOCK} 대화 진행 중 예시", 4),
        ("SC-mock0004", customer, f"{_MOCK} 가이드 보류 예시 [held]", 3),
        ("SC-mock0005", customer, f"{_MOCK} 가이드 생성 실패 예시 [fail]", 2),
        ("SC-mock0006", engineer, f"{_MOCK} 조치 완료 예시", 1),
    ]
    for case_id, error_code, memo, hours_ago in samples:
        store.cases[case_id] = _new_reported_case(case_id, error_code, memo, now - timedelta(hours=hours_ago))

    # 0003: 질문 하나를 넣어 in_progress로 만듦
    in_progress = store.cases["SC-mock0003"]
    question = f"{_MOCK} 같은 증상의 과거 조치가 있나요?"
    _add_chat_turn(store, in_progress, question, _fake_engineer_answer(store, in_progress, question), now)

    # 0006: 대화 두 턴을 넣은 뒤 조치 결과를 넣어 완료로 만듦(완료 건의 대화 기록 확인용)
    for minutes, question in [(10, f"{_MOCK} 이 에러의 점검 순서를 알려 주세요."),
                              (20, f"{_MOCK} 같은 증상의 과거 조치가 있나요?")]:
        case = store.cases["SC-mock0006"]
        answer = _fake_engineer_answer(store, case, question)
        _add_chat_turn(store, case, question, answer, case.created_at + timedelta(minutes=minutes))
    done = store.cases["SC-mock0006"]
    store.resolutions[done.case_id] = Resolution(
        cause=f"{_MOCK} 내부 원인(고객 비공개)",
        action=f"{_MOCK} 현장에서 부품 점검 후 재가동",
        result=f"{_MOCK} 정상 동작 확인",
    )
    store.cases[done.case_id] = done.model_copy(
        update={"status": CaseStatus.COMPLETED, "completed_at": done.created_at + timedelta(minutes=30)}
    )


def _first_code(store: _Store, predicate) -> ErrorCode:
    """조건에 맞는 첫 에러코드를 고른다."""
    return next(e for e in store.error_codes.values() if predicate(e))


def _new_reported_case(case_id: str, error_code: ErrorCode, memo: str | None, created_at: datetime) -> ServiceCase:
    """경로를 정해 신규 신고를 만든다.

    메모에 [held]·[fail]을 넣으면 가이드 보류·생성 실패로 접수된다.
    """
    route, guide_failure, answer = Route.SUPPORT, None, None  # 기본은 엔지니어 처리
    if is_self_guide_candidate(error_code):
        memo_text = memo or ""
        if "[held]" in memo_text:
            guide_failure = GuideFailure.HELD
        elif "[fail]" in memo_text:
            guide_failure = GuideFailure.GENERATION_FAILED
        else:
            route, answer = Route.SELF_GUIDE, _fake_customer_guidance(error_code)
    completed = route == Route.SELF_GUIDE  # 셀프 가이드는 접수 즉시 완료
    return ServiceCase(
        case_id=case_id,
        equipment=error_code.equipment,
        code=error_code.code,
        memo=memo,
        source=Source.REPORTED,
        status=CaseStatus.COMPLETED if completed else CaseStatus.RECEIVED,
        route=route,
        guide_failure=guide_failure,
        guidance_answer=answer,
        created_at=created_at,
        completed_at=created_at if completed else None,
    )


def _fake_customer_guidance(error_code: ErrorCode) -> Answer:
    """코드 표 행(`error:<설비>:<코드>`)을 인용하는 가짜 셀프 가이드를 만든다."""
    citations = [
        Citation(
            kind=CitationKind.MANUAL,
            ref_id=f"error:{error_code.equipment}:{error_code.code}",
            locator=f"{_MOCK} {error_code.manual_id} 1쪽",
        )
    ]
    return Answer(
        verdict=Verdict.ANSWERED,
        steps=[
            AnswerStep(text=f"{_MOCK} 설비를 안전하게 정지하고 {error_code.name} 표시를 확인합니다.", citations=citations),
            AnswerStep(text=f"{_MOCK} 매뉴얼 절차에 따라 리셋 후 재가동합니다.", citations=citations),
        ],
        notice=None,
    )


def _fake_engineer_answer(store: _Store, case: ServiceCase, question: str) -> Answer:
    """질문 키워드에 따라 가짜 엔지니어 답변을 만든다."""
    # 보류 답변은 단계 없이 안내 문구만
    if "[insufficient]" in question:
        return Answer(verdict=Verdict.INSUFFICIENT_EVIDENCE, steps=[], notice=f"{_MOCK} 근거가 부족해 답할 수 없습니다.")
    # 에러코드 표 인용
    citations = [
        Citation(
            kind=CitationKind.MANUAL,
            ref_id=f"error:{case.equipment}:{case.code}",
            locator=f"{_MOCK} 에러코드 표 {case.code}",
        )
    ]
    # 같은 설비·코드의 시드 이력이 있으면 그것도 인용
    similar = next(
        (
            c
            for c in store.cases.values()
            if c.source == Source.SEED and c.equipment == case.equipment and c.code == case.code
        ),
        None,
    )
    if similar is not None:
        citations.append(
            Citation(
                kind=CitationKind.HISTORY,
                ref_id=f"history:{similar.case_id}",
                locator=f"{similar.case_id} {similar.code}",
            )
        )
    return Answer(
        verdict=Verdict.ANSWERED,
        steps=[AnswerStep(text=f"{_MOCK} 에러코드 표와 과거 이력을 기준으로 점검합니다.", citations=citations)],
        notice=None,
    )


def _add_chat_turn(store: _Store, case: ServiceCase, question: str, answer: Answer, created_at: datetime) -> None:
    """대화 한 턴을 저장한다."""
    store.chat_turns.setdefault(case.case_id, []).append(
        ChatTurn(
            turn_id=store.next_turn_id,
            case_id=case.case_id,
            question=question,
            answer=answer,
            created_at=created_at,
        )
    )
    store.next_turn_id += 1
    # 첫 질문이면 received → in_progress
    if case.status == CaseStatus.RECEIVED:
        store.cases[case.case_id] = case.model_copy(update={"status": CaseStatus.IN_PROGRESS})


def _parse_enum(enum_type, value, label: str):
    """값을 열거형으로 바꾼다. 안 되면 ValidationError."""
    try:
        return enum_type(value)
    except ValueError:
        raise ValidationError(f"허용되지 않은 {label} 값입니다: {value}") from None


def _filter_by_status(cases, status: CaseStatus | None) -> list[ServiceCase]:
    """상태가 주어지면 그 상태의 신고만 남긴다."""
    if status is None:
        return list(cases)
    status = _parse_enum(CaseStatus, status, "상태")
    return [c for c in cases if c.status == status]


def _latest_first(cases: list[ServiceCase]) -> list[ServiceCase]:
    """신고 시각 최신순으로 정렬한다. 시각이 같으면 나중에 저장한 신고가 먼저 온다.

    Windows는 시각이 약 15ms 단위로 찍혀 연달아 저장한 신고의 시각이 같을 수 있다.
    저장 순서(저장소에 들어간 순서)를 두 번째 기준으로 써서 결과가 운영체제에 따라 달라지지 않게 한다.
    """
    ordered = sorted(enumerate(cases), key=lambda pair: (pair[1].created_at, pair[0]), reverse=True)
    return [case for _, case in ordered]


def _get_case(store: _Store, case_id: str) -> ServiceCase:
    """접수번호로 신고를 찾는다. 없으면 NotFoundError."""
    case = store.cases.get(case_id)
    if case is None:
        raise NotFoundError(f"접수번호 {case_id}을(를) 찾을 수 없습니다.")
    return case


def _to_detail(store: _Store, case: ServiceCase) -> CaseDetail:
    """신고를 엔지니어용 상세로 묶는다."""
    return CaseDetail(
        service_case=case,
        error_code=store.error_codes[(case.equipment, case.code)],
        resolution=store.resolutions.get(case.case_id),
    )


def _to_customer_result(store: _Store, case: ServiceCase) -> CustomerCaseResult:
    """신고를 고객 공개 필드로 바꾼다."""
    resolution = store.resolutions.get(case.case_id)
    return CustomerCaseResult(
        case_id=case.case_id,
        equipment=case.equipment,
        code=case.code,
        error_name=store.error_codes[(case.equipment, case.code)].name,
        memo=case.memo,
        status=case.status,
        created_at=case.created_at,
        guidance_answer=case.guidance_answer,
        action=resolution.action if resolution else None,  # 조치·결과만 공개, 원인은 뺌
        result=resolution.result if resolution else None,
    )


def _error_code_text(error_code: ErrorCode) -> str:
    """에러코드 표 한 행을 원문 형태로 만든다."""
    return (
        f"{error_code.equipment} {error_code.code} {error_code.name}\n"
        f"심각도: {error_code.severity} / 조치 구분: {error_code.action_owner}\n"
        f"원인: {error_code.cause}\n조치: {error_code.action}"
    )


def install() -> None:
    """`service`의 공개 함수를 이 모듈의 같은 이름 함수로 바꿔 끼운다. `app.py --fake`에서 한 번 부른다.

    화면은 `service.<함수>`를 부를 때마다 이름으로 찾으므로, 바꿔 끼운 뒤에는 모든 화면이 가짜 자료로 동작한다.

    Returns:
        None: 이후 `service`의 공개 함수 호출이 메모리 자료로 처리된다.
    """
    from mpilot import service

    for name in public_function_names(service):
        setattr(service, name, globals()[name])


def public_function_names(module) -> list[str]:
    """모듈에 정의된 공개 함수(이름이 `_`로 시작하지 않는 함수) 이름을 돌려준다.

    Args:
        module: 살펴볼 모듈(`service`).

    Returns:
        list[str]: 그 모듈에서 정의한 공개 함수 이름 목록. 다른 모듈에서 가져온 함수는 뺀다.
    """
    import inspect

    return [
        name for name, fn in inspect.getmembers(module, inspect.isfunction)
        if fn.__module__ == module.__name__ and not name.startswith("_")
    ]
