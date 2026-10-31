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
    GuideFailure,
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


GUIDE = Answer(
    verdict=Verdict.ANSWERED, steps=[AnswerStep(text="커넥터 상태를 확인한다.", citations=[MANUAL])], notice=None,
)


def _detail(status=CaseStatus.IN_PROGRESS, route=Route.SUPPORT, guide_failure=None, guidance_answer=None) -> CaseDetail:
    return CaseDetail(
        service_case=ServiceCase(
            case_id="SC-1", equipment=Equipment.SRM, code="S115", memo=None, source=Source.REPORTED,
            status=status, route=route, guide_failure=guide_failure, guidance_answer=guidance_answer, created_at=_NOW,
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
    for expected in ("접수내역", "SC-1", "SRM", "S115", "리프트 엔코더 편차", "상", "조치 중", "2026-10-28 10:00"):
        assert expected in text


def test_chat_state_of_dumps_turns_to_plain_dicts():
    # gr.render가 상태를 해시하려 하므로, list 필드를 가진 ChatTurn을 그대로 담지 않고 dict로 바꾼다.
    turns = [_turn(1, MANUAL), _turn(2)]
    state = chat.chat_state_of(turns, completed=True)
    assert state["completed"] is True
    assert [t["question"] for t in state["turns"]] == ["질문1", "질문2"]
    assert state["turns"][0]["answer"]["steps"][0]["citations"][0]["ref_id"] == MANUAL.ref_id
    assert chat.chat_state_of([], completed=False) == {"turns": [], "completed": False, "waiting_customer": False}
    assert chat.chat_state_of([], False, waiting_customer=True)["waiting_customer"] is True


def test_resolution_html_only_for_completed():
    text = chat.resolution_html(_detail(CaseStatus.COMPLETED))
    for expected in ("엔코더 케이블 접촉 불량", "커넥터 재체결", "정상 동작 확인"):
        assert expected in text


def test_resolution_html_shows_blank_fields_before_completion():
    # 조치 결과가 없어도 라벨(조치 결과·원인·조치 내용·결과)은 똑같이 보이고 값만 공란이다.
    text = chat.resolution_html(_detail())
    for label in ("조치 결과", "원인", "조치 내용", "결과"):
        assert label in text
    assert "엔코더 케이블 접촉 불량" not in text


def test_citation_source_text_joins_label_and_body_in_order(monkeypatch):
    monkeypatch.setattr(chat.service, "get_citation_text", lambda c: f"원문:{c.ref_id}")
    text = chat.citation_source_text([MANUAL, HISTORY])
    assert chat.common.citation_label(MANUAL) in text and f"원문:{MANUAL.ref_id}" in text
    assert text.index(MANUAL.ref_id) < text.index(HISTORY.ref_id)


def test_can_ask_only_for_open_support_cases():
    assert chat.can_ask(_detail(CaseStatus.RECEIVED))
    assert chat.can_ask(_detail(CaseStatus.IN_PROGRESS))
    assert not chat.can_ask(_detail(CaseStatus.COMPLETED))
    # 고객 확인 전 셀프 가이드 건은 이관(route=support) 전까지 읽기 전용이다.
    assert not chat.can_ask(_detail(CaseStatus.IN_PROGRESS, Route.SELF_GUIDE, None, GUIDE))
    assert not chat.can_ask(_detail(CaseStatus.COMPLETED, Route.SELF_GUIDE, None, GUIDE))
    # [기술지원접수]으로 이관된 건은 조치 중이라 질문할 수 있다.
    assert chat.can_ask(_detail(CaseStatus.IN_PROGRESS, Route.SUPPORT, GuideFailure.UNRESOLVED, GUIDE))


def test_guide_box_html_shows_steps_only_when_guide_was_given():
    assert chat.guide_box_html(_detail()) == ""
    text = chat.guide_box_html(_detail(CaseStatus.IN_PROGRESS, Route.SELF_GUIDE, None, GUIDE))
    assert "고객 안내(셀프 가이드)" in text and "1. 커넥터 상태를 확인한다." in text


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
    column, summary, state, record, box, source_text, question, resolve, send, next_screen = (
        screen["handlers"]["on_screen_change"](chat.SCREEN, "SC-1")
    )
    assert column.visible and "SC-1" in summary and "조치 결과" in record
    assert [t["question"] for t in state["turns"]] == [t.question for t in screen["turns"]]
    assert state["completed"] is False
    assert not box.visible and source_text == ""  # 원문 모달은 새로 불러올 때 닫혀 있다
    assert question.visible and question.interactive and resolve.visible and send.visible and send.interactive
    assert question.placeholder == chat.QUESTION_PLACEHOLDER
    assert next_screen == gr.skip()


def test_enter_completed_case_is_read_only(screen):
    # 질문 입력창·전송 버튼은 화면 구조 유지를 위해 숨기지 않고 비활성화만 한다. [조치 결과 작성]만 숨긴다.
    screen["detail"] = _detail(CaseStatus.COMPLETED)
    _, _, state, record, *_, question, resolve, send, _ = screen["handlers"]["on_screen_change"](chat.SCREEN, "SC-1")
    assert state["completed"] and "커넥터 재체결" in record  # 대화 기록과 조치 결과가 보인다
    assert question.visible and not question.interactive
    assert question.placeholder == chat.QUESTION_PLACEHOLDER_COMPLETED
    assert not resolve.visible
    assert send.visible and not send.interactive


def test_enter_completed_case_without_chat(screen):
    screen["detail"], screen["turns"] = _detail(CaseStatus.COMPLETED), []
    _, _, state, *_ = screen["handlers"]["on_screen_change"](chat.SCREEN, "SC-1")
    assert state == {"turns": [], "completed": True, "waiting_customer": False}


def test_enter_pending_self_guide_is_read_only_with_guide(screen):
    # 고객이 아직 해결 여부를 누르지 않은 셀프 가이드 건: 열리지만 질문은 못 하고 안내 단계가 보인다.
    screen["detail"] = _detail(CaseStatus.IN_PROGRESS, Route.SELF_GUIDE, None, GUIDE)
    screen["turns"] = []
    _, _, state, record, *_, question, resolve, send, _ = screen["handlers"]["on_screen_change"](chat.SCREEN, "SC-1")
    assert state == {"turns": [], "completed": False, "waiting_customer": True}
    assert "커넥터 상태를 확인한다." in record
    assert question.visible and not question.interactive and not resolve.visible and not send.interactive


def test_enter_escalated_case_allows_chat(screen):
    screen["detail"] = _detail(CaseStatus.IN_PROGRESS, Route.SUPPORT, GuideFailure.UNRESOLVED, GUIDE)
    _, _, state, record, *_, question, resolve, send, _ = screen["handlers"]["on_screen_change"](chat.SCREEN, "SC-1")
    assert state["completed"] is False and state["waiting_customer"] is False
    assert "커넥터 상태를 확인한다." in record  # 이관된 건도 고객이 받은 안내를 함께 본다
    assert question.interactive and resolve.visible and send.interactive


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
    summary, state, *_, send = screen["handlers"]["on_after_send"]("SC-1")  # send_btn이 맨 끝
    assert len(state["turns"]) == 2 and send.interactive
