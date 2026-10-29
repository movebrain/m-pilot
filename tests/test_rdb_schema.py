"""rdb 공통 부분(스키마, connect, reset_schema, 시각·JSON·행 변환) 테스트.

DB가 필요한 테스트는 `rdb_db` fixture(tests/conftest.py)를 쓴다. 나머지는 DB 없이 돈다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import inspect, insert, select, update

from mpilot.db import rdb
from mpilot.db.rdb import _common
from mpilot.db.schemas import chat_turns, error_codes, metadata, resolutions, service_cases
from mpilot.models import (
    Answer,
    CaseDetail,
    DependencyError,
    NotFoundError,
    Resolution,
    ValidationError,
    Verdict,
)

KST = timezone(timedelta(hours=9))


def add_error_code(conn, equipment="SRM", code="S101"):
    """에러코드 표에 한 행을 넣는다."""
    conn.execute(
        insert(error_codes).values(
            equipment=equipment,
            code=code,
            name="X축 위치 편차 초과",
            severity="중",
            action_owner="고객사 조치",
            cause="원인 문장",
            action="조치 문장",
            manual_id="HMX-SRM-TSM-001",
            page=12,
            section="9.4 에러 코드 일람",
            customer_procedure_ref_ids='["manual:HMX-SRM-TSM-001:12:1"]',
            required_safety_rules='["전원을 차단한다"]',
        )
    )


def add_case(conn, case_id="SC-0001", status="received", route="support", answer_json=None):
    """신고 한 건을 넣는다(설비 SRM, 코드 S101)."""
    completed_at = None
    if status == "completed":
        completed_at = datetime(2026, 10, 27, 1, 0, 0)
    conn.execute(
        insert(service_cases).values(
            case_id=case_id,
            equipment="SRM",
            code="S101",
            customer_memo="크레인이 멈췄어요",
            source="reported",
            status=status,
            route=route,
            guide_failure=None,
            self_guide_answer=answer_json,
            created_at=datetime(2026, 10, 27, 0, 0, 0),
            completed_at=completed_at,
        )
    )


# ---------------------------------------------------------------------------
# 스키마 정의(DB 없이)
# ---------------------------------------------------------------------------


def test_schema_has_only_four_tables():
    # 고객·엔지니어 테이블은 포함 여부가 정해지지 않아 만들지 않는다.
    assert sorted(metadata.tables) == ["chat_turns", "error_codes", "resolutions", "service_cases"]


def test_service_cases_columns_follow_erd_without_customer_engineer():
    names = [c.name for c in service_cases.columns]
    assert names == [
        "case_id",
        "equipment",
        "code",
        "customer_memo",
        "source",
        "status",
        "route",
        "guide_failure",
        "self_guide_answer",
        "created_at",
        "completed_at",
    ]
    assert "customer_id" not in names
    assert "engineer_id" not in names


def test_nullable_columns_follow_spec():
    assert service_cases.c.customer_memo.nullable
    assert service_cases.c.guide_failure.nullable
    assert service_cases.c.self_guide_answer.nullable
    assert service_cases.c.completed_at.nullable
    assert not service_cases.c.created_at.nullable
    assert not error_codes.c.customer_procedure_ref_ids.nullable
    assert not error_codes.c.required_safety_rules.nullable
    assert not chat_turns.c.answer.nullable


def test_primary_keys():
    assert [c.name for c in error_codes.primary_key] == ["equipment", "code"]
    assert [c.name for c in service_cases.primary_key] == ["case_id"]
    assert [c.name for c in resolutions.primary_key] == ["case_id"]
    assert [c.name for c in chat_turns.primary_key] == ["turn_id"]


# ---------------------------------------------------------------------------
# connect·연결 전 호출(DB 없이)
# ---------------------------------------------------------------------------


def test_connect_rejects_bad_url():
    with pytest.raises(ValidationError):
        rdb.connect("이건 URL이 아님")


def test_connect_rejects_unknown_driver():
    with pytest.raises(ValidationError):
        rdb.connect("mysql+nodriver://user:pw@127.0.0.1/db")


def test_connect_unreachable_db_is_dependency_error_without_password(monkeypatch):
    monkeypatch.setattr(_common, "_engine", None)
    with pytest.raises(DependencyError) as error:
        rdb.connect("mysql+pymysql://user:secret-pw@127.0.0.1:1/db?connect_timeout=2")
    assert "secret-pw" not in str(error.value)
    assert _common._engine is None  # 실패하면 엔진을 보관하지 않는다


def test_functions_before_connect_are_dependency_error(monkeypatch):
    monkeypatch.setattr(_common, "_engine", None)
    with pytest.raises(DependencyError):
        rdb.reset_schema()
    with pytest.raises(DependencyError):
        rdb.fetch_chat_turns("SC-0001")


# ---------------------------------------------------------------------------
# 시각·JSON 변환(DB 없이)
# ---------------------------------------------------------------------------


def test_to_db_time_converts_to_utc_without_timezone():
    value = datetime(2026, 3, 2, 9, 15, 30, 123456, tzinfo=KST)
    assert _common._to_db_time(value) == datetime(2026, 3, 2, 0, 15, 30)


def test_to_db_time_rejects_naive_time():
    with pytest.raises(ValidationError):
        _common._to_db_time(datetime(2026, 3, 2, 9, 15))


def test_from_db_time_attaches_utc():
    assert _common._from_db_time(datetime(2026, 3, 2, 0, 15)) == datetime(2026, 3, 2, 0, 15, tzinfo=UTC)
    assert _common._from_db_time(None) is None


def test_now_for_db_has_no_timezone_and_microseconds():
    now = _common._now_for_db()
    assert now.tzinfo is None
    assert now.microsecond == 0


def test_to_json_list_keeps_korean():
    assert _common._to_json_list(["전원 차단"]) == '["전원 차단"]'
    assert _common._to_json_list([]) == "[]"


# ---------------------------------------------------------------------------
# 실제 DB
# ---------------------------------------------------------------------------


def test_reset_schema_creates_four_empty_tables(rdb_db):
    names = sorted(inspect(_common._engine).get_table_names())
    assert names == ["chat_turns", "error_codes", "resolutions", "service_cases"]


def test_reset_schema_creates_foreign_keys(rdb_db):
    inspector = inspect(_common._engine)
    case_fk = inspector.get_foreign_keys("service_cases")[0]
    assert case_fk["referred_table"] == "error_codes"
    assert case_fk["constrained_columns"] == ["equipment", "code"]
    assert inspector.get_foreign_keys("resolutions")[0]["referred_table"] == "service_cases"
    assert inspector.get_foreign_keys("chat_turns")[0]["referred_table"] == "service_cases"


def test_reset_schema_deletes_existing_rows(rdb_db):
    with _common._engine.begin() as conn:
        add_error_code(conn)
        add_case(conn)
    rdb_db.reset_schema()
    with _common._engine.connect() as conn:
        assert conn.execute(select(service_cases)).all() == []
        assert conn.execute(select(error_codes)).all() == []


def test_case_id_is_case_sensitive(rdb_db):
    with _common._engine.begin() as conn:
        add_error_code(conn)
        add_case(conn, case_id="SC-ABCD1234")
    with _common._engine.connect() as conn:
        row = conn.execute(
            select(service_cases).where(service_cases.c.case_id == "sc-abcd1234")
        ).first()
    assert row is None


def test_foreign_key_blocks_unknown_error_code(rdb_db):
    with pytest.raises(Exception):
        with _common._engine.begin() as conn:
            add_case(conn)  # 에러코드 표에 S101이 없다


def test_korean_text_round_trip(rdb_db):
    with _common._engine.begin() as conn:
        add_error_code(conn)
    with _common._engine.connect() as conn:
        row = conn.execute(select(error_codes)).first()
    assert _common._row_to_error_code(row).required_safety_rules == ["전원을 차단한다"]


def test_load_case_detail_without_resolution(rdb_db):
    with _common._engine.begin() as conn:
        add_error_code(conn)
        add_case(conn)
        detail = _common._load_case_detail(conn, "SC-0001")
    assert isinstance(detail, CaseDetail)
    assert detail.service_case.memo == "크레인이 멈췄어요"
    assert detail.service_case.created_at == datetime(2026, 10, 27, 0, 0, tzinfo=UTC)
    assert detail.error_code.customer_procedure_ref_ids == ["manual:HMX-SRM-TSM-001:12:1"]
    assert detail.resolution is None


def test_load_case_detail_with_resolution_and_guidance(rdb_db):
    answer = Answer(verdict=Verdict.ANSWERED, steps=[], notice=None)
    with _common._engine.begin() as conn:
        add_error_code(conn)
        add_case(conn, status="completed", route="self_guide", answer_json=answer.model_dump_json())
        add_case(conn, case_id="SC-0002", status="completed")
        conn.execute(
            insert(resolutions).values(case_id="SC-0002", cause="원인", action="조치", result="결과")
        )
        self_guide = _common._load_case_detail(conn, "SC-0001")
        support = _common._load_case_detail(conn, "SC-0002")
    assert self_guide.service_case.guidance_answer == answer
    assert support.resolution == Resolution(cause="원인", action="조치", result="결과")


def test_load_case_detail_unknown_case_is_not_found(rdb_db):
    with _common._engine.connect() as conn:
        with pytest.raises(NotFoundError):
            _common._load_case_detail(conn, "SC-없음")


def test_broken_stored_value_is_dependency_error(rdb_db):
    with _common._engine.begin() as conn:
        add_error_code(conn)
        add_case(conn)
        conn.execute(
            update(error_codes).values(customer_procedure_ref_ids="깨진 JSON")
        )
        conn.execute(update(service_cases).values(status="unknown"))
    with _common._engine.connect() as conn:
        with pytest.raises(DependencyError):
            _common._row_to_error_code(conn.execute(select(error_codes)).first())
        with pytest.raises(DependencyError):
            _common._row_to_service_case(conn.execute(select(service_cases)).first())
