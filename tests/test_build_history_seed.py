"""시드 이력 CSV 생성(scripts.build_history_seed) 테스트.

DB 없이 돈다. 원자료 이력 CSV는 임시 폴더에 직접 만든다.
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mpilot.models import ValidationError
from scripts import build_history_seed as build

RAW_HEADER = ["이력ID", "발생일시", "복구일시", "정지시간(분)", "호기", "모델명", "에러 코드", "에러명", "심각도",
              "조치 구분", "발생 위치", "현장 증상", "확인한 상태값", "원인", "조치 내용", "조치자", "결과",
              "참조 매뉴얼", "비고"]


def raw_row(case_id="SRM-H-0001", created="2026-07-01 08:35", completed="2026-07-01 09:00", **overrides):
    """원자료 이력 한 행."""
    row = {column: f"{column} 값" for column in RAW_HEADER}
    row.update({
        "이력ID": case_id, "발생일시": created, "복구일시": completed, "모델명": "SRM", "에러 코드": "S101",
        "현장 증상": "목적지 셀 앞 정지, HMI에 S101 표시", "원인": "반사판 오염", "조치 내용": "반사판 청소",
        "결과": "해결",
    })
    row.update(overrides)
    return row


def write_raw(path: Path, rows: list[dict], header=RAW_HEADER) -> Path:
    """원자료 이력 CSV를 만든다(원자료처럼 BOM이 붙은 UTF-8)."""
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


# ---------------------------------------------------------------------------
# 시각 변환
# ---------------------------------------------------------------------------


def test_to_iso_kst():
    assert build.to_iso_kst("2026-07-01 08:35") == "2026-07-01T08:35:00+09:00"


def test_to_iso_kst_is_korea_time():
    # 한국 시간 2026-07-01 08:35는 UTC로 9시간 이른 2026-06-30 23:35다.
    value = datetime.fromisoformat(build.to_iso_kst("2026-07-01 08:35"))

    assert value.astimezone(UTC) == datetime(2026, 6, 30, 23, 35, tzinfo=UTC)


@pytest.mark.parametrize(
    "value",
    ["2026-07-01T08:35", "2026/07/01 08:35", "2026-07-01 8:35", "2026-07-01 08:35:00", "", "2026-02-30 08:35"],
)
def test_to_iso_kst_rejects_other_formats(value):
    with pytest.raises(ValidationError):
        build.to_iso_kst(value)


# ---------------------------------------------------------------------------
# 열 변환
# ---------------------------------------------------------------------------


def test_read_raw_history_maps_columns(tmp_path):
    path = write_raw(tmp_path / "srm_error_history_dummy.csv", [raw_row()])

    [row] = build.read_raw_history(path)

    assert row == {
        "case_id": "SRM-H-0001",
        "equipment": "SRM",
        "code": "S101",
        "memo": "목적지 셀 앞 정지, HMI에 S101 표시",  # 쉼표가 든 값도 그대로
        "cause": "반사판 오염",
        "action": "반사판 청소",
        "result": "해결",
        "created_at": "2026-07-01T08:35:00+09:00",
        "completed_at": "2026-07-01T09:00:00+09:00",
    }


def test_read_raw_history_ignores_extra_columns(tmp_path):
    # RTV 원자료처럼 쓰지 않는 열(OBC 버전)이 더 있어도 된다.
    header = RAW_HEADER + ["OBC 버전"]
    path = write_raw(tmp_path / "rtv.csv", [raw_row(**{"OBC 버전": "v2.1"})], header=header)

    [row] = build.read_raw_history(path)

    assert list(row) == build.OUTPUT_COLUMNS


def test_read_raw_history_keeps_values_unchanged(tmp_path):
    # 값은 보정하지 않는다(앞뒤 공백·빈 메모도 그대로 옮기고 검증은 적재 함수가 한다).
    path = write_raw(tmp_path / "srm.csv", [raw_row(**{"원인": "  반사판 오염 ", "현장 증상": ""})])

    [row] = build.read_raw_history(path)

    assert (row["cause"], row["memo"]) == ("  반사판 오염 ", "")


def test_read_raw_history_rejects_missing_column(tmp_path):
    header = [c for c in RAW_HEADER if c != "조치 내용"]
    path = write_raw(tmp_path / "srm.csv", [raw_row()], header=header)

    with pytest.raises(ValidationError, match="조치 내용"):
        build.read_raw_history(path)


def test_read_raw_history_rejects_bad_time(tmp_path):
    path = write_raw(tmp_path / "srm.csv", [raw_row(), raw_row("SRM-H-0002", completed="2026-07-02")])

    with pytest.raises(ValidationError, match="2번째 행 복구일시"):
        build.read_raw_history(path)


# ---------------------------------------------------------------------------
# 합치기·쓰기
# ---------------------------------------------------------------------------


def test_build_rows_sorted_by_case_id(tmp_path):
    srm = write_raw(tmp_path / "srm.csv", [raw_row("SRM-H-0002"), raw_row("SRM-H-0001")])
    agv = write_raw(tmp_path / "agv.csv", [raw_row("AGV-H-0001", **{"모델명": "AGV"})])

    rows = build.build_rows([srm, agv])

    assert [r["case_id"] for r in rows] == ["AGV-H-0001", "SRM-H-0001", "SRM-H-0002"]


def test_build_rows_same_result_regardless_of_file_order(tmp_path):
    srm = write_raw(tmp_path / "srm.csv", [raw_row("SRM-H-0001")])
    agv = write_raw(tmp_path / "agv.csv", [raw_row("AGV-H-0001", **{"모델명": "AGV"})])

    assert build.build_rows([srm, agv]) == build.build_rows([agv, srm])


def test_build_rows_requires_files():
    with pytest.raises(ValidationError):
        build.build_rows([])


def test_write_rows_header_and_encoding(tmp_path):
    rows = build.build_rows([write_raw(tmp_path / "srm.csv", [raw_row()])])
    path = tmp_path / "history_seed.csv"

    build.write_rows(path, rows)

    data = path.read_bytes()
    assert not data.startswith(b"\xef\xbb\xbf")  # BOM 없는 UTF-8
    first_line = data.decode("utf-8").splitlines()[0]
    assert first_line == "case_id,equipment,code,memo,cause,action,result,created_at,completed_at"
    with path.open(encoding="utf-8", newline="") as f:
        assert list(csv.DictReader(f)) == rows


def test_output_columns_match_seed_loader():
    # rdb.load_seed_history_from_csv가 요구하는 열과 같아야 한다(spec §3.4).
    assert build.OUTPUT_COLUMNS == ["case_id", "equipment", "code", "memo", "cause", "action", "result",
                                    "created_at", "completed_at"]


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def test_main_writes_file_and_returns_0(tmp_path, monkeypatch, capsys):
    write_raw(tmp_path / "srm_error_history_dummy.csv", [raw_row()])
    monkeypatch.setattr(build, "RAW_DIR", tmp_path)
    monkeypatch.setattr(build, "OUTPUT_PATH", tmp_path / "history_seed.csv")

    assert build.main() == 0

    assert (tmp_path / "history_seed.csv").exists()
    assert "1건" in capsys.readouterr().out


def test_main_rerun_gives_same_file(tmp_path, monkeypatch):
    write_raw(tmp_path / "srm_error_history_dummy.csv", [raw_row("SRM-H-0002"), raw_row("SRM-H-0001")])
    monkeypatch.setattr(build, "RAW_DIR", tmp_path)
    monkeypatch.setattr(build, "OUTPUT_PATH", tmp_path / "history_seed.csv")

    build.main()
    first = (tmp_path / "history_seed.csv").read_bytes()
    build.main()

    assert (tmp_path / "history_seed.csv").read_bytes() == first


def test_main_returns_1_and_writes_nothing_on_bad_raw(tmp_path, monkeypatch, capsys):
    write_raw(tmp_path / "srm_error_history_dummy.csv", [raw_row(created="07/01 08:35")])
    monkeypatch.setattr(build, "RAW_DIR", tmp_path)
    monkeypatch.setattr(build, "OUTPUT_PATH", tmp_path / "history_seed.csv")

    assert build.main() == 1

    assert not (tmp_path / "history_seed.csv").exists()
    assert "발생일시" in capsys.readouterr().err
