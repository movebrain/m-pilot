"""엔지니어 대시보드: `service.list_engineer_cases`와 화면의 표 변환·행 선택 테스트."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import gradio as gr
import pytest

from mpilot import service
from mpilot.models import (
    ActionOwner,
    CaseDetail,
    CaseStatus,
    Equipment,
    ErrorCode,
    Role,
    Route,
    ServiceCase,
    Severity,
    Source,
    ValidationError,
)
from mpilot.ui import engineer_dashboard as dashboard

_CODE = ErrorCode(
    equipment=Equipment.SRM, code="S115", name="리프트 엔코더 편차", severity=Severity.HIGH,
    action_owner=ActionOwner.ENGINEER, cause="원인", action="조치", manual_id="srm_rev1_2", page=41,
    section="6.3 승강부",
)


def _detail(case_id: str, day: int, route=Route.SUPPORT, source=Source.REPORTED,
            status=CaseStatus.RECEIVED) -> CaseDetail:
    created = datetime(2026, 10, day, 1, 0, tzinfo=timezone.utc)
    return CaseDetail(
        service_case=ServiceCase(
            case_id=case_id, equipment=Equipment.SRM, code="S115", memo=None, source=source, status=status,
            route=route, guide_failure=None, guidance_answer=None, created_at=created,
            completed_at=created if status == CaseStatus.COMPLETED else None,
        ),
        error_code=_CODE,
        resolution=None,
    )


# service ---------------------------------------------------------------------


@pytest.fixture
def fetch_calls(monkeypatch):
    calls = []
    rows = [_detail("SEED-1", 1, source=Source.SEED, status=CaseStatus.COMPLETED), _detail("SC-new", 26)]

    def fetch_cases(status=None, source=None):
        calls.append((status, source))
        return rows

    monkeypatch.setattr(service.rdb, "fetch_cases", fetch_cases)
    return calls


def test_list_engineer_cases_includes_seed(fetch_calls):
    results = service.list_engineer_cases()
    assert fetch_calls == [(None, None)]  # 원본으로 거르지 않는다
    assert [r.service_case.case_id for r in results] == ["SC-new", "SEED-1"]  # 최신순


@pytest.mark.parametrize("status", [CaseStatus.RECEIVED, CaseStatus.IN_PROGRESS, CaseStatus.COMPLETED])
def test_list_engineer_cases_passes_status(fetch_calls, status):
    service.list_engineer_cases(status)
    assert fetch_calls == [(status, None)]


# 화면 --------------------------------------------------------------------------


def test_case_rows_follow_headers_and_labels():
    [row] = dashboard.case_rows([_detail("SC-1", 27, route=Route.SELF_GUIDE, status=CaseStatus.COMPLETED)])
    assert len(row) == len(dashboard.TABLE_HEADERS)
    assert row == ["SC-1", "SRM", "S115", "리프트 엔코더 편차", "상", "고객 안내", "조치 완료", "2026-10-27 10:00"]


def test_is_support_row():
    support, self_guide = dashboard.case_rows([_detail("SC-1", 27), _detail("SC-2", 27, route=Route.SELF_GUIDE)])
    assert dashboard.is_support_row(support)
    assert not dashboard.is_support_row(self_guide)
    assert not dashboard.is_support_row([""])


def test_load_rows_hides_error_text(monkeypatch):
    warnings = []

    def fail(status=None):
        raise ValidationError("DB 원문 오류")

    monkeypatch.setattr(dashboard.service, "list_engineer_cases", fail)
    monkeypatch.setattr(dashboard.gr, "Warning", warnings.append)

    assert dashboard.load_rows() == []
    assert warnings == [dashboard.common.error_message(ValidationError())]


def _build(monkeypatch):
    """화면을 Blocks에 조립하고 표 컴포넌트와 핸들러를 꺼낸다."""
    calls = []
    monkeypatch.setattr(
        dashboard.service, "list_engineer_cases",
        lambda status=None: calls.append(status) or [_detail("SC-1", 27)],
    )
    with gr.Blocks() as demo:
        dashboard.build_engineer_dashboard_screen(gr.State(Role.ENGINEER), gr.State(None), gr.State(dashboard.SCREEN))
    table = next(b for b in demo.blocks.values() if isinstance(b, gr.Dataframe))
    handlers = {f.fn.__name__: f.fn for f in demo.fns.values()}
    calls.clear()  # 조립 중 초기값 읽기는 세지 않는다
    return table, handlers, calls


def test_table_passes_rows_as_lists(monkeypatch):
    table, _, _ = _build(monkeypatch)
    assert table.type == "array"


def test_select_support_row_opens_chat(monkeypatch):
    _, handlers, _ = _build(monkeypatch)
    rows = dashboard.case_rows([_detail("SC-a", 27), _detail("SC-b", 26)])
    assert handlers["on_select"](rows, SimpleNamespace(index=[1, 0])) == ("SC-b", dashboard.common.SCREEN_ENGINEER_CHAT)


def test_select_self_guide_row_stays_with_notice(monkeypatch):
    notices = []
    _, handlers, _ = _build(monkeypatch)
    monkeypatch.setattr(dashboard.gr, "Info", notices.append)
    rows = dashboard.case_rows([_detail("SC-g", 27, route=Route.SELF_GUIDE)])

    assert handlers["on_select"](rows, SimpleNamespace(index=[0, 0])) == (gr.skip(), gr.skip())
    assert notices == [dashboard.SELF_GUIDE_NOTICE]


def test_select_empty_row_stays(monkeypatch):
    _, handlers, _ = _build(monkeypatch)
    assert handlers["on_select"]([[""]], SimpleNamespace(index=[0, 0])) == (gr.skip(), gr.skip())


def test_screen_change_reloads_only_when_visible(monkeypatch):
    _, handlers, calls = _build(monkeypatch)

    column, rows, label, page = handlers["on_screen_change"](dashboard.SCREEN, "completed")
    assert column.visible and rows[0][0] == "SC-1" and page == 0 and calls == [CaseStatus.COMPLETED]
    assert label == "1 / 1"

    column, rows, label, page = handlers["on_screen_change"](dashboard.common.SCREEN_ENGINEER_CHAT, "")
    assert not column.visible and rows == gr.skip() and calls == [CaseStatus.COMPLETED]
