"""고객 신고 작성 화면의 입력 변환 테스트."""

from __future__ import annotations

import pytest

from mpilot.models import ActionOwner, Equipment, ErrorCode, Severity, ValidationError
from mpilot.ui import customer_report_form as form


def test_code_choices_label_and_value():
    code = ErrorCode(
        equipment=Equipment.SRM, code="S115", name="리프트 엔코더 편차", severity=Severity.MEDIUM,
        action_owner=ActionOwner.ENGINEER, cause="원인", action="조치", manual_id="srm_rev1_2", page=41,
        section="6.3 승강부",
    )
    assert form.code_choices([code]) == [("S115 · 리프트 엔코더 편차", "S115")]


def test_build_case_input_trims_memo():
    data = form.build_case_input("RTV", "R206", "  주행 중 정지  ")
    assert (data.equipment, data.code, data.memo) == (Equipment.RTV, "R206", "주행 중 정지")


@pytest.mark.parametrize("memo", [None, "", "   "])
def test_build_case_input_empty_memo_is_none(memo):
    assert form.build_case_input("AGV", "A120", memo).memo is None


@pytest.mark.parametrize(("equipment", "code"), [(None, "A120"), ("AGV", None), ("", "")])
def test_build_case_input_requires_selection(equipment, code):
    with pytest.raises(ValidationError):
        form.build_case_input(equipment, code, None)


def test_build_case_input_rejects_long_memo():
    form.build_case_input("AGV", "A120", "가" * form.MEMO_MAX_LENGTH)  # 경계값은 통과
    with pytest.raises(ValidationError):
        form.build_case_input("AGV", "A120", "가" * (form.MEMO_MAX_LENGTH + 1))
