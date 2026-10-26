"""UI 개발용 가짜 service: DB·Chroma·LLM 없이 `service`와 같은 함수를 메모리 자료로 흉내 낸다.

역할:
    DB·RAG 파트가 구현되기 전에 화면 7개를 띄워 볼 수 있게 `service.py`의 공개 함수 13개를
    같은 시그니처로 제공한다. 진짜 service로 바꿀 때 UI 코드를 고치지 않아도 되게 하는 것이 목적이다.

책임:
    - 에러코드 표는 `data/raw/error_code_table.csv`의 설비·코드·에러명·심각도·조치 구분을 그대로 쓴다.
      원자료에 없는 열(원인·조치·출처·허용 절차 ID)은 `(목)` 표시를 단 가짜 값으로 채운다.
    - 시드 이력은 `data/raw/*_history_dummy.csv` 200건을 `completed` + `Resolution`으로 올린다.
    - 신규 신고는 spec §2.10 허용 조합(셀프 가이드, 엔지니어 처리, `held`, `generation_failed`)을
      한 건 이상씩 미리 만들어 둔다.
    - 경로 결정과 완료 건 차단은 `models`의 규칙 함수를 그대로 써서 진짜 service와 같은 분기를 낸다.
    - 상태는 프로세스 메모리에만 둔다. 앱을 다시 시작하거나 `reset_fake_data`를 부르면 처음으로 돌아간다.

화면 시험용 키워드(메모·질문·조치 결과에 넣으면 해당 분기를 낸다):
    - `submit_case` 메모의 `[held]` → 보류(`held`), `[fail]` → 생성 실패(`generation_failed`).
    - `send_chat_question` 질문의 `[insufficient]` → 근거 부족, `[na]` → 적용 대상 아님,
      `[fail]` → `DependencyError`(대화를 저장하지 않는다).
    - `confirm_resolution` 결과의 `[fail]` → 저장은 하고 검색 반영 실패(`False`)를 돌려준다.

로그인:
    `set_config`로 설정을 받았으면 그 데모 계정으로 확인한다. 받지 않았으면 ID `customer`·`engineer`에
    ID와 같은 비밀번호를 받는다(가짜 계정이며 실제 비밀 정보가 아니다).

의존: `config`, `models`(진짜 service가 쓰는 db·rag는 부르지 않는다).
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
    Role,
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

_RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
_TIME_FORMAT = "%Y-%m-%d %H:%M"
_MOCK = "(목)"
_DEFAULT_ACCOUNTS = {"customer": Role.CUSTOMER, "engineer": Role.ENGINEER}


@dataclass
class _Store:
    """가짜 service의 메모리 저장소."""

    error_codes: dict[tuple[Equipment, str], ErrorCode]
    cases: dict[str, ServiceCase]
    resolutions: dict[str, Resolution] = field(default_factory=dict)
    chat_turns: dict[str, list[ChatTurn]] = field(default_factory=dict)
    next_turn_id: int = 1


_config: AppConfig | None = None
_store: _Store | None = None


# ---------------------------------------------------------------------------
# 공개 함수(service.py와 같은 시그니처)
# ---------------------------------------------------------------------------


def set_config(config: AppConfig) -> None:
    """앱 설정을 보관한다. 가짜 service는 데모 계정만 쓴다.

    Args:
        config: `config.load_config`가 만든 앱 설정.

    Returns:
        None: 모듈 안에 설정을 보관한다.
    """
    global _config
    _config = config


def login(login_id: str, password: str) -> Role:
    """데모 계정을 확인해 역할을 알려준다.

    Args:
        login_id: 입력한 로그인 ID.
        password: 입력한 비밀번호.

    Returns:
        Role: 계정에 정해진 역할.

    Raises:
        ValidationError: 계정이 없거나 비밀번호가 틀렸을 때.
    """
    if _config is not None:
        account = _config.demo_accounts.get(login_id)
        if account is not None and account.password.get_secret_value() == password:
            return account.role
    elif login_id in _DEFAULT_ACCOUNTS and password == login_id:
        return _DEFAULT_ACCOUNTS[login_id]
    raise ValidationError("로그인 정보가 올바르지 않습니다.")


def list_customer_cases(status: CaseStatus | None = None) -> list[CustomerCaseResult]:
    """고객이 접수한 신고(`source=reported`)를 공개 필드로 최신순 조회한다.

    Args:
        status: 지정하면 그 진행 상태의 신고만 돌려준다. `None`이면 모두.

    Returns:
        list[CustomerCaseResult]: 공개 필드만 담은 신고 목록(최신순).

    Raises:
        ValidationError: 허용되지 않은 상태 값일 때.
    """
    store = _get_store()
    cases = [c for c in _filter_by_status(store.cases.values(), status) if c.source == Source.REPORTED]
    return [_to_customer_result(store, c) for c in _latest_first(cases)]


def get_customer_case(case_id: str) -> CustomerCaseResult:
    """고객 공개 신고 상세를 조회한다.

    Args:
        case_id: 접수번호.

    Returns:
        CustomerCaseResult: 공개 필드만 담은 신고 상세.

    Raises:
        NotFoundError: 접수번호가 없거나 시드 이력일 때.
    """
    store = _get_store()
    case = _get_case(store, case_id)
    if case.source != Source.REPORTED:
        raise NotFoundError(f"접수번호 {case_id}을(를) 찾을 수 없습니다.")
    return _to_customer_result(store, case)


def list_error_codes(equipment: Equipment) -> list[ErrorCode]:
    """설비별 에러코드 선택지를 조회한다.

    Args:
        equipment: 고객이 고른 설비.

    Returns:
        list[ErrorCode]: 그 설비의 에러코드 표 행(원자료 순서).

    Raises:
        ValidationError: 허용되지 않은 설비일 때.
    """
    equipment = _parse_enum(Equipment, equipment, "설비")
    return [e for (eq, _), e in _get_store().error_codes.items() if eq == equipment]


def submit_case(data: CaseInput) -> str:
    """신고를 접수하고 처리 경로를 정한 뒤 접수번호를 반환한다.

    경로 판정은 `models`의 규칙 함수를 쓰고, 가이드 생성은 메모 키워드(`[held]`, `[fail]`)로 흉내 낸다.

    Args:
        data: 고객이 제출한 설비·에러코드·증상 메모.

    Returns:
        str: 새로 발급한 접수번호(`SC-<UUID 앞 8자리>`).

    Raises:
        ValidationError: 미등록 코드일 때.
    """
    store = _get_store()
    error_code = store.error_codes.get((data.equipment, data.code))
    if error_code is None:
        raise ValidationError(f"{data.equipment} {data.code}은(는) 등록되지 않은 코드입니다.")
    case_id = f"SC-{uuid.uuid4().hex[:8]}"
    store.cases[case_id] = _new_reported_case(case_id, error_code, data.memo, datetime.now())
    return case_id


def list_engineer_cases(status: CaseStatus | None = None) -> list[CaseDetail]:
    """시드 이력과 신규 신고를 모두 최신순으로 조회한다.

    Args:
        status: 지정하면 그 진행 상태의 신고만 돌려준다. `None`이면 모두.

    Returns:
        list[CaseDetail]: 신고·에러코드·조치 기록 목록(최신순).

    Raises:
        ValidationError: 허용되지 않은 상태 값일 때.
    """
    store = _get_store()
    cases = _latest_first(_filter_by_status(store.cases.values(), status))
    return [_to_detail(store, c) for c in cases]


def get_engineer_case(case_id: str) -> CaseDetail:
    """신고·에러코드·현장 조치를 조회한다.

    Args:
        case_id: 접수번호.

    Returns:
        CaseDetail: 신고 건, 신고한 에러코드 행, 조치 기록(있으면).

    Raises:
        NotFoundError: 접수번호가 없을 때.
    """
    store = _get_store()
    return _to_detail(store, _get_case(store, case_id))


def list_chat_turns(case_id: str) -> list[ChatTurn]:
    """신고의 대화 이력을 오래된 순으로 조회한다.

    Args:
        case_id: 접수번호.

    Returns:
        list[ChatTurn]: 오래된 순 대화 목록. 대화가 없으면 빈 목록.

    Raises:
        NotFoundError: 접수번호가 없을 때.
    """
    store = _get_store()
    _get_case(store, case_id)
    return list(store.chat_turns.get(case_id, []))


def send_chat_question(case_id: str, question: str) -> None:
    """가짜 답변을 만들어 대화를 저장한다. 첫 질문이면 신고가 `in_progress`가 된다.

    Args:
        case_id: 접수번호.
        question: 엔지니어의 질문. 앞뒤 공백 제거 후 1~2,000자.

    Returns:
        None: 결과는 저장된 대화로 남는다.

    Raises:
        ValidationError: 질문이 비었거나 2,000자를 넘을 때.
        NotFoundError: 접수번호가 없을 때.
        InvalidStateError: 이미 완료된 건일 때('이미 완료').
        DependencyError: 질문에 `[fail]`이 있을 때(모델 장애 흉내).
    """
    store = _get_store()
    case = _get_case(store, case_id)
    raise_if_case_completed(case)
    question = question.strip()
    if not 1 <= len(question) <= 2000:
        raise ValidationError("질문은 1~2,000자로 입력해 주세요.")
    if "[fail]" in question:
        raise DependencyError(f"{_MOCK} 답변 생성에 실패했습니다.")
    _add_chat_turn(store, case, question, _fake_engineer_answer(store, case, question), datetime.now())


def confirm_resolution(case_id: str, record: Resolution) -> tuple[CaseDetail, bool]:
    """조치 결과를 확정해 신고를 완료한다.

    Args:
        case_id: 접수번호.
        record: 엔지니어가 쓴 원인·실제 조치·결과.

    Returns:
        tuple[CaseDetail, bool]: 완료된 신고와 조치 기록, 검색 반영 성공 여부.
            결과에 `[fail]`이 있으면 반영 실패(`False`)를 흉내 낸다.

    Raises:
        NotFoundError: 접수번호가 없을 때.
        InvalidStateError: 이미 완료된 건일 때('이미 완료').
    """
    store = _get_store()
    case = _get_case(store, case_id)
    raise_if_case_completed(case)
    store.resolutions[case_id] = record
    store.cases[case_id] = case.model_copy(
        update={"status": CaseStatus.COMPLETED, "completed_at": datetime.now()}
    )
    return _to_detail(store, store.cases[case_id]), "[fail]" not in record.result


def get_citation_text(citation: Citation) -> str:
    """인용 출처의 원문을 돌려준다. 매뉴얼 본문은 가짜 문장이다.

    Args:
        citation: 답변 단계가 가리키는 근거.

    Returns:
        str: 그 문서의 원문.

    Raises:
        NotFoundError: 그 ID의 문서가 없을 때.
    """
    store = _get_store()
    prefix, _, rest = citation.ref_id.partition(":")
    if prefix == "error":
        equipment, _, code = rest.partition(":")
        error_code = store.error_codes.get((equipment, code))
        if error_code is not None:
            return _error_code_text(error_code)
    elif prefix == "history":
        resolution = store.resolutions.get(rest)
        if resolution is not None:
            case = store.cases[rest]
            return (
                f"[{case.case_id}] {case.equipment} {case.code}\n증상: {case.memo or '-'}\n"
                f"원인: {resolution.cause}\n조치: {resolution.action}\n결과: {resolution.result}"
            )
    elif prefix == "manual" and rest:
        return f"{_MOCK} 매뉴얼 원문({citation.locator}). 실제 원문은 DB 파트 구현 뒤에 보입니다."
    raise NotFoundError(f"{citation.ref_id}의 원문을 찾을 수 없습니다.")


def prepare_search_index() -> None:
    """가짜 자료를 준비한다. 색인은 만들지 않는다.

    Returns:
        None: 메모리 자료가 준비된다.
    """
    _get_store()


def reset_fake_data() -> None:
    """메모리 자료를 버린다. 다음 호출 때 원자료에서 다시 만든다(테스트용).

    Returns:
        None
    """
    global _store
    _store = None


# ---------------------------------------------------------------------------
# 자료 만들기
# ---------------------------------------------------------------------------


def _get_store() -> _Store:
    """처음 부를 때 원자료를 읽어 저장소를 만든다."""
    global _store
    if _store is None:
        _store = _build_store()
    return _store


def _build_store() -> _Store:
    """에러코드 표·시드 이력·예시 신고로 저장소를 만든다."""
    store = _Store(error_codes=_load_error_codes(), cases={})
    _load_seed_history(store)
    _add_sample_reported_cases(store)
    return store


def _read_csv(path: Path) -> list[dict[str, str]]:
    """BOM이 있는 UTF-8 CSV를 읽는다."""
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _load_error_codes() -> dict[tuple[Equipment, str], ErrorCode]:
    """에러코드 표를 읽는다. 원자료에 없는 열은 `(목)` 값으로 채운다.

    고객사 조치 코드에는 가짜 허용 절차 ID를 하나 붙여 셀프 가이드 경로를 시험할 수 있게 한다.
    """
    codes: dict[tuple[Equipment, str], ErrorCode] = {}
    for row in _read_csv(_RAW_DIR / "error_code_table.csv"):
        equipment = Equipment(row["equipment"])
        action_owner = ActionOwner(row["action_owner"])
        manual_id = f"mock-{equipment.name.lower()}"
        refs = [f"manual:{manual_id}:1:1"] if action_owner == ActionOwner.CUSTOMER else []
        codes[(equipment, row["code"])] = ErrorCode(
            equipment=equipment,
            code=row["code"],
            name=row["name"],
            severity=Severity(row["severity"]),
            action_owner=action_owner,
            cause=f"{_MOCK} 원인 미정",
            action=f"{_MOCK} 조치 미정",
            manual_id=manual_id,
            page=1,
            section=_MOCK,
            customer_procedure_ref_ids=refs,
            required_safety_rules=[],
        )
    return codes


def _load_seed_history(store: _Store) -> None:
    """설비별 이력 더미 CSV를 완료된 시드 신고와 조치 기록으로 올린다."""
    for path in sorted(_RAW_DIR.glob("*_history_dummy.csv")):
        for row in _read_csv(path):
            case_id = row["이력ID"]
            store.cases[case_id] = ServiceCase(
                case_id=case_id,
                equipment=Equipment(row["모델명"]),
                code=row["에러 코드"],
                memo=row["현장 증상"].strip() or None,
                source=Source.SEED,
                status=CaseStatus.COMPLETED,
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
    """허용 조합(spec §2.10)마다 고객 신고를 한 건 이상 만든다."""
    customer = _first_code(store, lambda e: is_self_guide_candidate(e))
    engineer = _first_code(store, lambda e: not is_self_guide_candidate(e))
    now = datetime.now().replace(second=0, microsecond=0)
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

    in_progress = store.cases["SC-mock0003"]
    question = f"{_MOCK} 같은 증상의 과거 조치가 있나요?"
    _add_chat_turn(store, in_progress, question, _fake_engineer_answer(store, in_progress, question), now)

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
    """경로 결정(spec §2.10)을 흉내 내 신규 신고를 만든다."""
    route, guide_failure, answer = Route.SUPPORT, None, None
    # 허용 절차가 없는 코드의 guide_failure 값은 spec에서 아직 정해지지 않아 NULL로 둔다.
    if is_self_guide_candidate(error_code) and has_customer_procedure(error_code):
        memo_text = memo or ""
        if "[held]" in memo_text:
            guide_failure = GuideFailure.HELD
        elif "[fail]" in memo_text:
            guide_failure = GuideFailure.GENERATION_FAILED
        else:
            route, answer = Route.SELF_GUIDE, _fake_customer_guidance(error_code)
    completed = route == Route.SELF_GUIDE
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
    """허용 절차 ID만 인용하는 가짜 셀프 가이드를 만든다."""
    citations = [
        Citation(kind=CitationKind.MANUAL, ref_id=ref, locator=f"{_MOCK} {error_code.manual_id} 1쪽")
        for ref in error_code.customer_procedure_ref_ids
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
    if "[insufficient]" in question:
        return Answer(verdict=Verdict.INSUFFICIENT_EVIDENCE, steps=[], notice=f"{_MOCK} 근거가 부족해 답할 수 없습니다.")
    if "[na]" in question:
        return Answer(verdict=Verdict.NOT_APPLICABLE, steps=[], notice=f"{_MOCK} 지원 대상 설비가 아닙니다.")
    citations = [
        Citation(
            kind=CitationKind.MANUAL,
            ref_id=f"error:{case.equipment}:{case.code}",
            locator=f"{_MOCK} 에러코드 표 {case.code}",
        )
    ]
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
    """대화를 저장하고, 첫 질문이면 신고를 `in_progress`로 바꾼다."""
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
    if case.status == CaseStatus.RECEIVED:
        store.cases[case.case_id] = case.model_copy(update={"status": CaseStatus.IN_PROGRESS})


# ---------------------------------------------------------------------------
# 조회 보조
# ---------------------------------------------------------------------------


def _parse_enum(enum_type, value, label: str):
    """열거형 값으로 바꾸고, 허용되지 않은 값이면 `ValidationError`를 던진다."""
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
    """신고 시각 최신순으로 정렬한다."""
    return sorted(cases, key=lambda c: c.created_at, reverse=True)


def _get_case(store: _Store, case_id: str) -> ServiceCase:
    """접수번호로 신고를 찾고, 없으면 `NotFoundError`를 던진다."""
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
    """신고를 고객 공개 필드로 바꾼다. 내부 원인과 `guide_failure`는 담지 않는다."""
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
        action=resolution.action if resolution else None,
        result=resolution.result if resolution else None,
    )


def _error_code_text(error_code: ErrorCode) -> str:
    """에러코드 표 한 행을 원문 형태로 만든다."""
    return (
        f"{error_code.equipment} {error_code.code} {error_code.name}\n"
        f"심각도: {error_code.severity} / 조치 구분: {error_code.action_owner}\n"
        f"원인: {error_code.cause}\n조치: {error_code.action}"
    )
