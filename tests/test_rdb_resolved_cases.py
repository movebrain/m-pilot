"""조치 기록이 있는 신고 조회(fetch_resolved_cases) 테스트.

DB가 필요한 테스트는 실제 MySQL 테스트 DB를 쓴다(`rdb_db` fixture, tests/conftest.py).
신고·조치 기록은 표에 직접 넣는다(시드 적재 함수에 기대지 않는다).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import insert

from mpilot.db.schemas import error_codes, resolutions, service_cases
from mpilot.models import (
    Answer,
    AnswerStep,
    CaseStatus,
    Citation,
    CitationKind,
    DependencyError,
    Equipment,
    Route,
    Source,
    Verdict,
)

ANSWERED = Answer(
    verdict=Verdict.ANSWERED,
    steps=[
        AnswerStep(
            text="반사판을 닦는다",
            citations=[
                Citation(kind=CitationKind.MANUAL, ref_id="manual:srm_manual:12:1", locator="p.12 11.1")
            ],
        )
    ],
    notice=None,
)


@pytest.fixture
def rdb(rdb_db):
    """에러코드 표에 SRM S101 한 행이 있는 테스트 DB에 연결된 `rdb` 모듈."""
    with rdb_db._get_engine().begin() as conn:
        conn.execute(
            insert(error_codes).values(
                equipment="SRM",
                code="S101",
                name="X축 위치 편차 초과",
                severity="중",
                action_owner="고객사 조치",
                cause="원인 문장",
                action="조치 문장",
                manual_id="srm_manual",
                page=12,
                section="11.1",
                required_safety_rules="[]",
            )
        )
    return rdb_db


def add_case(rdb, case_id, status="completed", route="support", source="seed", resolution=True,
             memo="현장 증상"):
    """신고 한 건을 넣는다(설비 SRM, 코드 S101). `resolution`이면 조치 기록도 넣는다."""
    completed_at = datetime(2026, 7, 1, 10, 0) if status == "completed" else None
    self_guide_answer = ANSWERED.model_dump_json() if route == "self_guide" else None
    with rdb._get_engine().begin() as conn:
        conn.execute(
            insert(service_cases).values(
                case_id=case_id,
                equipment="SRM",
                code="S101",
                customer_memo=memo,
                source=source,
                status=status,
                route=route,
                guide_failure=None,
                self_guide_answer=self_guide_answer,
                created_at=datetime(2026, 7, 1, 9, 0),
                completed_at=completed_at,
            )
        )
        if resolution:
            conn.execute(
                insert(resolutions).values(case_id=case_id, cause="센서 오염", action="센서 청소",
                                           result="정상 동작 확인")
            )


def test_fetch_resolved_cases_returns_case_detail(rdb):
    add_case(rdb, "SRM-H-0001")

    [detail] = rdb.fetch_resolved_cases()

    case = detail.service_case
    assert case.case_id == "SRM-H-0001"
    assert case.equipment == Equipment.SRM
    assert (case.source, case.route, case.status) == (Source.SEED, Route.SUPPORT, CaseStatus.COMPLETED)
    assert case.memo == "현장 증상"  # DB 컬럼 customer_memo → 모델 필드 memo
    assert case.guidance_answer is None
    assert detail.error_code.code == "S101"
    assert (detail.resolution.cause, detail.resolution.action, detail.resolution.result) == (
        "센서 오염", "센서 청소", "정상 동작 확인"
    )


def test_fetch_resolved_cases_attaches_utc(rdb):
    add_case(rdb, "SRM-H-0001")

    case = rdb.fetch_resolved_cases()[0].service_case

    # DATETIME에는 시간대 없이 저장되어 있고, 꺼낼 때 UTC가 붙는다.
    assert case.created_at == datetime(2026, 7, 1, 9, 0, tzinfo=UTC)
    assert case.completed_at == datetime(2026, 7, 1, 10, 0, tzinfo=UTC)


def test_fetch_resolved_cases_excludes_cases_without_resolution(rdb):
    add_case(rdb, "SRM-H-0001")  # 시드 완료 건(조치 기록 있음)
    add_case(rdb, "SC-RECV0001", status="received", source="reported", resolution=False)  # 접수
    add_case(rdb, "SC-PROG0001", status="in_progress", source="reported", resolution=False)  # 조치 중
    add_case(rdb, "SC-SELF0001", route="self_guide", source="reported", resolution=False)  # 셀프 가이드

    ids = [d.service_case.case_id for d in rdb.fetch_resolved_cases()]

    assert ids == ["SRM-H-0001"]


def test_fetch_resolved_cases_includes_reported_resolution(rdb):
    # 앱에서 엔지니어가 확정한 건도 시드와 함께 나온다.
    add_case(rdb, "SRM-H-0001")
    add_case(rdb, "SC-DONE0001", source="reported", memo=None)

    details = {d.service_case.case_id: d for d in rdb.fetch_resolved_cases()}

    assert set(details) == {"SRM-H-0001", "SC-DONE0001"}
    assert details["SC-DONE0001"].service_case.memo is None


def test_fetch_resolved_cases_sorted_by_case_id(rdb):
    for case_id in ["SRM-H-0003", "SRM-H-0001", "SRM-H-0002"]:
        add_case(rdb, case_id)

    ids = [d.service_case.case_id for d in rdb.fetch_resolved_cases()]

    assert ids == ["SRM-H-0001", "SRM-H-0002", "SRM-H-0003"]


def test_fetch_resolved_cases_empty(rdb):
    assert rdb.fetch_resolved_cases() == []


def test_fetch_resolved_cases_requires_connect():
    # DB가 없어도 도는 테스트: connect 전에 부르면 DependencyError.
    from mpilot.db import rdb as rdb_module

    assert rdb_module._engine is None
    with pytest.raises(DependencyError):
        rdb_module.fetch_resolved_cases()
