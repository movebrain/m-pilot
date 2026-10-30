"""고객 신고 목록 화면의 표 변환·행 선택·재조회 테스트."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import gradio as gr

from mpilot.models import CaseStatus, CustomerCaseResult, Equipment, Role, Route, ValidationError
from mpilot.ui import customer_dashboard as dashboard


def _result(case_id: str, status: CaseStatus) -> CustomerCaseResult:
    return CustomerCaseResult(
        case_id=case_id, equipment=Equipment.MINI_LOADER, code="E-101", error_name="포크 위치 센서 오감지",
        memo=None, status=status, route=Route.SUPPORT, created_at=datetime(2026, 10, 25, 1, 0, tzinfo=timezone.utc),
        guidance_answer=None, action=None, result=None,
    )


def test_case_rows_follow_headers_and_labels():
    [row] = dashboard.case_rows([_result("SC-1a2b3c4d", CaseStatus.IN_PROGRESS)])
    assert len(row) == len(dashboard.TABLE_HEADERS)
    assert row == ["SC-1a2b3c4d", "Mini Loader", "E-101", "포크 위치 센서 오감지", "조치 중", "2026-10-25 10:00"]


def test_row_status_label_marks_self_guide_completion():
    from mpilot.models import Answer

    guide = Answer(verdict="answered", steps=[], notice=None)
    self_guided = _result("SC-1", CaseStatus.COMPLETED)
    self_guided = self_guided.model_copy(update={"guidance_answer": guide})
    assert dashboard.row_status_label(self_guided) == dashboard.SELF_GUIDE_COMPLETED_LABEL


def test_row_status_label_keeps_normal_label_for_engineer_completion():
    assert dashboard.row_status_label(_result("SC-1", CaseStatus.COMPLETED)) == "조치 완료"
    assert dashboard.row_status_label(_result("SC-1", CaseStatus.IN_PROGRESS)) == "조치 중"


def test_load_rows_passes_filter(monkeypatch):
    seen = []
    monkeypatch.setattr(
        dashboard.service, "list_customer_cases",
        lambda status=None: seen.append(status) or [_result("SC-1", CaseStatus.COMPLETED)],
    )
    assert dashboard.load_rows("completed")[0][4] == "조치 완료"
    assert dashboard.load_rows("")[0][4] == "조치 완료"
    assert seen == [CaseStatus.COMPLETED, None]


def test_load_rows_hides_error_text(monkeypatch):
    warnings = []

    def fail(status=None):
        raise ValidationError("DB 원문 오류")

    monkeypatch.setattr(dashboard.service, "list_customer_cases", fail)
    monkeypatch.setattr(dashboard.gr, "Warning", warnings.append)

    assert dashboard.load_rows() == []
    assert warnings == [dashboard.common.error_message(ValidationError())]


def test_picked_case_id():
    rows = [["SC-a", "SRM"], ["SC-b", "AGV"]]
    assert dashboard.picked_case_id(rows, 1) == "SC-b"
    assert dashboard.picked_case_id(rows, 5) is None
    assert dashboard.picked_case_id([], 0) is None
    assert dashboard.picked_case_id([[""]], 0) is None  # 빈 표의 자리 행


def _build(monkeypatch):
    """화면을 Blocks에 조립하고 표 컴포넌트와 핸들러(`on_select`, `on_screen_change`)를 꺼낸다."""
    calls = []
    monkeypatch.setattr(
        dashboard.service, "list_customer_cases",
        lambda status=None: calls.append(status) or [_result("SC-1", CaseStatus.RECEIVED)],
    )
    with gr.Blocks() as demo:
        dashboard.build_customer_dashboard_screen(
            gr.State(Role.CUSTOMER), gr.State(None), gr.State(dashboard.SCREEN)
        )
    table = next(b for b in demo.blocks.values() if isinstance(b, gr.Dataframe))
    handlers = {f.fn.__name__: f.fn for f in demo.fns.values()}
    calls.clear()  # 조립 중 초기값 읽기는 세지 않는다
    return table, handlers, calls


def test_table_passes_rows_as_lists(monkeypatch):
    table, _, _ = _build(monkeypatch)
    assert table.type == "array"  # on_select는 list[list[str]]를 받는다


def test_select_row_opens_detail(monkeypatch):
    _, handlers, _ = _build(monkeypatch)
    rows = [["SC-a", "SRM"], ["SC-b", "AGV"]]
    assert handlers["on_select"](rows, SimpleNamespace(index=[1, 0])) == ("SC-b", dashboard.common.SCREEN_CUSTOMER_DETAIL)


def test_select_empty_row_stays(monkeypatch):
    _, handlers, _ = _build(monkeypatch)
    assert handlers["on_select"]([[""]], SimpleNamespace(index=[0, 0])) == (gr.skip(), gr.skip())


def test_screen_change_reloads_only_when_visible(monkeypatch):
    _, handlers, calls = _build(monkeypatch)

    column, rows, label, page = handlers["on_screen_change"](dashboard.SCREEN, "in_progress")
    assert column.visible and rows[0][0] == "SC-1" and page == 0 and calls == [CaseStatus.IN_PROGRESS]
    assert label == "1 / 1"

    column, rows, label, page = handlers["on_screen_change"](dashboard.common.SCREEN_CUSTOMER_DETAIL, "")
    assert not column.visible and rows == gr.skip() and calls == [CaseStatus.IN_PROGRESS]  # 숨겨지면 읽지 않는다
