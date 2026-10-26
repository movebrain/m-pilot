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


@pytest.mark.parametrize(
    "exc_type", [ValidationError, NotFoundError, InvalidStateError, DependencyError]
)
def test_error_message_hides_raw_text(exc_type):
    message = common.error_message(exc_type("DB password=secret"))
    assert message == common.ERROR_MESSAGES[exc_type]
    assert "secret" not in message


def test_error_message_unknown_exception():
    assert common.error_message(RuntimeError("boom")) == common.UNKNOWN_ERROR_MESSAGE
