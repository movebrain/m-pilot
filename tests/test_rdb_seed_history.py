"""시드 이력 CSV 적재(load_seed_history_from_csv) 테스트.

DB가 필요한 테스트는 실제 MySQL 테스트 DB를 쓴다(`rdb_db` fixture, tests/conftest.py).
저장된 값은 `service_cases`·`resolutions` 표를 직접 읽어 확인한다.
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from mpilot.db.rdb import _common
from mpilot.db.schemas import error_codes, resolutions, service_cases
from mpilot.models import DependencyError, Equipment, ValidationError

CODE_HEADER = error_codes.c.keys()
SEED_HEADER = ["case_id", "equipment", "code", "memo", "cause", "action", "result",
               "created_at", "completed_at"]
SEED_PER_EQUIPMENT = 50
# 설비마다 코드 하나씩(시드 이력이 참조한다).
CODE_BY_EQUIPMENT = {
    Equipment.MINI_LOADER: "M401",
    Equipment.SRM: "S101",
    Equipment.AGV: "A361",
    Equipment.RTV: "R201",
}


def write_csv(path: Path, header: list[str], rows: list[dict]) -> Path:
    """머리글과 행으로 CSV 파일을 만든다."""
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)
    return path


def code_row(eq: Equipment, error_code: str) -> dict:
    """에러코드 CSV 한 행."""
    return {
        "equipment": eq.value,
        "code": error_code,
        "name": f"{error_code} 테스트 에러",
        "severity": "중",
        "action_owner": "고객사 조치",
        "cause": "원인",
        "action": "조치",
        "manual_id": f"{eq.name.lower()}_manual",
        "page": "12",
        "section": "11.1",
    }


def seed_row(equipment: Equipment, n: int, **overrides) -> dict:
    """시드 이력 CSV 한 행. 바꿀 칸만 `overrides`로 준다."""
    created = datetime(2026, 7, 1, 9, 0, tzinfo=UTC) + timedelta(hours=n)
    row = {
        "case_id": f"{equipment.name}-H-{n:04d}",
        "equipment": equipment.value,
        "code": CODE_BY_EQUIPMENT[equipment],
        "memo": "현장 증상",
        "cause": "원인",
        "action": "조치",
        "result": "해결",
        "created_at": created.isoformat(),
        "completed_at": (created + timedelta(minutes=30)).isoformat(),
    }
    row.update(overrides)
    return row


def valid_seed_rows() -> list[dict]:
    return [seed_row(eq, n) for eq in Equipment for n in range(1, SEED_PER_EQUIPMENT + 1)]


@pytest.fixture
def rdb(rdb_db, tmp_path):
    """에러코드 표(설비마다 1행)를 적재한 테스트 DB에 연결된 `rdb` 모듈."""
    rows = [code_row(eq, code) for eq, code in CODE_BY_EQUIPMENT.items()]
    rdb_db.load_error_codes_from_csv(write_csv(tmp_path / "codes.csv", CODE_HEADER, rows))
    return rdb_db


def read_rows(rdb, table) -> list:
    """표의 모든 행을 읽는다."""
    with _common._get_engine().connect() as conn:
        return conn.execute(select(table)).all()


def read_case(rdb, case_id: str):
    """`service_cases`에서 접수번호 한 행을 읽는다."""
    with _common._get_engine().connect() as conn:
        return conn.execute(
            select(service_cases).where(service_cases.c.case_id == case_id)
        ).one()


def test_load_seed_history_stores_200_completed_cases(rdb, tmp_path):
    path = write_csv(tmp_path / "seed.csv", SEED_HEADER, valid_seed_rows())

    assert rdb.load_seed_history_from_csv(path) == 200

    assert len(read_rows(rdb, service_cases)) == 200
    assert len(read_rows(rdb, resolutions)) == 200
    case = read_case(rdb, "SRM-H-0001")
    assert (case.source, case.route, case.status) == ("seed", "support", "completed")
    assert case.guide_failure is None
    assert case.self_guide_answer is None
    assert case.customer_memo == "현장 증상"
    with _common._get_engine().connect() as conn:
        record = conn.execute(
            select(resolutions).where(resolutions.c.case_id == "SRM-H-0001")
        ).one()
    assert (record.cause, record.action, record.result) == ("원인", "조치", "해결")


def test_load_seed_history_converts_times_to_utc(rdb, tmp_path):
    rows = valid_seed_rows()
    rows[0].update(created_at="2026-07-01T18:00:00+09:00", completed_at="2026-07-01T18:30:00+09:00")
    rdb.load_seed_history_from_csv(write_csv(tmp_path / "seed.csv", SEED_HEADER, rows))

    case = read_case(rdb, rows[0]["case_id"])
    # DATETIME: UTC 기준, 시간대 없이 저장
    assert case.created_at == datetime(2026, 7, 1, 9, 0)
    assert case.completed_at == datetime(2026, 7, 1, 9, 30)


def test_load_seed_history_empty_memo_is_null(rdb, tmp_path):
    rows = valid_seed_rows()
    rows[0]["memo"] = ""
    rdb.load_seed_history_from_csv(write_csv(tmp_path / "seed.csv", SEED_HEADER, rows))

    assert read_case(rdb, rows[0]["case_id"]).customer_memo is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"code": "S999"},  # 에러코드 표에 없는 코드
        {"equipment": "AGV"},  # 설비와 코드 쌍이 표에 없음
        {"equipment": "Forklift"},  # 허용 밖 설비
        {"cause": " "},  # 필수값 누락
        {"result": ""},  # 필수값 누락
        {"case_id": "SRM:0001"},  # 문서 ID 구분자
        {"case_id": "S" * 65},  # case_id VARCHAR(64) 초과
        {"created_at": "2026-07-01 09:00"},  # 시간대 없음
        {"completed_at": "어제"},  # 시각 형식 아님
        {"completed_at": "2026-06-30T09:00:00+00:00"},  # 완료가 신고보다 빠름
        {"memo": "가" * 2001},  # 메모 길이 초과
        {"cause": "가" * 5001},  # 조치 기록 길이 초과
    ],
)
def test_load_seed_history_rejects_bad_row_and_stores_nothing(rdb, tmp_path, overrides):
    rows = valid_seed_rows()
    rows[60].update(overrides)  # SRM 행 하나
    path = write_csv(tmp_path / "seed.csv", SEED_HEADER, rows)

    with pytest.raises(ValidationError):
        rdb.load_seed_history_from_csv(path)
    assert read_rows(rdb, service_cases) == []
    assert read_rows(rdb, resolutions) == []


def test_load_seed_history_rejects_duplicate_case_id(rdb, tmp_path):
    rows = valid_seed_rows()
    rows[1]["case_id"] = rows[0]["case_id"]

    with pytest.raises(ValidationError, match="중복"):
        rdb.load_seed_history_from_csv(write_csv(tmp_path / "seed.csv", SEED_HEADER, rows))


@pytest.mark.parametrize("drop", [1, 51])
def test_load_seed_history_rejects_wrong_count(rdb, tmp_path, drop):
    rows = valid_seed_rows()
    del rows[:drop]

    with pytest.raises(ValidationError, match="건수 불일치"):
        rdb.load_seed_history_from_csv(write_csv(tmp_path / "seed.csv", SEED_HEADER, rows))
    assert read_rows(rdb, service_cases) == []


def test_load_seed_history_rejects_too_many(rdb, tmp_path):
    rows = valid_seed_rows() + [seed_row(Equipment.AGV, 51)]

    with pytest.raises(ValidationError, match="'AGV': 51"):
        rdb.load_seed_history_from_csv(write_csv(tmp_path / "seed.csv", SEED_HEADER, rows))


def test_load_seed_history_rejects_wrong_header(rdb, tmp_path):
    header = [c for c in SEED_HEADER if c != "result"]
    rows = [{k: v for k, v in r.items() if k != "result"} for r in valid_seed_rows()]

    with pytest.raises(ValidationError):
        rdb.load_seed_history_from_csv(write_csv(tmp_path / "seed.csv", header, rows))


def test_load_seed_history_missing_file_is_dependency_error(rdb, tmp_path):
    with pytest.raises(DependencyError):
        rdb.load_seed_history_from_csv(tmp_path / "없음.csv")


def test_load_seed_history_requires_connect(tmp_path):
    # DB가 없어도 도는 테스트: connect 전에 부르면 DependencyError.
    from mpilot.db import rdb as rdb_module

    assert _common._engine is None
    with pytest.raises(DependencyError):
        rdb_module.load_seed_history_from_csv(tmp_path / "없음.csv")
