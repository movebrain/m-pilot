"""DB 초기화 스크립트(scripts.init_db.main) 테스트.

DB 없이 돈다. 설정 로드와 `rdb` 함수를 바꿔 끼워 호출 순서와 종료 코드만 확인한다.
적재 함수 자체는 test_rdb_error_codes.py·test_rdb_seed_history.py가 확인한다.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from mpilot.models import DependencyError, ValidationError
from scripts import init_db

DB_URL = "mysql+pymysql://mpilot:s3cret-pw@127.0.0.1:3306/mpilot"


@pytest.fixture
def calls(monkeypatch):
    """설정 로드와 rdb 함수를 가짜로 바꾸고, 불린 순서를 담는 목록을 돌려준다."""
    calls = []
    config = SimpleNamespace(
        db_url=SecretStr(DB_URL),
        codes_path=Path("data/errorcodes.csv"),
        history_seed_path=Path("data/history_seed.csv"),
    )

    def record(name, result=None):
        def fake(*args):
            calls.append((name, *args))
            return result
        return fake

    monkeypatch.setattr(init_db, "load_config", record("load_config", config))
    monkeypatch.setattr(init_db.rdb, "connect", record("connect"))
    monkeypatch.setattr(init_db.rdb, "reset_schema", record("reset_schema"))
    monkeypatch.setattr(init_db.rdb, "load_error_codes_from_csv",
                        record("load_error_codes_from_csv", ["code"] * 249))
    monkeypatch.setattr(init_db.rdb, "load_seed_history_from_csv",
                        record("load_seed_history_from_csv", 200))
    return calls


def test_main_runs_steps_in_order_and_returns_0(calls, capsys):
    assert init_db.main() == 0

    assert calls == [
        ("load_config",),
        ("connect", DB_URL),
        ("reset_schema",),
        ("load_error_codes_from_csv", Path("data/errorcodes.csv")),
        ("load_seed_history_from_csv", Path("data/history_seed.csv")),
    ]
    out = capsys.readouterr().out
    assert "249" in out and "200" in out  # 적재 건수를 출력한다


@pytest.mark.parametrize(
    ("step", "error"),
    [
        ("load_config", ValidationError("필수 설정 MYSQL_USER이(가) 없습니다.")),
        ("connect", DependencyError("DB에 연결할 수 없습니다.")),
        ("load_error_codes_from_csv", ValidationError("3번째 행: 필수값이 비었습니다.")),
        ("load_seed_history_from_csv", ValidationError("건수 불일치")),
    ],
)
def test_main_returns_1_on_app_error(calls, capsys, monkeypatch, step, error):
    def fail(*args):
        raise error

    target = init_db if step == "load_config" else init_db.rdb
    monkeypatch.setattr(target, step, fail)

    assert init_db.main() == 1  # 예외를 밖으로 던지지 않는다

    err = capsys.readouterr().err
    assert str(error) in err


def test_main_stops_after_failure(calls, monkeypatch):
    def fail(*args):
        raise ValidationError("에러코드 CSV 오류")

    monkeypatch.setattr(init_db.rdb, "load_error_codes_from_csv", fail)

    init_db.main()

    # 에러코드 적재가 실패하면 시드 적재로 넘어가지 않는다.
    assert "load_seed_history_from_csv" not in [c[0] for c in calls]


def test_main_does_not_print_connection_info(calls, capsys):
    init_db.main()

    captured = capsys.readouterr()
    for secret in ["s3cret-pw", "mysql+pymysql", "127.0.0.1"]:
        assert secret not in captured.out + captured.err
