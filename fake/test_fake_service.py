"""가짜 service 테스트: service.py와 같은 계약, 원자료 값 유지, 허용 조합·상태 전이(spec §2.10)."""

import csv
import inspect
from pathlib import Path

import pytest

from fake import fake_service
from mpilot import service
from mpilot.models import (
    CaseInput,
    CaseStatus,
    Citation,
    CitationKind,
    DependencyError,
    Equipment,
    GuideFailure,
    InvalidStateError,
    NotFoundError,
    Resolution,
    Route,
    Source,
    ValidationError,
    Verdict,
    is_self_guide_candidate,
)

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"


@pytest.fixture(autouse=True)
def fresh_data():
    fake_service.reset_fake_data()
    fake_service._config = None
    yield
    fake_service.reset_fake_data()


def first_code(candidate: bool):
    return next(
        e
        for eq in Equipment
        for e in fake_service.list_error_codes(eq)
        if is_self_guide_candidate(e) is candidate
    )


def open_case_id() -> str:
    code = first_code(candidate=False)
    return fake_service.submit_case(CaseInput(equipment=code.equipment, code=code.code, memo=None))


def resolution(result: str = "정상 동작 확인") -> Resolution:
    return Resolution(cause="원인", action="조치", result=result)


# 계약 -------------------------------------------------------------------------


def test_public_functions_match_service_signatures():
    # service의 공개 함수(spec §2.3.3)가 모두 같은 시그니처로 있어야 install로 바꿔 끼울 수 있다.
    names = fake_service.public_function_names(service)
    assert len(names) == 12  # CHG-01로 login이 빠졌다
    for name in names:
        assert inspect.signature(getattr(fake_service, name)) == inspect.signature(getattr(service, name))


def test_install_swaps_service_functions(monkeypatch):
    names = fake_service.public_function_names(service)
    for name in names:  # 테스트가 끝나면 원래 함수로 되돌린다
        monkeypatch.setattr(service, name, getattr(service, name))

    fake_service.install()

    for name in names:
        assert getattr(service, name) is getattr(fake_service, name)


# 원자료 ------------------------------------------------------------------------


def test_error_codes_keep_raw_severity_and_action_owner():
    with (RAW_DIR / "error_code_table.csv").open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    codes = {(e.equipment, e.code): e for eq in Equipment for e in fake_service.list_error_codes(eq)}
    assert len(codes) == len(rows)
    for row in rows:
        code = codes[(row["equipment"], row["code"])]
        assert (code.name, code.severity, code.action_owner) == (row["name"], row["severity"], row["action_owner"])


def test_seed_history_is_completed_support_with_resolution():
    seeds = [d for d in fake_service.list_engineer_cases() if d.service_case.source == Source.SEED]
    assert len(seeds) == 200
    for d in seeds:
        assert d.service_case.status == CaseStatus.COMPLETED
        assert d.service_case.route == Route.SUPPORT
        assert d.service_case.guide_failure is None
        assert d.resolution is not None
        assert d.service_case.completed_at >= d.service_case.created_at


def test_sample_cases_cover_allowed_combinations():
    reported = [d.service_case for d in fake_service.list_engineer_cases() if d.service_case.source == Source.REPORTED]
    combos = {(c.route, c.guide_failure, c.status) for c in reported}
    assert (Route.SELF_GUIDE, None, CaseStatus.COMPLETED) in combos
    assert (Route.SUPPORT, None, CaseStatus.RECEIVED) in combos
    assert (Route.SUPPORT, None, CaseStatus.IN_PROGRESS) in combos
    assert (Route.SUPPORT, GuideFailure.HELD, CaseStatus.RECEIVED) in combos
    assert (Route.SUPPORT, GuideFailure.GENERATION_FAILED, CaseStatus.RECEIVED) in combos
    assert (Route.SUPPORT, None, CaseStatus.COMPLETED) in combos


# 고객 화면 ---------------------------------------------------------------------


def test_customer_cases_exclude_seed_and_are_latest_first():
    cases = fake_service.list_customer_cases()
    assert cases
    assert all(fake_service.get_engineer_case(c.case_id).service_case.source == Source.REPORTED for c in cases)
    assert [c.created_at for c in cases] == sorted((c.created_at for c in cases), reverse=True)


def test_same_time_cases_put_later_saved_first():
    # Windows처럼 두 신고의 시각이 같게 찍혀도 나중에 저장한 신고가 먼저 온다
    first, second = open_case_id(), open_case_id()
    store = fake_service._get_store()
    store.cases[second] = store.cases[second].model_copy(update={"created_at": store.cases[first].created_at})
    ids = [d.service_case.case_id for d in fake_service.list_engineer_cases()]
    assert ids.index(second) < ids.index(first)


def test_completed_sample_keeps_chat_history():
    # 완료 예시 0006은 대화 기록을 가진 채 완료되어 있다(완료 건 대화 기록 화면 확인용)
    case = fake_service.get_engineer_case("SC-mock0006")
    assert case.service_case.status == CaseStatus.COMPLETED and case.resolution is not None
    turns = fake_service.list_chat_turns("SC-mock0006")
    assert len(turns) == 2 and all(t.created_at <= case.service_case.completed_at for t in turns)


def test_customer_cannot_open_seed_case():
    seed = next(d for d in fake_service.list_engineer_cases() if d.service_case.source == Source.SEED)
    with pytest.raises(NotFoundError):
        fake_service.get_customer_case(seed.service_case.case_id)


def test_customer_result_hides_internal_cause():
    done = next(c for c in fake_service.list_customer_cases(CaseStatus.COMPLETED) if c.action is not None)
    internal = fake_service.get_engineer_case(done.case_id).resolution.cause
    assert internal not in done.model_dump_json()
    assert "guide_failure" not in type(done).model_fields


def test_invalid_status_raises_validation_error():
    with pytest.raises(ValidationError):
        fake_service.list_customer_cases("unknown")


# 신고 접수 ---------------------------------------------------------------------


def test_submit_customer_code_becomes_self_guide():
    code = first_code(candidate=True)
    case_id = fake_service.submit_case(CaseInput(equipment=code.equipment, code=code.code, memo=None))
    assert case_id.startswith("SC-") and len(case_id) == 11
    case = fake_service.get_engineer_case(case_id).service_case
    assert (case.route, case.status, case.guide_failure) == (Route.SELF_GUIDE, CaseStatus.COMPLETED, None)
    cited = {c.ref_id for s in case.guidance_answer.steps for c in s.citations}
    assert cited <= set(code.customer_procedure_ref_ids)


def test_submit_engineer_code_goes_to_support():
    case = fake_service.get_engineer_case(open_case_id()).service_case
    assert (case.route, case.status, case.guide_failure, case.guidance_answer) == (
        Route.SUPPORT,
        CaseStatus.RECEIVED,
        None,
        None,
    )


@pytest.mark.parametrize(("memo", "failure"), [("[held]", GuideFailure.HELD), ("[fail]", GuideFailure.GENERATION_FAILED)])
def test_submit_guide_failure_keywords(memo, failure):
    code = first_code(candidate=True)
    case_id = fake_service.submit_case(CaseInput(equipment=code.equipment, code=code.code, memo=memo))
    case = fake_service.get_engineer_case(case_id).service_case
    assert (case.route, case.guide_failure, case.status) == (Route.SUPPORT, failure, CaseStatus.RECEIVED)


def test_submit_unknown_code_raises_validation_error():
    with pytest.raises(ValidationError):
        fake_service.submit_case(CaseInput(equipment=Equipment.AGV, code="NOPE", memo=None))


# 엔지니어 대화 ------------------------------------------------------------------


def test_first_question_moves_case_to_in_progress():
    case_id = open_case_id()
    fake_service.send_chat_question(case_id, "  원인이 뭔가요?  ")
    turns = fake_service.list_chat_turns(case_id)
    assert [t.question for t in turns] == ["원인이 뭔가요?"]
    assert turns[0].answer.verdict == Verdict.ANSWERED
    assert all(s.citations for s in turns[0].answer.steps)
    assert fake_service.get_engineer_case(case_id).service_case.status == CaseStatus.IN_PROGRESS


def test_held_answers_have_no_steps():
    case_id = open_case_id()
    fake_service.send_chat_question(case_id, "[insufficient]")
    answer = fake_service.list_chat_turns(case_id)[-1].answer
    assert (answer.verdict, answer.steps) == (Verdict.INSUFFICIENT_EVIDENCE, [])
    assert answer.notice


def test_failed_answer_is_not_saved():
    case_id = open_case_id()
    with pytest.raises(DependencyError):
        fake_service.send_chat_question(case_id, "[fail]")
    assert fake_service.list_chat_turns(case_id) == []


@pytest.mark.parametrize("question", ["   ", "가" * 2001])
def test_question_length_is_validated(question):
    with pytest.raises(ValidationError):
        fake_service.send_chat_question(open_case_id(), question)


def test_unknown_case_raises_not_found():
    with pytest.raises(NotFoundError):
        fake_service.list_chat_turns("SC-nothing")


# 조치 확정 ---------------------------------------------------------------------


def test_confirm_resolution_completes_case():
    case_id = open_case_id()
    detail, indexed = fake_service.confirm_resolution(case_id, resolution())
    assert indexed is True
    assert detail.service_case.status == CaseStatus.COMPLETED
    assert detail.service_case.completed_at >= detail.service_case.created_at
    assert detail.resolution == resolution()


def test_confirm_resolution_index_failure_keyword():
    _, indexed = fake_service.confirm_resolution(open_case_id(), resolution("[fail]"))
    assert indexed is False


def test_completed_case_blocks_question_and_resolution():
    case_id = open_case_id()
    fake_service.confirm_resolution(case_id, resolution())
    with pytest.raises(InvalidStateError, match="이미 완료"):
        fake_service.send_chat_question(case_id, "질문")
    with pytest.raises(InvalidStateError, match="이미 완료"):
        fake_service.confirm_resolution(case_id, resolution())


# 인용 원문 ---------------------------------------------------------------------


def test_citation_text_for_each_kind():
    seed = next(d.service_case for d in fake_service.list_engineer_cases() if d.service_case.source == Source.SEED)
    error = Citation(kind=CitationKind.MANUAL, ref_id=f"error:{seed.equipment}:{seed.code}", locator="표")
    history = Citation(kind=CitationKind.HISTORY, ref_id=f"history:{seed.case_id}", locator=seed.case_id)
    manual = Citation(kind=CitationKind.MANUAL, ref_id="manual:mock-agv:1:1", locator="1쪽")
    assert seed.code in fake_service.get_citation_text(error)
    assert seed.case_id in fake_service.get_citation_text(history)
    assert "(목)" in fake_service.get_citation_text(manual)
    with pytest.raises(NotFoundError):
        fake_service.get_citation_text(Citation(kind=CitationKind.HISTORY, ref_id="history:none", locator="-"))
