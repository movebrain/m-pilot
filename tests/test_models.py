"""공통 규칙 함수 테스트(spec §2.10 경로 결정·상태 전이)."""

from datetime import datetime

import pytest

from mpilot.models import (
    ActionOwner,
    CaseStatus,
    Equipment,
    ErrorCode,
    InvalidStateError,
    Route,
    ServiceCase,
    Severity,
    Source,
    is_self_guide_candidate,
    raise_if_case_completed,
)


def make_code(severity=Severity.LOW, owner=ActionOwner.CUSTOMER) -> ErrorCode:
    return ErrorCode(
        equipment=Equipment.AGV,
        code="R001",
        name="테스트",
        severity=severity,
        action_owner=owner,
        cause="원인",
        action="조치",
        manual_id="agv",
        page=1,
        section="1",
    )


def make_case(status: CaseStatus) -> ServiceCase:
    return ServiceCase(
        case_id="SC-00000001",
        equipment=Equipment.AGV,
        code="R001",
        memo=None,
        source=Source.REPORTED,
        status=status,
        route=Route.SUPPORT,
        guide_failure=None,
        guidance_answer=None,
        created_at=datetime(2026, 1, 1),
        completed_at=None,
    )


@pytest.mark.parametrize(
    ("severity", "owner", "expected"),
    [
        (Severity.HIGH, ActionOwner.CUSTOMER, False),  # 심각도 상이면 조치 구분과 무관
        (Severity.HIGH, ActionOwner.ENGINEER, False),
        (Severity.MEDIUM, ActionOwner.CUSTOMER, True),
        (Severity.LOW, ActionOwner.CUSTOMER, True),
        (Severity.MEDIUM, ActionOwner.ENGINEER, False),
        (Severity.LOW, ActionOwner.ENGINEER, False),
    ],
)
def test_is_self_guide_candidate(severity, owner, expected):
    assert is_self_guide_candidate(make_code(severity, owner)) is expected


@pytest.mark.parametrize("status", [CaseStatus.RECEIVED, CaseStatus.IN_PROGRESS])
def test_raise_if_case_completed_passes_open_case(status):
    assert raise_if_case_completed(make_case(status)) is None


def test_raise_if_case_completed_blocks_completed_case():
    with pytest.raises(InvalidStateError, match="이미 완료"):
        raise_if_case_completed(make_case(CaseStatus.COMPLETED))
