"""신고 목록·상세 조회(fetch_cases, fetch_case_detail) 테스트.

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
    NotFoundError,
    Route,
    Source,
    ValidationError,
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
            )
        )
    return rdb_db


def add_case(rdb, case_id, status="completed", route="support", source="seed", resolution=True,
             memo="현장 증상", created_at=datetime(2026, 7, 1, 9, 0)):
    """신고 한 건을 넣는다(설비 SRM, 코드 S101). `resolution`이면 조치 기록도 넣는다."""
    completed_at = created_at.replace(hour=23) if status == "completed" else None
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
                created_at=created_at,
                completed_at=completed_at,
            )
        )
        if resolution:
            conn.execute(
                insert(resolutions).values(case_id=case_id, cause="센서 오염", action="센서 청소",
                                           result="정상 동작 확인")
            )


def add_mixed_cases(rdb):
    """상태·출처가 섞인 신고 4건을 넣는다(신고 시각이 모두 다르다)."""
    add_case(rdb, "SRM-H-0001", created_at=datetime(2026, 7, 1, 9, 0))  # 시드 완료
    add_case(rdb, "SC-RECV0001", status="received", source="reported", resolution=False,
             created_at=datetime(2026, 7, 3, 9, 0))  # 접수
    add_case(rdb, "SC-PROG0001", status="in_progress", source="reported", resolution=False,
             created_at=datetime(2026, 7, 2, 9, 0))  # 조치 중
    add_case(rdb, "SC-SELF0001", route="self_guide", source="reported", resolution=False,
             created_at=datetime(2026, 7, 4, 9, 0))  # 셀프 가이드 완료


def ids(details):
    """CaseDetail 목록의 접수번호 목록."""
    return [d.service_case.case_id for d in details]


# ---------------------------------------------------------------------------
# fetch_case_detail
# ---------------------------------------------------------------------------


def test_fetch_case_detail_returns_case_code_and_resolution(rdb):
    add_case(rdb, "SRM-H-0001")

    detail = rdb.fetch_case_detail("SRM-H-0001")

    case = detail.service_case
    assert case.case_id == "SRM-H-0001"
    assert case.equipment == Equipment.SRM
    assert (case.source, case.route, case.status) == (Source.SEED, Route.SUPPORT, CaseStatus.COMPLETED)
    assert case.memo == "현장 증상"  # DB 컬럼 customer_memo → 모델 필드 memo
    assert case.created_at == datetime(2026, 7, 1, 9, 0, tzinfo=UTC)
    assert detail.error_code.code == "S101"
    assert detail.resolution.action == "센서 청소"


def test_fetch_case_detail_self_guide_has_answer_and_no_resolution(rdb):
    add_case(rdb, "SC-SELF0001", route="self_guide", source="reported", resolution=False)

    detail = rdb.fetch_case_detail("SC-SELF0001")

    assert detail.resolution is None
    assert detail.service_case.guidance_answer == ANSWERED  # self_guide_answer → guidance_answer


def test_fetch_case_detail_not_found(rdb):
    with pytest.raises(NotFoundError):
        rdb.fetch_case_detail("SC-NOPE0001")


# ---------------------------------------------------------------------------
# fetch_cases
# ---------------------------------------------------------------------------


def test_fetch_cases_sorted_newest_first(rdb):
    add_mixed_cases(rdb)

    assert ids(rdb.fetch_cases()) == ["SC-SELF0001", "SC-RECV0001", "SC-PROG0001", "SRM-H-0001"]


def test_fetch_cases_same_created_at_sorted_by_case_id_desc(rdb):
    for case_id in ["SRM-H-0002", "SRM-H-0001", "SRM-H-0003"]:
        add_case(rdb, case_id)

    assert ids(rdb.fetch_cases()) == ["SRM-H-0003", "SRM-H-0002", "SRM-H-0001"]


def test_fetch_cases_fills_resolution_only_when_present(rdb):
    add_mixed_cases(rdb)

    details = {d.service_case.case_id: d for d in rdb.fetch_cases()}

    assert details["SRM-H-0001"].resolution.cause == "센서 오염"
    assert details["SC-RECV0001"].resolution is None
    assert details["SC-SELF0001"].resolution is None
    assert details["SC-SELF0001"].service_case.guidance_answer == ANSWERED
    assert all(d.error_code.code == "S101" for d in details.values())


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (CaseStatus.RECEIVED, ["SC-RECV0001"]),
        (CaseStatus.IN_PROGRESS, ["SC-PROG0001"]),
        (CaseStatus.COMPLETED, ["SC-SELF0001", "SRM-H-0001"]),
    ],
)
def test_fetch_cases_filters_by_status(rdb, status, expected):
    add_mixed_cases(rdb)

    assert ids(rdb.fetch_cases(status=status)) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (Source.SEED, ["SRM-H-0001"]),
        (Source.REPORTED, ["SC-SELF0001", "SC-RECV0001", "SC-PROG0001"]),
    ],
)
def test_fetch_cases_filters_by_source(rdb, source, expected):
    add_mixed_cases(rdb)

    assert ids(rdb.fetch_cases(source=source)) == expected


def test_fetch_cases_filters_by_status_and_source(rdb):
    add_mixed_cases(rdb)

    result = rdb.fetch_cases(status=CaseStatus.COMPLETED, source=Source.REPORTED)

    assert ids(result) == ["SC-SELF0001"]


def test_fetch_cases_accepts_plain_string_filter(rdb):
    add_mixed_cases(rdb)

    assert ids(rdb.fetch_cases(status="received")) == ["SC-RECV0001"]


def test_fetch_cases_empty(rdb):
    assert rdb.fetch_cases() == []


# ---------------------------------------------------------------------------
# DB 없이 도는 테스트
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kwargs", [{"status": "done"}, {"source": "imported"}])
def test_fetch_cases_rejects_unknown_filter(kwargs):
    # 필터 검증은 DB 조회 전에 한다.
    from mpilot.db import rdb as rdb_module

    with pytest.raises(ValidationError):
        rdb_module.fetch_cases(**kwargs)


def test_fetch_requires_connect():
    from mpilot.db import rdb as rdb_module

    assert rdb_module._engine is None
    with pytest.raises(DependencyError):
        rdb_module.fetch_cases()
    with pytest.raises(DependencyError):
        rdb_module.fetch_case_detail("SRM-H-0001")


def test_fetch_failure_becomes_dependency_error(monkeypatch):
    # 조회 중 SQLAlchemy 오류가 나면 DependencyError로 바꿔 낸다.
    from sqlalchemy.exc import OperationalError

    from mpilot.db import rdb as rdb_module

    class BrokenEngine:
        def connect(self):
            raise OperationalError("SELECT", {}, Exception("연결 끊김"))

    monkeypatch.setattr(rdb_module, "_engine", BrokenEngine())
    with pytest.raises(DependencyError):
        rdb_module.fetch_cases()
    with pytest.raises(DependencyError):
        rdb_module.fetch_case_detail("SRM-H-0001")
