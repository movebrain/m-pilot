"""신고 접수 저장(save_self_guide_case, save_support_case) 테스트.

DB가 필요한 테스트는 실제 MySQL 테스트 DB를 쓴다(`rdb_db` fixture, tests/conftest.py).
저장한 값은 조회 함수와 표 직접 읽기로 함께 확인한다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, insert, select

from mpilot.db.schemas import error_codes, resolutions, service_cases
from mpilot.models import (
    Answer,
    AnswerStep,
    CaseInput,
    CaseStatus,
    Citation,
    CitationKind,
    DependencyError,
    Equipment,
    GuideFailure,
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
HELD = Answer(verdict=Verdict.INSUFFICIENT_EVIDENCE, steps=[], notice="근거가 부족합니다.")
SRM_S101 = CaseInput(equipment=Equipment.SRM, code="S101", memo="목적지 셀 앞 정지")


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


def stored_row(rdb, case_id):
    """`service_cases`에 저장된 행을 그대로 읽는다(DB 컬럼 이름 확인용)."""
    with rdb._get_engine().connect() as conn:
        return conn.execute(select(service_cases).where(service_cases.c.case_id == case_id)).first()


def count_rows(rdb, table):
    """표의 행 수."""
    with rdb._get_engine().connect() as conn:
        return conn.execute(select(func.count()).select_from(table)).scalar()


def assert_recent_utc(value):
    """방금 저장한 UTC 시각인지 확인한다(초 단위로 저장하므로 넉넉히 본다)."""
    now = datetime.now(UTC)
    assert value.tzinfo == UTC
    assert now - timedelta(minutes=1) <= value <= now + timedelta(seconds=1)


# ---------------------------------------------------------------------------
# save_support_case
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("guide_failure", [None, GuideFailure.HELD, GuideFailure.GENERATION_FAILED])
def test_save_support_case_stores_received(rdb, guide_failure):
    detail = rdb.save_support_case("SC-0000AAAA", SRM_S101, guide_failure)

    case = detail.service_case
    assert case.case_id == "SC-0000AAAA"
    assert (case.source, case.route, case.status) == (Source.REPORTED, Route.SUPPORT, CaseStatus.RECEIVED)
    assert case.guide_failure == guide_failure
    assert case.guidance_answer is None
    assert case.memo == "목적지 셀 앞 정지"
    assert case.completed_at is None
    assert_recent_utc(case.created_at)
    assert detail.error_code.name == "X축 위치 편차 초과"
    assert detail.resolution is None


def test_save_support_case_matches_fetch(rdb):
    saved = rdb.save_support_case("SC-0000AAAA", SRM_S101, GuideFailure.HELD)

    assert rdb.fetch_case_detail("SC-0000AAAA") == saved
    assert rdb.fetch_cases() == [saved]


def test_save_support_case_uses_db_column_names(rdb):
    rdb.save_support_case("SC-0000AAAA", SRM_S101, GuideFailure.GENERATION_FAILED)

    row = stored_row(rdb, "SC-0000AAAA")
    assert row.customer_memo == "목적지 셀 앞 정지"  # 모델 필드 memo → DB 컬럼 customer_memo
    assert row.self_guide_answer is None
    assert row.guide_failure == "generation_failed"
    assert row.created_at.tzinfo is None  # DATETIME에는 시간대 없이 저장


def test_save_support_case_without_memo(rdb):
    data = CaseInput(equipment=Equipment.SRM, code="S101", memo=None)

    detail = rdb.save_support_case("SC-0000AAAA", data, None)

    assert detail.service_case.memo is None


def test_save_support_case_rejects_unknown_guide_failure(rdb):
    with pytest.raises(ValidationError):
        rdb.save_support_case("SC-0000AAAA", SRM_S101, "timeout")
    assert count_rows(rdb, service_cases) == 0


# ---------------------------------------------------------------------------
# save_self_guide_case
# ---------------------------------------------------------------------------


def test_save_self_guide_case_stores_completed(rdb):
    detail = rdb.save_self_guide_case("SC-0000BBBB", SRM_S101, ANSWERED)

    case = detail.service_case
    assert (case.source, case.route, case.status) == (Source.REPORTED, Route.SELF_GUIDE, CaseStatus.COMPLETED)
    assert case.guide_failure is None
    assert case.guidance_answer == ANSWERED  # 내용·단계·출처가 그대로 되돌아온다
    assert_recent_utc(case.created_at)
    assert case.completed_at == case.created_at
    assert detail.resolution is None


def test_save_self_guide_case_matches_fetch(rdb):
    saved = rdb.save_self_guide_case("SC-0000BBBB", SRM_S101, ANSWERED)

    assert rdb.fetch_case_detail("SC-0000BBBB") == saved


def test_save_self_guide_case_stores_answer_json(rdb):
    rdb.save_self_guide_case("SC-0000BBBB", SRM_S101, ANSWERED)

    row = stored_row(rdb, "SC-0000BBBB")
    # 모델 필드 guidance_answer → DB 컬럼 self_guide_answer(Answer JSON)
    assert Answer.model_validate_json(row.self_guide_answer) == ANSWERED


def test_save_self_guide_case_creates_no_resolution(rdb):
    rdb.save_self_guide_case("SC-0000BBBB", SRM_S101, ANSWERED)

    assert count_rows(rdb, resolutions) == 0


def test_save_self_guide_case_not_in_resolved_cases(rdb):
    # 셀프 가이드는 검색 근거(history)로 재사용하지 않는다.
    rdb.save_self_guide_case("SC-0000BBBB", SRM_S101, ANSWERED)

    assert rdb.fetch_resolved_cases() == []


def test_save_self_guide_case_rejects_held_answer(rdb):
    with pytest.raises(ValidationError):
        rdb.save_self_guide_case("SC-0000BBBB", SRM_S101, HELD)
    assert count_rows(rdb, service_cases) == 0


# ---------------------------------------------------------------------------
# 공통 오류
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("save", ["support", "self_guide"])
def test_save_rejects_unregistered_code(rdb, save):
    data = CaseInput(equipment=Equipment.SRM, code="S999", memo=None)

    with pytest.raises(ValidationError):
        if save == "support":
            rdb.save_support_case("SC-0000CCCC", data, None)
        else:
            rdb.save_self_guide_case("SC-0000CCCC", data, ANSWERED)
    assert count_rows(rdb, service_cases) == 0


def test_save_rejects_code_of_other_equipment(rdb):
    # S101은 SRM 코드다. (설비, 코드)가 키라 AGV로 신고하면 미등록 코드다.
    data = CaseInput(equipment=Equipment.AGV, code="S101", memo=None)

    with pytest.raises(ValidationError):
        rdb.save_support_case("SC-0000CCCC", data, None)


def test_save_rejects_too_long_memo(rdb):
    # CaseInput 검증을 거치지 않은 값이 들어와도 저장 직전에 막는다.
    data = CaseInput.model_construct(equipment=Equipment.SRM, code="S101", memo="가" * 2001)

    with pytest.raises(ValidationError):
        rdb.save_support_case("SC-0000CCCC", data, None)
    assert count_rows(rdb, service_cases) == 0


def test_save_accepts_memo_at_limit(rdb):
    data = CaseInput(equipment=Equipment.SRM, code="S101", memo="가" * 2000)

    detail = rdb.save_support_case("SC-0000CCCC", data, None)

    assert len(detail.service_case.memo) == 2000


@pytest.mark.parametrize("save", ["support", "self_guide"])
def test_save_duplicate_case_id_is_dependency_error(rdb, save):
    rdb.save_support_case("SC-0000DDDD", SRM_S101, None)

    with pytest.raises(DependencyError):
        if save == "support":
            rdb.save_support_case("SC-0000DDDD", SRM_S101, None)
        else:
            rdb.save_self_guide_case("SC-0000DDDD", SRM_S101, ANSWERED)

    # 먼저 저장한 건은 그대로 남는다(재시도·덮어쓰기 없음).
    assert count_rows(rdb, service_cases) == 1
    assert rdb.fetch_case_detail("SC-0000DDDD").service_case.route == Route.SUPPORT


# ---------------------------------------------------------------------------
# DB 없이 도는 테스트
# ---------------------------------------------------------------------------


def test_save_requires_connect():
    from mpilot.db import rdb as rdb_module

    assert rdb_module._engine is None
    with pytest.raises(DependencyError):
        rdb_module.save_support_case("SC-0000EEEE", SRM_S101, None)
    with pytest.raises(DependencyError):
        rdb_module.save_self_guide_case("SC-0000EEEE", SRM_S101, ANSWERED)


def test_save_self_guide_checks_verdict_before_db():
    # 연결 전이라도 판정 검사가 먼저라 ValidationError가 난다.
    from mpilot.db import rdb as rdb_module

    with pytest.raises(ValidationError):
        rdb_module.save_self_guide_case("SC-0000EEEE", SRM_S101, HELD)
