"""에러코드 CSV 적재·설비별 조회(load_error_codes_from_csv, fetch_error_codes) 테스트.

DB가 필요한 테스트는 실제 MySQL 테스트 DB를 쓴다(`rdb_db` fixture, tests/conftest.py).
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from mpilot.db.rdb import _common
from mpilot.db.schemas import error_codes
from mpilot.models import DependencyError, Equipment, ValidationError

CODE_HEADER = error_codes.c.keys()
# 설비마다 코드 하나씩.
CODE_BY_EQUIPMENT = {
    Equipment.MINI_LOADER: "M401",
    Equipment.SRM: "S101",
    Equipment.AGV: "A361",
    Equipment.RTV: "R201",
}


@pytest.fixture
def rdb(rdb_db):
    """빈 테이블 4개가 있는 테스트 DB에 연결된 `rdb` 모듈."""
    return rdb_db


def write_csv(path: Path, header: list[str], rows: list[dict]) -> Path:
    """머리글과 행으로 CSV 파일을 만든다."""
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)
    return path


def code_row(eq: Equipment, error_code: str, **overrides) -> dict:
    """에러코드 CSV 한 행. 바꿀 칸만 `overrides`로 준다."""
    row = {
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
        "customer_procedure_ref_ids": json.dumps([f"manual:{eq.name.lower()}_manual:12:1"]),
        "required_safety_rules": json.dumps(["전원을 차단한다"], ensure_ascii=False),
    }
    row.update(overrides)
    return row


def valid_code_rows() -> list[dict]:
    return [code_row(eq, code) for eq, code in CODE_BY_EQUIPMENT.items()]


def count_rows(rdb) -> int:
    """`error_codes` 표의 행 수."""
    with _common._get_engine().connect() as conn:
        return len(conn.execute(select(error_codes)).all())


# ---------------------------------------------------------------------------
# load_error_codes_from_csv
# ---------------------------------------------------------------------------


def test_load_error_codes_stores_rows_and_fetch_returns_them(rdb, tmp_path):
    path = write_csv(tmp_path / "errorcodes.csv", CODE_HEADER, valid_code_rows())

    loaded = rdb.load_error_codes_from_csv(path)

    assert len(loaded) == 4
    fetched = rdb.fetch_error_codes(Equipment.SRM)
    assert [c.code for c in fetched] == ["S101"]
    assert fetched[0].page == 12
    assert fetched[0].customer_procedure_ref_ids == ["manual:srm_manual:12:1"]
    assert fetched[0].required_safety_rules == ["전원을 차단한다"]


def test_load_error_codes_accepts_empty_lists_and_bom(rdb, tmp_path):
    row = code_row(Equipment.AGV, "A001", customer_procedure_ref_ids="[]", required_safety_rules="[]")
    path = tmp_path / "errorcodes.csv"
    write_csv(path, CODE_HEADER, [row])
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())  # 엑셀에서 저장한 CSV처럼 BOM을 붙인다

    loaded = rdb.load_error_codes_from_csv(path)

    assert loaded[0].customer_procedure_ref_ids == []
    assert loaded[0].required_safety_rules == []


@pytest.mark.parametrize(
    "bad_row",
    [
        code_row(Equipment.AGV, "A999", name=""),  # 필수값 누락
        code_row(Equipment.AGV, "A999", section="  "),  # 출처 누락(공백뿐)
        code_row(Equipment.AGV, "A999", equipment="Forklift"),  # 허용 밖 설비
        code_row(Equipment.AGV, "A999", severity="최상"),  # 허용 밖 심각도
        code_row(Equipment.AGV, "A999", page="0"),  # 쪽은 1 이상
        code_row(Equipment.AGV, "A999", page="12쪽"),  # 쪽은 정수
        code_row(Equipment.AGV, "A999", customer_procedure_ref_ids=""),  # 빈 목록은 []로 써야 한다
        code_row(Equipment.AGV, "A999", required_safety_rules="전원 차단"),  # JSON 배열 아님
        code_row(Equipment.AGV, "A999", customer_procedure_ref_ids='"문자열"'),  # 배열 아님
        code_row(Equipment.AGV, "A361"),  # 중복 키
        code_row(Equipment.AGV, "A" * 33),  # code VARCHAR(32) 초과
        code_row(Equipment.AGV, "A999", name="가" * 256),  # name VARCHAR(255) 초과
        code_row(Equipment.AGV, "A999", manual_id="m" * 65),  # manual_id VARCHAR(64) 초과
    ],
)
def test_load_error_codes_rejects_bad_row_and_stores_nothing(rdb, tmp_path, bad_row):
    path = write_csv(tmp_path / "errorcodes.csv", CODE_HEADER, valid_code_rows() + [bad_row])

    with pytest.raises(ValidationError):
        rdb.load_error_codes_from_csv(path)
    assert count_rows(rdb) == 0


def test_load_error_codes_accepts_values_at_max_length(rdb, tmp_path):
    # 딱 최대 길이(VARCHAR(n)의 n자)까지는 받는다. 오류 문구에 칸 이름이 나온다.
    ok = code_row(Equipment.AGV, "A" * 32, name="가" * 255, section="절" * 255)
    rdb.load_error_codes_from_csv(write_csv(tmp_path / "ok.csv", CODE_HEADER, [ok]))
    assert rdb.fetch_error_codes(Equipment.AGV)[0].name == "가" * 255

    long_section = code_row(Equipment.RTV, "R1", section="절" * 256)
    with pytest.raises(ValidationError, match="section"):
        rdb.load_error_codes_from_csv(write_csv(tmp_path / "long.csv", CODE_HEADER, [long_section]))


def test_load_error_codes_does_not_trim_values(rdb, tmp_path):
    # 앞뒤 공백을 고쳐 주지 않고 오류로 본다(자동 보정 없음).
    path = write_csv(
        tmp_path / "errorcodes.csv", CODE_HEADER, [code_row(Equipment.AGV, "A1", equipment=" AGV")]
    )

    with pytest.raises(ValidationError):
        rdb.load_error_codes_from_csv(path)


def test_load_error_codes_keeps_numeric_looking_code(rdb, tmp_path):
    # pandas가 "0012"를 숫자 12로 바꾸지 않아야 한다(자동 보정 없음).
    path = write_csv(tmp_path / "errorcodes.csv", CODE_HEADER, [code_row(Equipment.AGV, "0012")])

    rdb.load_error_codes_from_csv(path)

    assert [c.code for c in rdb.fetch_error_codes(Equipment.AGV)] == ["0012"]


@pytest.mark.parametrize("cut", [slice(0, -1), slice(None)])
def test_load_error_codes_rejects_wrong_field_count(rdb, tmp_path, cut):
    path = write_csv(tmp_path / "errorcodes.csv", CODE_HEADER, valid_code_rows())
    line = ",".join(['"' + v + '"' for v in code_row(Equipment.AGV, "A999").values()][cut])
    extra = "" if cut == slice(0, -1) else ',"남는 칸"'
    with path.open("a", encoding="utf-8") as f:
        f.write(line + extra + "\n")

    with pytest.raises(ValidationError):
        rdb.load_error_codes_from_csv(path)
    assert count_rows(rdb) == 0


def test_load_error_codes_rejects_wrong_header(rdb, tmp_path):
    header = [c for c in CODE_HEADER if c != "section"]
    rows = [{k: v for k, v in r.items() if k != "section"} for r in valid_code_rows()]
    path = write_csv(tmp_path / "errorcodes.csv", header, rows)

    with pytest.raises(ValidationError, match="section"):
        rdb.load_error_codes_from_csv(path)


def test_load_error_codes_rejects_empty_file(rdb, tmp_path):
    path = write_csv(tmp_path / "errorcodes.csv", CODE_HEADER, [])

    with pytest.raises(ValidationError):
        rdb.load_error_codes_from_csv(path)


def test_load_error_codes_missing_file_is_dependency_error(rdb, tmp_path):
    with pytest.raises(DependencyError):
        rdb.load_error_codes_from_csv(tmp_path / "없음.csv")


# ---------------------------------------------------------------------------
# fetch_error_codes
# ---------------------------------------------------------------------------


def test_fetch_error_codes_unknown_equipment(rdb):
    with pytest.raises(ValidationError):
        rdb.fetch_error_codes("Forklift")


def test_fetch_error_codes_empty_when_none(rdb):
    assert rdb.fetch_error_codes(Equipment.RTV) == []


def test_fetch_error_codes_sorted_by_code(rdb, tmp_path):
    rows = [code_row(Equipment.RTV, c) for c in ("R207", "R201", "R203")]
    rdb.load_error_codes_from_csv(write_csv(tmp_path / "c.csv", CODE_HEADER, rows))

    assert [c.code for c in rdb.fetch_error_codes(Equipment.RTV)] == ["R201", "R203", "R207"]


def test_functions_require_connect(tmp_path):
    # DB가 없어도 도는 테스트: connect 전에 부르면 DependencyError.
    from mpilot.db import rdb as rdb_module

    assert _common._engine is None
    with pytest.raises(DependencyError):
        rdb_module.fetch_error_codes(Equipment.AGV)
    with pytest.raises(DependencyError):
        rdb_module.load_error_codes_from_csv(tmp_path / "없음.csv")
