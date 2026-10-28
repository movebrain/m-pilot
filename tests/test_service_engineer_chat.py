"""장애 분석 대화의 서비스(`get_engineer_case`, `list_chat_turns`, `send_chat_question`) 테스트.

DB·RAG는 `monkeypatch`로 대신한다.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mpilot import service
from mpilot.config import AppConfig
from mpilot.models import (
    ActionOwner,
    Answer,
    AnswerStep,
    CaseDetail,
    CaseStatus,
    ChatTurn,
    Citation,
    CitationKind,
    DependencyError,
    Equipment,
    ErrorCode,
    InvalidStateError,
    NotFoundError,
    Route,
    SearchSettings,
    ServiceCase,
    Severity,
    Source,
    ValidationError,
    Verdict,
)

_SETTINGS = SearchSettings(manual_k=3, history_k=3, evidence_threshold=0.5)
_CODE = ErrorCode(
    equipment=Equipment.SRM, code="S115", name="리프트 엔코더 편차", severity=Severity.HIGH,
    action_owner=ActionOwner.ENGINEER, cause="원인", action="조치", manual_id="srm_rev1_2", page=41,
    section="6.3 승강부",
)
ANSWERED = Answer(
    verdict=Verdict.ANSWERED,
    steps=[AnswerStep(text="엔코더 커넥터를 점검한다.", citations=[
        Citation(kind=CitationKind.MANUAL, ref_id="manual:srm_rev1_2:41:1", locator="p.41 · 6.3 승강부"),
    ])],
    notice=None,
)
HELD = Answer(verdict=Verdict.INSUFFICIENT_EVIDENCE, steps=[], notice="근거가 부족합니다.")
_NOW = datetime(2026, 10, 28, 1, 0, tzinfo=timezone.utc)


def _detail(status=CaseStatus.RECEIVED, route=Route.SUPPORT) -> CaseDetail:
    completed = status == CaseStatus.COMPLETED
    return CaseDetail(
        service_case=ServiceCase(
            case_id="SC-1", equipment=Equipment.SRM, code="S115", memo="소음", source=Source.REPORTED,
            status=status, route=route, guide_failure=None,
            guidance_answer=ANSWERED if route == Route.SELF_GUIDE else None,
            created_at=_NOW, completed_at=_NOW if completed else None,
        ),
        error_code=_CODE,
        resolution=None,
    )


PREVIOUS = ChatTurn(turn_id=1, case_id="SC-1", question="이전 질문", answer=HELD, created_at=_NOW)


@pytest.fixture
def backend(monkeypatch):
    """설정과 rdb·pipeline을 가짜로 바꾸고 호출을 기록한다."""
    monkeypatch.setattr(service, "_config", AppConfig(
        db_url="mysql+pymysql://u:p@localhost/db", openai_api_key="key", chat_model_name="chat",
        embedding_model_name="emb", manuals_dir=Path("m"), codes_path=Path("c"), history_seed_path=Path("h"),
        search=_SETTINGS, llm_timeout_seconds=0.2, demo_accounts={},
    ))
    state = {"detail": _detail(), "answer": ANSWERED, "saved": [], "asked": []}

    def fetch_case_detail(case_id):
        if case_id != "SC-1":
            raise NotFoundError(case_id)
        return state["detail"]

    def run_engineer_answer(context, request, settings):
        state["asked"].append((context, request, settings))
        result = state["answer"]
        if isinstance(result, Exception):
            raise result
        if result == "slow":
            time.sleep(1)
            return ANSWERED
        return result

    monkeypatch.setattr(service.rdb, "fetch_case_detail", fetch_case_detail)
    monkeypatch.setattr(service.rdb, "fetch_chat_turns", lambda case_id: [PREVIOUS])
    monkeypatch.setattr(service.rdb, "save_chat_turn",
                        lambda case_id, question, answer: state["saved"].append((case_id, question, answer)))
    monkeypatch.setattr(service.pipeline, "run_engineer_answer", run_engineer_answer)
    return state


# 조회 ---------------------------------------------------------------------------


def test_get_engineer_case_reads_db(backend):
    assert service.get_engineer_case("SC-1") == backend["detail"]


def test_list_chat_turns_reads_db(backend):
    assert service.list_chat_turns("SC-1") == [PREVIOUS]


def test_list_chat_turns_unknown_case_raises(backend):
    with pytest.raises(NotFoundError):
        service.list_chat_turns("SC-x")  # fetch_chat_turns는 빈 목록이라 접수번호부터 확인한다


# 질문 ---------------------------------------------------------------------------


def test_answer_is_saved_with_history(backend):
    service.send_chat_question("SC-1", "  엔코더 점검 순서는?  ")

    [(context, request, settings)] = backend["asked"]
    assert context.error_code == _CODE and context.memo == "소음"  # 신고 코드로 대상 고정
    assert request.question == "엔코더 점검 순서는?" and request.turns == [PREVIOUS]
    assert settings == _SETTINGS
    assert backend["saved"] == [("SC-1", "엔코더 점검 순서는?", ANSWERED)]


def test_insufficient_evidence_is_saved(backend):
    backend["answer"] = HELD
    service.send_chat_question("SC-1", "질문")
    assert backend["saved"] == [("SC-1", "질문", HELD)]


@pytest.mark.parametrize("error", [DependencyError("검증 실패"), RuntimeError("모델 클라이언트 오류")])
def test_failure_is_not_saved(backend, error):
    backend["answer"] = error
    with pytest.raises(DependencyError):
        service.send_chat_question("SC-1", "질문")
    assert backend["saved"] == []


def test_timeout_is_not_saved(backend):
    backend["answer"] = "slow"
    with pytest.raises(DependencyError):
        service.send_chat_question("SC-1", "질문")
    assert backend["saved"] == []


@pytest.mark.parametrize("detail", [
    _detail(status=CaseStatus.COMPLETED),
    _detail(status=CaseStatus.COMPLETED, route=Route.SELF_GUIDE),
])
def test_completed_case_skips_pipeline(backend, detail):
    backend["detail"] = detail
    with pytest.raises(InvalidStateError):
        service.send_chat_question("SC-1", "질문")
    assert backend["asked"] == [] and backend["saved"] == []


@pytest.mark.parametrize("question", ["   ", "가" * 2001])
def test_invalid_question_raises(backend, question):
    with pytest.raises(ValidationError):
        service.send_chat_question("SC-1", question)
    assert backend["asked"] == []


def test_question_length_boundary_passes(backend):
    service.send_chat_question("SC-1", "가" * 2000)
    assert len(backend["saved"]) == 1


def test_unknown_case_raises(backend):
    with pytest.raises(NotFoundError):
        service.send_chat_question("SC-x", "질문")
    assert backend["asked"] == []
