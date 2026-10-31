"""고객 신고 상세용 service 함수 테스트. DB·검색 저장소는 `monkeypatch`로 대신한다."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from langchain_core.documents import Document

from mpilot import service
from mpilot.models import (
    ActionOwner,
    CaseDetail,
    CaseStatus,
    Citation,
    CitationKind,
    DependencyError,
    Equipment,
    ErrorCode,
    GuideFailure,
    InvalidStateError,
    NotFoundError,
    Resolution,
    Route,
    ServiceCase,
    Severity,
    Source,
)

_CODE = ErrorCode(
    equipment=Equipment.RTV, code="R206", name="주행 휠 슬립", severity=Severity.MEDIUM,
    action_owner=ActionOwner.ENGINEER, cause="표의 원인", action="표의 조치",
    manual_id="rtv_rev2_1", page=15, section="4.1",
)
SECRET_CAUSE = "내부 원인: 휠 마모"


def _detail(source=Source.REPORTED, status=CaseStatus.COMPLETED, guide_failure=None, resolution=None) -> CaseDetail:
    created = datetime(2026, 10, 27, tzinfo=timezone.utc)
    return CaseDetail(
        service_case=ServiceCase(
            case_id="SC-1", equipment=Equipment.RTV, code="R206", memo="정지", source=source, status=status,
            route=Route.SUPPORT, guide_failure=guide_failure, guidance_answer=None, created_at=created,
            completed_at=created if status == CaseStatus.COMPLETED else None,
        ),
        error_code=_CODE,
        resolution=resolution,
    )


def test_get_customer_case_hides_cause_and_guide_failure(monkeypatch):
    detail = _detail(
        guide_failure=GuideFailure.GENERATION_FAILED,
        resolution=Resolution(cause=SECRET_CAUSE, action="휠 교체", result="정상 주행"),
    )
    monkeypatch.setattr(service.rdb, "fetch_case_detail", lambda case_id: detail)

    result = service.get_customer_case("SC-1")

    dumped = result.model_dump_json()
    assert SECRET_CAUSE not in dumped
    assert "generation_failed" not in dumped and "guide_failure" not in type(result).model_fields
    assert (result.action, result.result) == ("휠 교체", "정상 주행")


def test_get_customer_case_before_completion_has_no_result(monkeypatch):
    monkeypatch.setattr(service.rdb, "fetch_case_detail", lambda case_id: _detail(status=CaseStatus.RECEIVED))
    result = service.get_customer_case("SC-1")
    assert (result.action, result.result) == (None, None)


def test_get_customer_case_hides_seed_history(monkeypatch):
    monkeypatch.setattr(service.rdb, "fetch_case_detail", lambda case_id: _detail(source=Source.SEED))
    with pytest.raises(NotFoundError):
        service.get_customer_case("SEED-1")


def test_get_customer_case_unknown_id(monkeypatch):
    def missing(case_id):
        raise NotFoundError(case_id)

    monkeypatch.setattr(service.rdb, "fetch_case_detail", missing)
    with pytest.raises(NotFoundError):
        service.get_customer_case("SC-없음")


def test_get_citation_text_reads_document(monkeypatch):
    calls = []

    def get_document(kind, ref_id):
        calls.append((kind, ref_id))
        return Document(page_content="[가상 원문] 4.1 안전장치", metadata={})

    monkeypatch.setattr(service.vectordb, "get_document", get_document)
    citation = Citation(kind=CitationKind.MANUAL, ref_id="manual:rtv_rev2_1:15:1", locator="p.15 · 4.1")

    assert service.get_citation_text(citation) == "[가상 원문] 4.1 안전장치"
    assert calls == [(CitationKind.MANUAL, "manual:rtv_rev2_1:15:1")]


@pytest.mark.parametrize("error", [NotFoundError("없음"), DependencyError("저장소 장애")])
def test_get_citation_text_propagates_errors(monkeypatch, error):
    def fail(kind, ref_id):
        raise error

    monkeypatch.setattr(service.vectordb, "get_document", fail)
    with pytest.raises(type(error)):
        service.get_citation_text(Citation(kind=CitationKind.MANUAL, ref_id="manual:x:1:1", locator="p.1"))


@pytest.mark.parametrize(("name", "rdb_name"), [
    ("resolve_self_guide", "resolve_self_guide_case"),
    ("request_support", "escalate_self_guide_case"),
])
def test_self_guide_answers_delegate_to_rdb(monkeypatch, name, rdb_name):
    calls = []
    monkeypatch.setattr(service.rdb, "fetch_case_detail", lambda case_id: _detail(status=CaseStatus.IN_PROGRESS))
    monkeypatch.setattr(service.rdb, rdb_name, lambda case_id: calls.append(case_id))

    assert getattr(service, name)("SC-1") is None
    assert calls == ["SC-1"]


@pytest.mark.parametrize(("name", "rdb_name"), [
    ("resolve_self_guide", "resolve_self_guide_case"),
    ("request_support", "escalate_self_guide_case"),
])
def test_self_guide_answers_reject_seed_history_and_propagate_state_errors(monkeypatch, name, rdb_name):
    calls = []

    def already_handled(case_id):
        calls.append(case_id)
        raise InvalidStateError("이미 처리된 셀프 가이드 건")

    monkeypatch.setattr(service.rdb, rdb_name, already_handled)
    monkeypatch.setattr(service.rdb, "fetch_case_detail", lambda case_id: _detail(source=Source.SEED))
    with pytest.raises(NotFoundError):
        getattr(service, name)("SEED-1")
    assert calls == []  # 시드 이력은 고객에게 없는 건이라 DB를 바꾸지 않는다

    monkeypatch.setattr(service.rdb, "fetch_case_detail", lambda case_id: _detail(status=CaseStatus.IN_PROGRESS))
    with pytest.raises(InvalidStateError):
        getattr(service, name)("SC-1")
