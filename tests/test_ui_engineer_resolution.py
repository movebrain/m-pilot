"""조치 이력 저장 화면: 입력 변환 함수와 화면 핸들러 테스트. 서비스는 `monkeypatch`로 대신한다."""

from __future__ import annotations

from datetime import datetime, timezone

import gradio as gr
import pytest

from mpilot.models import (
    ActionOwner,
    CaseDetail,
    CaseStatus,
    DependencyError,
    Equipment,
    ErrorCode,
    InvalidStateError,
    NotFoundError,
    Resolution,
    Role,
    Route,
    ServiceCase,
    Severity,
    Source,
    ValidationError,
)
from mpilot.ui import engineer_resolution as resolution

_NOW = datetime(2026, 10, 28, 1, 0, tzinfo=timezone.utc)
_CODE = ErrorCode(
    equipment=Equipment.SRM, code="S115", name="리프트 엔코더 편차", severity=Severity.HIGH,
    action_owner=ActionOwner.ENGINEER, cause="원인", action="조치", manual_id="srm_rev1_2", page=41,
    section="6.3 승강부",
)


def _detail(status=CaseStatus.IN_PROGRESS) -> CaseDetail:
    return CaseDetail(
        service_case=ServiceCase(
            case_id="SC-1", equipment=Equipment.SRM, code="S115", memo="소음", source=Source.REPORTED,
            status=status, route=Route.SUPPORT, guide_failure=None, guidance_answer=None, created_at=_NOW,
            completed_at=_NOW if status == CaseStatus.COMPLETED else None,
        ),
        error_code=_CODE,
        resolution=None,
    )


# 입력 변환 --------------------------------------------------------------------------


def test_build_resolution_strips_fields():
    assert resolution.build_resolution(" 원인 ", "조치\n", "\t결과") == Resolution(
        cause="원인", action="조치", result="결과",
    )


@pytest.mark.parametrize("fields", [
    ("", "조치", "결과"),
    ("원인", "   ", "결과"),
    ("원인", "조치", None),
    ("가" * 5001, "조치", "결과"),
])
def test_build_resolution_rejects_empty_or_long(fields):
    with pytest.raises(ValidationError):
        resolution.build_resolution(*fields)


def test_build_resolution_length_boundary_passes():
    assert resolution.build_resolution("가" * 5000, "조치", "결과").cause == "가" * 5000


def test_confirm_notice_by_index_result():
    assert resolution.confirm_notice(True) == resolution.INDEXED_NOTICE
    assert resolution.confirm_notice(False) == resolution.NOT_INDEXED_NOTICE


def test_case_summary_shows_case_fields():
    text = resolution.case_summary_html(_detail())
    for expected in ("접수내역", "SC-1", "조치 중", "SRM", "S115", "리프트 엔코더 편차", "소음"):
        assert expected in text


# 화면 핸들러 ------------------------------------------------------------------------


@pytest.fixture
def screen(monkeypatch):
    """화면을 조립하고 핸들러와 서비스 호출 기록을 꺼낸다."""
    state = {"detail": _detail(), "confirmed": [], "confirm_error": None, "indexed": True,
             "warnings": [], "infos": []}

    def get_engineer_case(case_id):
        if case_id != "SC-1":
            raise NotFoundError(case_id)
        return state["detail"]

    def confirm_resolution(case_id, record):
        if state["confirm_error"]:
            raise state["confirm_error"]
        state["confirmed"].append((case_id, record))
        return state["detail"], state["indexed"]

    monkeypatch.setattr(resolution.service, "get_engineer_case", get_engineer_case)
    monkeypatch.setattr(resolution.service, "confirm_resolution", confirm_resolution)
    monkeypatch.setattr(resolution.gr, "Warning", state["warnings"].append)
    monkeypatch.setattr(resolution.gr, "Info", state["infos"].append)
    with gr.Blocks() as demo:
        resolution.build_engineer_resolution_screen(
            gr.State(Role.ENGINEER), gr.State(None), gr.State(resolution.common.SCREEN_ENGINEER_CHAT),
        )
    state["handlers"] = {f.fn.__name__: f.fn for f in demo.fns.values()}
    return state


def test_enter_loads_case_and_clears_inputs(screen):
    column, summary, cause, action, result, button, next_screen = (
        screen["handlers"]["on_screen_change"](resolution.SCREEN, "SC-1")
    )
    assert column.visible and "SC-1" in summary
    assert all(box.value == "" and box.interactive for box in (cause, action, result))
    assert button.interactive and next_screen == gr.skip()


def test_enter_completed_case_blocks_inputs(screen):
    screen["detail"] = _detail(CaseStatus.COMPLETED)
    _, _, cause, action, result, button, _ = screen["handlers"]["on_screen_change"](resolution.SCREEN, "SC-1")
    assert not any(box.interactive for box in (cause, action, result)) and not button.interactive
    assert screen["infos"] == [resolution.COMPLETED_NOTICE]


def test_enter_unknown_case_goes_to_dashboard(screen):
    result = screen["handlers"]["on_screen_change"](resolution.SCREEN, "SC-x")
    assert result[-1] == resolution.common.SCREEN_ENGINEER_LIST
    assert screen["warnings"] == [resolution.common.error_message(NotFoundError())]


def test_hidden_screen_skips_reload(screen):
    result = screen["handlers"]["on_screen_change"](resolution.common.SCREEN_ENGINEER_CHAT, "SC-1")
    assert not result[0].visible and all(r == gr.skip() for r in result[1:])


@pytest.mark.parametrize("indexed", [True, False])
def test_confirm_goes_to_dashboard_with_notice(screen, indexed):
    screen["indexed"] = indexed
    assert screen["handlers"]["on_confirm"]("SC-1", "원인", "조치", "결과") == resolution.common.SCREEN_ENGINEER_LIST
    assert screen["confirmed"] == [("SC-1", Resolution(cause="원인", action="조치", result="결과"))]
    assert screen["infos"] == [resolution.confirm_notice(indexed)]


def test_confirm_empty_field_stays_without_calling_service(screen):
    assert screen["handlers"]["on_confirm"]("SC-1", "원인", "", "결과") == gr.skip()
    assert screen["confirmed"] == []
    assert screen["warnings"] == [resolution.common.error_message(ValidationError())]


@pytest.mark.parametrize("error", [InvalidStateError("이미 완료"), DependencyError("DB 원문 오류")])
def test_confirm_failure_stays_with_fixed_text(screen, error):
    screen["confirm_error"] = error
    assert screen["handlers"]["on_confirm"]("SC-1", "원인", "조치", "결과") == gr.skip()
    assert screen["warnings"] == [resolution.common.error_message(error)]
