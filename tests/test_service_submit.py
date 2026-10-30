"""신고 접수(`service.submit_case`)의 경로 결정 테스트. DB·RAG는 `monkeypatch`로 대신한다."""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest

from mpilot import service
from mpilot.config import AppConfig
from mpilot.models import (
    ActionOwner,
    Answer,
    AnswerStep,
    CaseInput,
    Citation,
    CitationKind,
    DependencyError,
    Equipment,
    ErrorCode,
    GuideFailure,
    SearchSettings,
    Severity,
    ValidationError,
    Verdict,
)

PROCEDURE_ID = "manual:agv_rev1_1:8:2"  # 가짜 안내가 인용하는 근거 ID


def _code(code: str, severity: Severity, owner: ActionOwner) -> ErrorCode:
    return ErrorCode(
        equipment=Equipment.AGV, code=code, name=f"{code} 에러", severity=severity, action_owner=owner,
        cause="원인", action="조치", manual_id="agv_rev1_1", page=8, section="2.4 배터리",
    )


CODES = {
    "SELF": _code("A120", Severity.LOW, ActionOwner.CUSTOMER),  # 셀프 가이드 후보
    "HIGH": _code("A301", Severity.HIGH, ActionOwner.CUSTOMER),  # 심각도 상
    "ENGINEER": _code("A205", Severity.MEDIUM, ActionOwner.ENGINEER),  # 엔지니어 필요
}

ANSWERED = Answer(
    verdict=Verdict.ANSWERED,
    steps=[AnswerStep(text="충전 스테이션으로 복귀시킨다.", citations=[
        Citation(kind=CitationKind.MANUAL, ref_id=PROCEDURE_ID, locator="p.8 · 2.4 배터리"),
    ])],
    notice=None,
)
HELD = Answer(verdict=Verdict.INSUFFICIENT_EVIDENCE, steps=[], notice="근거가 부족합니다.")


@pytest.fixture
def backend(monkeypatch):
    """설정과 DB·pipeline을 가짜로 바꾸고, 저장 호출과 pipeline 호출을 기록한다."""
    monkeypatch.setattr(service, "_config", AppConfig(
        db_url="mysql+pymysql://u:p@localhost/db", openai_api_key="key", chat_model_name="chat",
        embedding_model_name="emb", manuals_dir=Path("m"), codes_path=Path("c"), history_seed_path=Path("h"),
        search=SearchSettings(manual_k=3, history_k=3),
        llm_timeout_seconds=0.2, demo_accounts={},
    ))
    calls = {"saved": [], "guidance": [], "settings": [], "answer": ANSWERED}

    def fetch_error_codes(equipment):
        return list(CODES.values()) if equipment == Equipment.AGV else []

    def run_customer_guidance(context, settings):
        calls["guidance"].append(context)
        calls["settings"].append(settings)
        result = calls["answer"]
        if isinstance(result, Exception):
            raise result
        if result == "slow":
            time.sleep(1)
            return ANSWERED
        return result

    monkeypatch.setattr(service.rdb, "fetch_error_codes", fetch_error_codes)
    monkeypatch.setattr(service.rdb, "save_self_guide_case",
                        lambda case_id, data, answer: calls["saved"].append(("self_guide", case_id, answer)))
    monkeypatch.setattr(service.rdb, "save_support_case",
                        lambda case_id, data, failure: calls["saved"].append(("support", case_id, failure)))
    monkeypatch.setattr(service.pipeline, "run_customer_guidance", run_customer_guidance)
    return calls


def _submit(code_key: str, memo: str | None = None) -> str:
    return service.submit_case(CaseInput(equipment=Equipment.AGV, code=CODES[code_key].code, memo=memo))


def test_case_id_format(backend):
    case_id = _submit("ENGINEER")
    assert re.fullmatch(r"SC-[0-9a-f]{8}", case_id)
    assert backend["saved"][0][1] == case_id  # 돌려준 번호로 저장한다


def test_self_guide_answered_saves_self_guide(backend):
    _submit("SELF", memo="충전 표시등 꺼짐")
    assert backend["saved"] == [("self_guide", backend["saved"][0][1], ANSWERED)]
    assert backend["guidance"][0].memo == "충전 표시등 꺼짐"
    assert backend["settings"] == [service._config.search]  # 검색 설정(건수)을 pipeline에 넘긴다


def test_held_answer_goes_to_engineer(backend):
    backend["answer"] = HELD
    _submit("SELF")
    assert [(kind, failure) for kind, _, failure in backend["saved"]] == [("support", GuideFailure.HELD)]


@pytest.mark.parametrize("error", [DependencyError("모델 장애"), RuntimeError("예상 못 한 오류")])
def test_generation_error_goes_to_engineer(backend, error):
    backend["answer"] = error
    _submit("SELF")
    assert [(kind, failure) for kind, _, failure in backend["saved"]] == [("support", GuideFailure.GENERATION_FAILED)]


def test_timeout_goes_to_engineer(backend):
    backend["answer"] = "slow"
    started = time.monotonic()
    _submit("SELF")
    assert time.monotonic() - started < 0.9  # 제한 시간(0.2초)이 지나면 기다리지 않는다
    assert [(kind, failure) for kind, _, failure in backend["saved"]] == [("support", GuideFailure.GENERATION_FAILED)]


@pytest.mark.parametrize("code_key", ["HIGH", "ENGINEER"])
def test_non_candidate_skips_guidance(backend, code_key):
    _submit(code_key)
    assert backend["guidance"] == []  # 표만으로 정한다. pipeline을 부르지 않는다
    assert [(kind, failure) for kind, _, failure in backend["saved"]] == [("support", None)]


def test_unregistered_code_raises(backend):
    with pytest.raises(ValidationError):
        service.submit_case(CaseInput(equipment=Equipment.AGV, code="A999", memo=None))
    assert backend["saved"] == []


def test_similar_code_is_not_substituted(backend):
    with pytest.raises(ValidationError):
        service.submit_case(CaseInput(equipment=Equipment.SRM, code="A120", memo=None))  # 다른 설비의 같은 코드


def test_db_save_error_propagates(backend, monkeypatch):
    def fail(case_id, data, failure):
        raise DependencyError("DB 장애")

    monkeypatch.setattr(service.rdb, "save_support_case", fail)
    with pytest.raises(DependencyError):
        _submit("ENGINEER")


def test_list_error_codes_reads_db(backend):
    assert [c.code for c in service.list_error_codes(Equipment.AGV)] == [c.code for c in CODES.values()]
    assert service.list_error_codes(Equipment.RTV) == []
