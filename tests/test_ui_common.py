"""ui/common 표시 변환 테스트."""

from __future__ import annotations

from datetime import datetime, timezone

import gradio as gr
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


def test_info_table_html_escapes_label_value_and_title():
    payload = "<img src=x onerror=alert(1)>"
    table = common.info_table_html([(payload, payload)], title=payload)
    assert "<img" not in table
    assert table.count("&lt;img src=x onerror=alert(1)&gt;") == 3


def test_info_table_html_keeps_plain_text_and_ampersand():
    table = common.info_table_html([("증상 메모", "전압 < 20V & 정지")])
    assert "전압 &lt; 20V &amp; 정지" in table


def test_info_table_html_horizontal_puts_labels_in_header_row():
    table = common.info_table_html([("접수번호", "SC-1"), ("설비", "AGV")])
    assert table.index("접수번호") < table.index("SC-1")  # 라벨 행이 값 행보다 먼저 나온다
    assert table.count("<tr") == 2  # 라벨 한 줄 + 값 한 줄


def test_info_table_html_columns_per_row_wraps_into_multiple_label_value_pairs():
    fields = [("접수번호", "SC-1"), ("상태", "접수"), ("설비", "AGV"), ("심각도", "상")]
    table = common.info_table_html(fields, columns_per_row=2)
    assert table.count("<tr") == 4  # (라벨+값) 줄 쌍이 2묶음
    # 첫 묶음(접수번호·상태 라벨, SC-1·접수 값)이 두 번째 묶음보다 먼저 나온다
    assert table.index("상태") < table.index("설비") < table.index("심각도")
    assert table.index("접수") < table.index("AGV")


def test_info_table_html_no_tr_lets_hidden_gradio_default_border_through():
    # Gradio 기본 테마가 tr에 테두리를 깔아 둬서, tr 자체도 지워야 검게 비쳐 보이지 않는다.
    table = common.info_table_html([("접수번호", "SC-1")])
    assert '<tr style="border:none !important">' in table


def test_info_table_html_note_moves_long_field_below_as_full_width_row():
    table = common.info_table_html(
        [("접수번호", "SC-1"), ("설비", "AGV")], note=("증상 메모", "아주 긴 증상 메모 내용")
    )
    assert table.index("SC-1") < table.index("증상 메모") < table.index("아주 긴 증상 메모 내용")
    assert 'colspan="2"' in table


def test_info_table_html_min_width_applies_to_container():
    table = common.info_table_html([("접수번호", "SC-1")], min_width="800px")
    assert "min-width:800px;" in table


def test_info_table_html_max_width_applies_to_container():
    table = common.info_table_html([("접수번호", "SC-1")], max_width="800px")
    assert "max-width:800px;" in table


def test_info_table_html_defaults_to_left_aligned():
    table = common.info_table_html([("접수번호", "SC-1")])
    assert 'margin:0">' in table


def test_info_table_html_center_floats_in_middle_of_column():
    table = common.info_table_html([("접수번호", "SC-1")], center=True)
    assert 'margin:0 auto">' in table


def test_info_table_html_cell_padding_overrides_gradio_default():
    # Gradio 기본 th·td padding이 `!important`라서, 우리 padding도 `!important`가 아니면 밀린다.
    table = common.info_table_html([("접수번호", "SC-1")])
    assert "padding:6px 12px !important" in table


def test_info_table_html_has_no_border_radius():
    table = common.info_table_html([("접수번호", "SC-1")])
    assert "border-radius" not in table


def test_info_table_html_table_itself_has_no_margin_or_padding():
    # Gradio 기본 테마가 table 자체에 마진·패딩을 깔아 둬서, 회색 테두리 위쪽에 빈 여백이 남는다.
    for table in (
        common.info_table_html([("접수번호", "SC-1")]),
        common.info_table_html([("원인", "엔코더 편차")], vertical=True),
    ):
        assert "<table style=\"border-collapse:collapse;width:100%;border:none !important;" \
            "margin:0 !important;padding:0 !important;border-spacing:0 !important\">" in table


def test_info_table_html_vertical_pairs_label_and_value_per_row():
    table = common.info_table_html([("원인", "엔코더 편차"), ("결과", "정상 동작")], vertical=True)
    assert "<thead>" not in table
    assert table.index("원인") < table.index("엔코더 편차") < table.index("결과") < table.index("정상 동작")
    assert table.count("border-bottom") == 2  # 마지막 줄 제외 모든 줄에 한 번씩(th·td)


def test_info_table_html_vertical_matches_horizontal_colors():
    # 접수내역(가로형)과 조치 결과(세로형)가 같은 디자인으로 보이도록 라벨(연회색)·값(흰색) 배경이 같다.
    table = common.info_table_html([("원인", "엔코더 편차")], vertical=True)
    assert "background:#f4f4f5 !important" in table  # 라벨 칸
    assert "background:#ffffff !important" in table  # 값 칸


def test_info_table_html_vertical_label_column_width_is_fixed():
    table = common.info_table_html([("원인", "엔코더 편차")], vertical=True)
    assert "max-width:100px !important" in table
    assert "width:100px !important" in table


# 내비바 ------------------------------------------------------------------------------


def test_navbar_logo_click_goes_to_home_screen():
    with gr.Blocks() as demo:
        screen = gr.State("eng_chat")
        common.build_navbar("M-Pilot 엔지니어", [("대시보드", "eng_list")], screen, "eng_list")
    # 로고 버튼은 인자 없이 home_screen을 그대로 돌려준다. 메뉴 버튼은 target을 기본값으로 받는다.
    zero_arg_fns = [f.fn for f in demo.fns.values() if f.fn.__code__.co_argcount == 0]
    assert any(fn() == "eng_list" for fn in zero_arg_fns)
