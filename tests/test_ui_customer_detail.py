"""고객 신고 상세 화면의 표시 변환 테스트."""

from __future__ import annotations

from datetime import datetime, timezone

from mpilot.models import (
    Answer,
    AnswerStep,
    CaseStatus,
    Citation,
    CitationKind,
    CustomerCaseResult,
    Equipment,
)
from mpilot.ui import customer_report_detail as detail

C1 = Citation(kind=CitationKind.MANUAL, ref_id="manual:agv_rev1_1:8:2", locator="p.8 · 2.4 배터리")
C2 = Citation(kind=CitationKind.MANUAL, ref_id="manual:agv_rev1_1:8:3", locator="p.8 · 2.4 배터리")


def _case(**kw) -> CustomerCaseResult:
    base = dict(
        case_id="SC-1", equipment=Equipment.AGV, code="A120", error_name="배터리 저전압", memo=None,
        status=CaseStatus.RECEIVED, created_at=datetime(2026, 10, 27, 1, 0, tzinfo=timezone.utc),
        guidance_answer=None, action=None, result=None,
    )
    base.update(kw)
    return CustomerCaseResult(**base)


GUIDE = Answer(verdict="answered", steps=[
    AnswerStep(text="충전 스테이션으로 복귀시킨다.", citations=[C1]),
    AnswerStep(text="충전 중에는 이동하지 않는다.", citations=[C1, C2]),
], notice=None)


def test_summary_shows_basic_fields():
    text = detail.summary_markdown(_case(memo="충전 안 됨"))
    for part in ["SC-1", "AGV", "A120", "배터리 저전압", "접수", "2026-10-27 10:00", "충전 안 됨"]:
        assert part in text


def test_self_guide_shows_steps_and_new_report_notice():
    text = detail.result_markdown(_case(status=CaseStatus.COMPLETED, guidance_answer=GUIDE))
    assert "충전 스테이션으로 복귀시킨다." in text
    assert detail.SELF_GUIDE_NOTICE in text


def test_engineer_case_before_completion_shows_waiting():
    assert detail.result_markdown(_case()) == detail.WAITING_NOTICE


def test_engineer_case_after_completion_shows_action_and_result():
    text = detail.result_markdown(_case(status=CaseStatus.COMPLETED, action="휠 교체", result="정상 주행"))
    assert "휠 교체" in text and "정상 주행" in text


def test_citations_are_unique_in_order():
    assert detail.citations_of(_case(guidance_answer=GUIDE)) == [C1, C2]
    assert detail.citations_of(_case()) == []
