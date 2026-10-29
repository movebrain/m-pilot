"""조치 확정(`service.confirm_resolution`) 테스트. rdb·vectordb는 `monkeypatch`로 대신한다."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from mpilot import service
from mpilot.models import (
    ActionOwner,
    CaseDetail,
    CaseStatus,
    DependencyError,
    Equipment,
    ErrorCode,
    InvalidStateError,
    Resolution,
    Route,
    ServiceCase,
    Severity,
    Source,
)

_NOW = datetime(2026, 10, 28, 1, 0, tzinfo=timezone.utc)
_CODE = ErrorCode(
    equipment=Equipment.SRM, code="S115", name="리프트 엔코더 편차", severity=Severity.HIGH,
    action_owner=ActionOwner.ENGINEER, cause="원인", action="조치", manual_id="srm_rev1_2", page=41,
    section="6.3 승강부",
)
RECORD = Resolution(cause="엔코더 커넥터 접촉 불량", action="커넥터 재체결", result="정상 동작 확인")
DONE = CaseDetail(
    service_case=ServiceCase(
        case_id="SC-1", equipment=Equipment.SRM, code="S115", memo=None, source=Source.REPORTED,
        status=CaseStatus.COMPLETED, route=Route.SUPPORT, guide_failure=None, guidance_answer=None,
        created_at=_NOW, completed_at=_NOW,
    ),
    error_code=_CODE,
    resolution=RECORD,
)


@pytest.fixture
def backend(monkeypatch):
    """rdb 저장과 vectordb 반영을 가짜로 바꾸고 호출 순서를 기록한다."""
    state = {"calls": [], "save_error": None, "index_error": None}

    def save_confirmed_resolution(case_id, record):
        state["calls"].append(("save", case_id, record))
        if state["save_error"]:
            raise state["save_error"]
        return DONE

    def index_history(case):
        state["calls"].append(("index", case.service_case.case_id))
        if state["index_error"]:
            raise state["index_error"]

    monkeypatch.setattr(service.rdb, "save_confirmed_resolution", save_confirmed_resolution)
    monkeypatch.setattr(service.vectordb, "index_history", index_history)
    return state


def test_save_then_index_returns_true(backend):
    detail, indexed = service.confirm_resolution("SC-1", RECORD)
    assert (detail, indexed) == (DONE, True)
    assert backend["calls"] == [("save", "SC-1", RECORD), ("index", "SC-1")]  # 저장이 먼저다


@pytest.mark.parametrize("error", [DependencyError("임베딩 실패"), RuntimeError("저장소 오류")])
def test_index_failure_keeps_saved_and_returns_false(backend, error):
    backend["index_error"] = error
    detail, indexed = service.confirm_resolution("SC-1", RECORD)
    assert (detail, indexed) == (DONE, False)  # 저장은 되돌리지 않는다


@pytest.mark.parametrize("error", [DependencyError("DB 실패"), InvalidStateError("이미 완료")])
def test_save_failure_raises_without_index(backend, error):
    backend["save_error"] = error
    with pytest.raises(type(error)):
        service.confirm_resolution("SC-1", RECORD)
    assert backend["calls"] == [("save", "SC-1", RECORD)]  # 반영을 시도하지 않는다
