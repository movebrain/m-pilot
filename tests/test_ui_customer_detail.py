"""고객 신고 상세 화면의 표시 변환 테스트."""

from __future__ import annotations

from datetime import datetime, timezone

import gradio as gr

from mpilot.models import (
    Answer,
    AnswerStep,
    CaseStatus,
    Citation,
    CitationKind,
    CustomerCaseResult,
    Equipment,
    Role,
)
from mpilot.ui import common
from mpilot.ui import customer_report_detail as detail

C1 = Citation(kind=CitationKind.MANUAL, ref_id="manual:agv_rev1_1:8:2", locator="p.8 · 2.4 배터리")
C2 = Citation(kind=CitationKind.MANUAL, ref_id="manual:agv_rev1_1:8:3", locator="p.8 · 2.4 배터리")


def _case(**kw) -> CustomerCaseResult:
    base = dict(
        case_id="SC-1", equipment=Equipment.AGV, code="A120", error_name="배터리 저전압", memo=None,
        status=CaseStatus.RECEIVED, created_at=datetime(2026, 10, 27, 1, 0, tzinfo=timezone.utc),
        guidance_answer=None, action=None, result=None,
    )
    base.update(kw)
    return CustomerCaseResult(**base)


GUIDE = Answer(verdict="answered", steps=[
    AnswerStep(text="충전 스테이션으로 복귀시킨다.", citations=[C1]),
    AnswerStep(text="충전 중에는 이동하지 않는다.", citations=[C1, C2]),
], notice=None)


def test_info_html_shows_basic_fields():
    text = detail.info_html(_case(memo="충전 안 됨"))
    for part in ["SC-1", "AGV", "A120", "배터리 저전압", "접수", "2026-10-27 10:00", "충전 안 됨"]:
        assert part in text


def test_self_guide_box_shows_steps_and_citations():
    case = _case(status=CaseStatus.COMPLETED, guidance_answer=GUIDE)
    text = detail.guide_html(case)
    assert "충전 스테이션으로 복귀시킨다." in text
    assert "p.8 · 2.4 배터리" in text  # 출처는 표기로만 보인다
    assert "1. 충전 스테이션으로 복귀시킨다." in text and "2. 충전 중에는 이동하지 않는다." in text
    assert detail.result_markdown(case) == ""  # 안내는 박스에만 보인다


def test_guide_steps_html_numbers_each_step_in_order():
    html = detail.guide_steps_html(GUIDE)
    assert html.index("1. 충전 스테이션으로 복귀시킨다.") < html.index("2. 충전 중에는 이동하지 않는다.")


def test_info_html_escapes_memo():
    text = detail.info_html(_case(memo="<img src=x onerror=alert(1)>"))
    assert "<img" not in text and "&lt;img src=x onerror=alert(1)&gt;" in text


def test_guide_steps_html_escapes_step_text_and_citation():
    bad = Citation(kind=CitationKind.MANUAL, ref_id="manual:agv_rev1_1:8:9", locator="<b>p.8</b>")
    answer = Answer(verdict="answered", steps=[
        AnswerStep(text="<script>alert(1)</script>", citations=[bad]),
    ], notice=None)
    html = detail.guide_steps_html(answer)
    assert "<script>" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<b>" not in html and "&lt;b&gt;p.8&lt;/b&gt;" in html


def test_engineer_case_has_no_guide_box():
    assert detail.guide_html(_case()) == ""


def test_engineer_case_before_completion_shows_nothing_in_body():
    # 기다림 문구는 맨 위 헤드라인 부제로 보이므로 본문엔 중복해 넣지 않는다.
    assert detail.result_markdown(_case()) == ""
    assert detail.WAITING_NOTICE in detail.status_header_html(_case())


def test_engineer_case_after_completion_shows_action_and_result():
    text = detail.result_markdown(_case(status=CaseStatus.COMPLETED, action="휠 교체", result="정상 주행"))
    assert "휠 교체" in text and "정상 주행" in text


def _build(monkeypatch, case: CustomerCaseResult):
    """화면을 Blocks에 조립하고 화면 전환 핸들러를 꺼낸다."""
    monkeypatch.setattr(detail.service, "get_customer_case", lambda case_id: case)
    with gr.Blocks() as demo:
        detail.build_customer_report_detail_screen(
            gr.State(Role.CUSTOMER), gr.State("SC-1"), gr.State(common.SCREEN_CUSTOMER_LIST),
        )
    handlers = {f.fn.__name__: f.fn for f in demo.fns.values()}
    return demo, handlers["on_screen_change"], handlers["on_action_taken"]


def test_screen_has_no_source_viewer(monkeypatch):
    # 출처를 고르는 Dropdown·원문을 펼치는 Accordion이 없다.
    demo, _, _ = _build(monkeypatch, _case())
    kinds = {type(b).__name__ for b in demo.blocks.values()}
    assert not kinds & {"Dropdown", "Accordion"}


def test_self_guide_case_opens_box(monkeypatch):
    _, on_screen_change, _ = _build(monkeypatch, _case(guidance_answer=GUIDE))
    result = on_screen_change(detail.SCREEN, "SC-1")
    box, guide, result_box, body = result[3], result[4], result[5], result[6]
    assert box.visible and "충전 스테이션으로 복귀시킨다." in guide and not result_box.visible and body == ""


def test_engineer_case_hides_box(monkeypatch):
    _, on_screen_change, _ = _build(monkeypatch, _case())
    result = on_screen_change(detail.SCREEN, "SC-1")
    box, guide, result_box, body = result[3], result[4], result[5], result[6]
    assert not box.visible and guide == "" and not result_box.visible and body == ""


def test_engineer_case_after_completion_shows_result_box(monkeypatch):
    _, on_screen_change, _ = _build(monkeypatch, _case(status=CaseStatus.COMPLETED, action="휠 교체", result="정상 주행"))
    result = on_screen_change(detail.SCREEN, "SC-1")
    result_box, body = result[5], result[6]
    assert result_box.visible and "휠 교체" in body


def test_self_guide_case_shows_support_and_resolve_buttons(monkeypatch):
    # 셀프 가이드 건은 [확인] 대신 [기술지원접수]·[해결완료] 두 버튼을 보인다.
    _, on_screen_change, _ = _build(monkeypatch, _case(guidance_answer=GUIDE))
    result = on_screen_change(detail.SCREEN, "SC-1")
    confirm_btn, support_btn, resolve_btn = result[7], result[8], result[9]
    assert not confirm_btn.visible and support_btn.visible and resolve_btn.visible


def test_engineer_case_shows_confirm_button_only(monkeypatch):
    _, on_screen_change, _ = _build(monkeypatch, _case())
    result = on_screen_change(detail.SCREEN, "SC-1")
    confirm_btn, support_btn, resolve_btn = result[7], result[8], result[9]
    assert confirm_btn.visible and not support_btn.visible and not resolve_btn.visible


def test_action_taken_swaps_to_confirm_button(monkeypatch):
    # [기술지원접수]·[해결완료] 중 아무 쪽을 눌러도 그 자리가 [확인] 버튼으로 바뀐다.
    _, _, on_action_taken = _build(monkeypatch, _case(guidance_answer=GUIDE))
    confirm_btn, support_btn, resolve_btn = on_action_taken()
    assert confirm_btn.visible and not support_btn.visible and not resolve_btn.visible


def test_status_header_reflects_case_state():
    assert "접수" in detail.status_header_html(_case())
    assert "셀프 가이드" in detail.status_header_html(_case(guidance_answer=GUIDE))
    assert "완료" in detail.status_header_html(_case(status=CaseStatus.COMPLETED, action="휠 교체", result="정상 주행"))
