"""고객 화면용 service 함수 테스트. DB는 `monkeypatch`로 대신한다."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from mpilot import service
from mpilot.models import (
    ActionOwner,
    CaseDetail,
    CaseStatus,
    CustomerCaseResult,
    Equipment,
    ErrorCode,
    Resolution,
    Route,
    ServiceCase,
    Severity,
    Source,
    ValidationError,
)

_CODE = ErrorCode(
    equipment=Equipment.SRM, code="S-310", name="리프트 엔코더 편차", severity=Severity.MEDIUM,
    action_owner=ActionOwner.ENGINEER, cause="엔코더 커플링 풀림", action="커플링 체결 확인",
    manual_id="srm_rev1_2", page=41, section="6.3 승강부",
)


def _detail(case_id: str, day: int, status: CaseStatus = CaseStatus.RECEIVED, resolution=None) -> CaseDetail:
    created = datetime(2026, 10, day, tzinfo=timezone.utc)
    return CaseDetail(
        service_case=ServiceCase(
            case_id=case_id, equipment=Equipment.SRM, code="S-310", memo=None, source=Source.REPORTED,
            status=status, route=Route.SUPPORT, guide_failure=None, guidance_answer=None,
            created_at=created, completed_at=created if status == CaseStatus.COMPLETED else None,
        ),
        error_code=_CODE,
        resolution=resolution,
    )


@pytest.fixture
def fetch_calls(monkeypatch):
    """`rdb.fetch_cases`를 가짜로 바꾸고 받은 인자를 기록한다."""
    calls: list[tuple] = []
    rows = [_detail("SC-old", 20), _detail("SC-new", 26)]  # 일부러 오래된 순

    def fake_fetch_cases(status=None, source=None):
        calls.append((status, source))
        return rows

    monkeypatch.setattr(service.rdb, "fetch_cases", fake_fetch_cases)
    return calls


def test_list_customer_cases_requests_reported_only(fetch_calls):
    service.list_customer_cases()
    assert fetch_calls == [(None, Source.REPORTED)]  # 시드 이력은 DB에서 거른다


@pytest.mark.parametrize("status", [CaseStatus.RECEIVED, CaseStatus.IN_PROGRESS, CaseStatus.COMPLETED, None])
def test_list_customer_cases_passes_status_through(fetch_calls, status):
    service.list_customer_cases(status)
    assert fetch_calls == [(status, Source.REPORTED)]


def test_list_customer_cases_returns_public_results_newest_first(fetch_calls):
    results = service.list_customer_cases()
    assert all(isinstance(r, CustomerCaseResult) for r in results)
    assert [r.case_id for r in results] == ["SC-new", "SC-old"]


def test_list_customer_cases_hides_cause(monkeypatch):
    done = _detail("SC-done", 25, CaseStatus.COMPLETED, Resolution(cause="내부 원인", action="재체결", result="정상"))
    monkeypatch.setattr(service.rdb, "fetch_cases", lambda status=None, source=None: [done])

    [result] = service.list_customer_cases()

    assert (result.action, result.result) == ("재체결", "정상")
    assert "내부 원인" not in result.model_dump_json()


def test_list_customer_cases_propagates_db_errors(monkeypatch):
    def fail(status=None, source=None):
        raise ValidationError("허용되지 않은 상태")

    monkeypatch.setattr(service.rdb, "fetch_cases", fail)
    with pytest.raises(ValidationError):
        service.list_customer_cases()
