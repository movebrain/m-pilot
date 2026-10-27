"""고객 신고 목록 화면의 표 변환·행 선택 테스트."""

from __future__ import annotations

from datetime import datetime, timezone

from mpilot.models import CaseStatus, CustomerCaseResult, Equipment, ValidationError
from mpilot.ui import customer_dashboard as dashboard


def _result(case_id: str, status: CaseStatus) -> CustomerCaseResult:
    return CustomerCaseResult(
        case_id=case_id, equipment=Equipment.MINI_LOADER, code="E-101", error_name="포크 위치 센서 오감지",
        memo=None, status=status, created_at=datetime(2026, 10, 25, 1, 0, tzinfo=timezone.utc),
        guidance_answer=None, action=None, result=None,
    )


def test_case_rows_follow_headers_and_labels():
    [row] = dashboard.case_rows([_result("SC-1a2b3c4d", CaseStatus.IN_PROGRESS)])
    assert len(row) == len(dashboard.TABLE_HEADERS)
    assert row == ["SC-1a2b3c4d", "Mini Loader", "E-101", "포크 위치 센서 오감지", "조치 중", "2026-10-25 10:00"]


def test_load_rows_passes_filter(monkeypatch):
    seen = []
    monkeypatch.setattr(
        dashboard.service, "list_customer_cases",
        lambda status=None: seen.append(status) or [_result("SC-1", CaseStatus.COMPLETED)],
    )
    assert dashboard.load_rows("completed")[0][4] == "조치 완료"
    assert dashboard.load_rows("") and seen == [CaseStatus.COMPLETED, None]


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
