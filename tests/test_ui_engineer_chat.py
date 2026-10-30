"""장애 분석 대화 화면: 표시 변환 함수와 화면 핸들러 테스트. 서비스는 `monkeypatch`로 대신한다."""

from __future__ import annotations

from datetime import datetime, timezone

import gradio as gr
import pytest

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
    NotFoundError,
    Role,
    Resolution,
    Route,
    ServiceCase,
    Severity,
    Source,
    Verdict,
)
from mpilot.ui import engineer_chat as chat

_CODE = ErrorCode(
    equipment=Equipment.SRM, code="S115", name="리프트 엔코더 편차", severity=Severity.HIGH,
    action_owner=ActionOwner.ENGINEER, cause="원인", action="조치", manual_id="srm_rev1_2", page=41,
    section="6.3 승강부",
)
_NOW = datetime(2026, 10, 28, 1, 0, tzinfo=timezone.utc)
MANUAL = Citation(kind=CitationKind.MANUAL, ref_id="manual:srm_rev1_2:41:1", locator="p.41 · 6.3 승강부")
HISTORY = Citation(kind=CitationKind.HISTORY, ref_id="history:SEED-1", locator="SEED-1")


RESOLUTION = Resolution(cause="엔코더 케이블 접촉 불량", action="커넥터 재체결", result="정상 동작 확인")


def _detail(status=CaseStatus.IN_PROGRESS) -> CaseDetail:
    return CaseDetail(
        service_case=ServiceCase(
            case_id="SC-1", equipment=Equipment.SRM, code="S115", memo=None, source=Source.REPORTED,
            status=status, route=Route.SUPPORT, guide_failure=None, guidance_answer=None, created_at=_NOW,
            completed_at=_NOW if status == CaseStatus.COMPLETED else None,
        ),
        error_code=_CODE,
        resolution=RESOLUTION if status == CaseStatus.COMPLETED else None,
    )


def _turn(no: int, *citations: Citation) -> ChatTurn:
    if citations:
        answer = Answer(verdict=Verdict.ANSWERED, steps=[AnswerStep(text=f"단계{no}", citations=list(citations))],
                        notice=None)
    else:
        answer = Answer(verdict=Verdict.INSUFFICIENT_EVIDENCE, steps=[], notice="근거가 부족합니다.")
    return ChatTurn(turn_id=no, case_id="SC-1", question=f"질문{no}", answer=answer, created_at=_NOW)


# 표시 변환 --------------------------------------------------------------------------


def test_case_summary_shows_case_fields():
    text = chat.case_summary_html(_detail())
    for expected in ("SC-1", "SRM", "S115", "리프트 엔코더 편차", "상", "조치 중", "2026-10-28 10:00"):
        assert expected in text


def test_chat_markdown_empty_and_order():
    assert chat.chat_markdown([]) == chat.EMPTY_CHAT_NOTICE
    assert chat.chat_markdown([], completed=True) == chat.COMPLETED_NO_CHAT_NOTICE  # 완료 건은 질문 안내를 하지 않는다
    text = chat.chat_markdown([_turn(1, MANUAL), _turn(2)])
    assert text.index("질문1") < text.index("질문2")
    assert "근거가 부족합니다." in text  # 근거 부족은 안내만 보인다


def test_resolution_html_only_for_completed():
    text = chat.resolution_html(_detail(CaseStatus.COMPLETED))
    for expected in ("엔코더 케이블 접촉 불량", "커넥터 재체결", "정상 동작 확인"):
        assert expected in text
    assert chat.NO_RESOLUTION_NOTICE in chat.resolution_html(_detail())


def test_citations_are_unique_in_order():
    turns = [_turn(1, MANUAL), _turn(2), _turn(3, HISTORY, MANUAL)]
    assert chat.citations_of(turns) == [MANUAL, HISTORY]


def test_can_ask_only_before_completed():
    assert chat.can_ask(_detail(CaseStatus.RECEIVED))
    assert chat.can_ask(_detail(CaseStatus.IN_PROGRESS))
    assert not chat.can_ask(_detail(CaseStatus.COMPLETED))


# 화면 핸들러 ------------------------------------------------------------------------


@pytest.fixture
def screen(monkeypatch):
    """화면을 조립하고 핸들러와 서비스 호출 기록을 꺼낸다."""
    state = {"detail": _detail(), "turns": [_turn(1, MANUAL)], "sent": [], "send_error": None, "warnings": []}

    def get_engineer_case(case_id):
        if case_id != "SC-1":
            raise NotFoundError(case_id)
        return state["detail"]

    def send_chat_question(case_id, question):
        if state["send_error"]:
            raise state["send_error"]
        state["sent"].append((case_id, question))

    monkeypatch.setattr(chat.service, "get_engineer_case", get_engineer_case)
    monkeypatch.setattr(chat.service, "list_chat_turns", lambda case_id: state["turns"])
    monkeypatch.setattr(chat.service, "send_chat_question", send_chat_question)
    monkeypatch.setattr(chat.service, "get_citation_text", lambda citation: f"원문:{citation.ref_id}")
    monkeypatch.setattr(chat.gr, "Warning", state["warnings"].append)
    with gr.Blocks() as demo:
        chat.build_engineer_chat_screen(gr.State(Role.ENGINEER), gr.State(None), gr.State(chat.common.SCREEN_ENGINEER_LIST))
    state["handlers"] = {f.fn.__name__: f.fn for f in demo.fns.values()}
    return state


def test_enter_loads_case_and_chat(screen):
    column, summary, text, record, pick, box, found, question, resolve, send, next_screen = (
        screen["handlers"]["on_screen_change"](chat.SCREEN, "SC-1")
    )
    assert column.visible and "SC-1" in summary and "질문1" in text and chat.NO_RESOLUTION_NOTICE in record
    assert pick.visible and found == [MANUAL]
    assert question.visible and question.interactive and resolve.visible and send.visible and send.interactive
    assert next_screen == gr.skip()


def test_enter_completed_case_is_read_only(screen):
    screen["detail"] = _detail(CaseStatus.COMPLETED)
    _, _, text, record, *_, question, resolve, send, _ = screen["handlers"]["on_screen_change"](chat.SCREEN, "SC-1")
    assert "질문1" in text and "커넥터 재체결" in record  # 대화 기록과 조치 결과가 보인다
    assert not question.visible and not resolve.visible and not send.visible


def test_enter_completed_case_without_chat(screen):
    screen["detail"], screen["turns"] = _detail(CaseStatus.COMPLETED), []
    _, _, text, *_ = screen["handlers"]["on_screen_change"](chat.SCREEN, "SC-1")
    assert text == chat.COMPLETED_NO_CHAT_NOTICE


def test_enter_unknown_case_goes_back(screen):
    result = screen["handlers"]["on_screen_change"](chat.SCREEN, "SC-x")
    assert result[-1] == chat.common.SCREEN_ENGINEER_LIST
    assert screen["warnings"] == [chat.common.error_message(NotFoundError())]


def test_hidden_screen_skips_reload(screen):
    result = screen["handlers"]["on_screen_change"](chat.common.SCREEN_ENGINEER_LIST, "SC-1")
    assert not result[0].visible and all(r == gr.skip() for r in result[1:])


def test_send_clears_question_on_success(screen):
    assert screen["handlers"]["on_send"]("SC-1", "질문") == ""
    assert screen["sent"] == [("SC-1", "질문")]


def test_send_failure_keeps_question_and_shows_fixed_text(screen):
    screen["send_error"] = DependencyError("모델 원문 오류")
    assert screen["handlers"]["on_send"]("SC-1", "질문") == gr.skip()
    assert screen["warnings"] == [chat.common.error_message(DependencyError())]


def test_after_send_reloads_and_unlocks(screen):
    screen["turns"] = [_turn(1, MANUAL), _turn(2)]
    summary, text, *_, send = screen["handlers"]["on_after_send"]("SC-1")  # send_btn이 맨 끝
    assert "질문2" in text and send.interactive


def test_source_pick_shows_original(screen):
    box, text = screen["handlers"]["on_source_pick"]("0", [MANUAL])
    assert isinstance(box, gr.Column) and box.visible and text == f"원문:{MANUAL.ref_id}"
    box, text = screen["handlers"]["on_source_pick"](None, [MANUAL])
    assert not box.visible and text == ""
