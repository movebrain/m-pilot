"""ui/common 표시 변환 테스트."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from mpilot.models import (
    Answer,
    AnswerStep,
    CaseStatus,
    Citation,
    CitationKind,
    DependencyError,
    InvalidStateError,
    NotFoundError,
    Role,
    Route,
    ValidationError,
    Verdict,
)
from mpilot.ui import common


def test_virtual_banner_mentions_virtual_data():
    assert "모든 데이터는 가상" in common.VIRTUAL_DATA_BANNER


@pytest.mark.parametrize(
    ("value", "label"),
    [("received", "접수"), (CaseStatus.IN_PROGRESS, "조치 중"), ("completed", "조치 완료")],
)
def test_status_label(value, label):
    assert common.status_label(value) == label


def test_route_label():
    assert common.route_label(Route.SELF_GUIDE) == "고객 안내"
    assert common.route_label("support") == "기술지원"


def test_unknown_status_raises():
    with pytest.raises(ValueError):
        common.status_label("done")


def test_status_filter_roundtrip():
    choices = common.status_choices()
    assert choices[0] == ("전체", "")
    assert common.parse_status_filter("") is None
    assert common.parse_status_filter(None) is None
    assert common.parse_status_filter("in_progress") == CaseStatus.IN_PROGRESS


def test_format_datetime_converts_utc_to_kst():
    utc = datetime(2026, 10, 26, 0, 30, tzinfo=timezone.utc)
    assert common.format_datetime(utc) == "2026-10-26 09:30"
    assert common.format_datetime(None) == "-"


def test_format_answer_answered_lists_steps_and_sources():
    answer = Answer(
        verdict=Verdict.ANSWERED,
        steps=[
            AnswerStep(
                text="비상정지 버튼을 누른다.",
                citations=[Citation(kind=CitationKind.MANUAL, ref_id="manual:m1:12:1", locator="p.12 · 3.2 점검")],
            )
        ],
        notice=None,
    )
    text = common.format_answer_markdown(answer)
    assert "1. 비상정지 버튼을 누른다." in text
    assert "[매뉴얼] p.12 · 3.2 점검" in text


def test_format_answer_held_shows_notice_only():
    answer = Answer(verdict=Verdict.INSUFFICIENT_EVIDENCE, steps=[], notice=None)
    text = common.format_answer_markdown(answer)
    assert "근거 부족" in text
    assert "1." not in text


@pytest.mark.parametrize("verdict", list(Verdict))
def test_every_verdict_has_a_label(verdict):
    assert common.verdict_label(verdict)


@pytest.mark.parametrize("verdict", [v for v in Verdict if v is not Verdict.ANSWERED])
def test_every_hold_verdict_has_a_default_notice(verdict):
    answer = Answer(verdict=verdict, steps=[], notice=None)
    assert common.verdict_label(verdict) in common.format_answer_markdown(answer)


@pytest.mark.parametrize(
    "exc_type", [ValidationError, NotFoundError, InvalidStateError, DependencyError]
)
def test_error_message_hides_raw_text(exc_type):
    message = common.error_message(exc_type("DB password=secret"))
    assert message == common.ERROR_MESSAGES[exc_type]
    assert "secret" not in message


def test_error_message_unknown_exception():
    assert common.error_message(RuntimeError("boom")) == common.UNKNOWN_ERROR_MESSAGE


@pytest.mark.parametrize(
    ("role", "screen"),
    [(Role.CUSTOMER, common.SCREEN_CUSTOMER_LIST), (Role.ENGINEER, common.SCREEN_ENGINEER_LIST), ("customer", common.SCREEN_CUSTOMER_LIST)],
)
def test_first_screen(role, screen):
    assert common.first_screen(role) == screen


def test_role_screens_do_not_overlap():
    customer = set(common.ROLE_SCREENS[Role.CUSTOMER])
    engineer = set(common.ROLE_SCREENS[Role.ENGINEER])
    assert len(customer) == len(engineer) == 3
    assert not customer & engineer


@pytest.mark.parametrize("current", [*common.ROLE_SCREENS[Role.CUSTOMER], *common.ROLE_SCREENS[Role.ENGINEER]])
def test_only_one_screen_visible(current):
    all_screens = [*common.ROLE_SCREENS[Role.CUSTOMER], *common.ROLE_SCREENS[Role.ENGINEER]]
    visible = [s for s in all_screens if common.is_screen_visible(current, s)]
    assert visible == [current]


def test_format_answer_notice_is_hidden_by_default_and_shown_when_asked():
    answer = Answer(
        verdict=Verdict.ANSWERED,
        steps=[AnswerStep(text="점검한다.", citations=[])],
        notice="다른 설비 자료를 포함했습니다.",
    )
    assert "다른 설비 자료를 포함했습니다." not in common.format_answer_markdown(answer)
    shown = common.format_answer_markdown(answer, show_notice=True)
    assert shown.startswith("> 다른 설비 자료를 포함했습니다.")
    assert shown.index("> 다른 설비") < shown.index("1. 점검한다.")


# 목록 페이지네이션 ------------------------------------------------------------------


@pytest.mark.parametrize(("total", "pages"), [
    (0, 1), (1, 1), (common.PAGE_SIZE, 1), (common.PAGE_SIZE + 1, 2), (common.PAGE_SIZE * 3, 3),
])
def test_page_count(total, pages):
    assert common.page_count(total) == pages


@pytest.mark.parametrize(("page", "total", "clamped"), [
    (-1, 50, 0), (0, 50, 0), (2, 50, 2), (3, 50, 2), (99, 0, 0),
])
def test_clamp_page(page, total, clamped):
    assert common.clamp_page(page, total) == clamped


def test_page_slice_cuts_one_page_and_clamps():
    rows = list(range(common.PAGE_SIZE + 5))
    assert common.page_slice(rows, 0) == rows[:common.PAGE_SIZE]
    assert common.page_slice(rows, 1) == rows[common.PAGE_SIZE:]  # 마지막 페이지는 남은 건만
    assert common.page_slice(rows, 9) == rows[common.PAGE_SIZE:]  # 범위를 벗어나면 마지막 페이지
    assert common.page_slice([], 0) == []


def test_page_label_is_one_based():
    assert common.page_label(0, 0) == "1 / 1"
    assert common.page_label(1, common.PAGE_SIZE * 3) == "2 / 3"
    assert common.page_label(7, common.PAGE_SIZE * 3) == "3 / 3"


# `gr.HTML`에 넣는 박스는 입력을 이스케이프한다 ------------------------------------------


def test_info_box_html_escapes_label_value_and_title():
    payload = "<img src=x onerror=alert(1)>"
    box = common.info_box_html([(payload, payload)], title=payload)
    assert "<img" not in box
    assert box.count("&lt;img src=x onerror=alert(1)&gt;") == 3


def test_info_box_html_keeps_plain_text_and_ampersand():
    box = common.info_box_html([("증상 메모", "전압 < 20V & 정지")])
    assert "전압 &lt; 20V &amp; 정지" in box
