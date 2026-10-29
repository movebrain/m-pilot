"""에러코드 표 정본 생성(scripts.build_error_codes) 테스트.

DB와 PDF 없이 돈다. 매뉴얼 청크는 parser가 만드는 모양(캡션 + 마크다운 표)으로 직접 만든다.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from mpilot.models import Chunk, Equipment, ValidationError
from scripts import build_error_codes as build

HEADER = "| 코드 | 명칭 | 대상 모델 | 심각도 | 조치 구분 | 발생 조건 · 원인 | 조치 |"
SEPARATOR = "| --- | --- | --- | --- | --- | --- | --- |"


def table_chunk(rows, page=22, chunk_no=6, section="9.2 에러 코드 일람", equipment=Equipment.SRM,
                manual_id="HMX-SRM-TSM-001"):
    """9.2절 에러 코드 표 청크 하나를 만든다."""
    text = "\n".join(["표 9-4. 에러 코드 일람", HEADER, SEPARATOR, *rows])
    return Chunk(manual_id=manual_id, equipment=equipment, page=page, section=section,
                 chunk_no=chunk_no, version="1.2", text=text)


S101_ROW = ("| S101 | X축 위치 편차 초과 | 전 모델 | 중 | 고객사 조치 | 센서 오염 | "
            "작업자: CMD-09 RESET 1회 재시도. 정비자: 센서 청소 |")
S102_ROW = "| S102 | 과속도 트립 | 전 모델 | 상 | 엔지니어 필요 | 드라이브 파라미터 오류 | 정비자 호출 |"


def raw(code="S101", name="X축 위치 편차 초과", severity="중", action_owner="고객사 조치", equipment="SRM"):
    """원자료 에러코드 표 한 행."""
    return {"equipment": equipment, "code": code, "name": name, "severity": severity,
            "action_owner": action_owner}


# ---------------------------------------------------------------------------
# read_manual_code_rows
# ---------------------------------------------------------------------------


def test_read_manual_code_rows_takes_cells_and_source():
    rows = build.read_manual_code_rows([table_chunk([S101_ROW, S102_ROW])])

    s101 = rows[("SRM", "S101")]
    assert s101["cause"] == "센서 오염"
    assert s101["action"] == "작업자: CMD-09 RESET 1회 재시도. 정비자: 센서 청소"
    assert (s101["manual_id"], s101["page"], s101["section"]) == ("HMX-SRM-TSM-001", 22, "9.2 에러 코드 일람")
    assert rows[("SRM", "S102")]["action_owner"] == "엔지니어 필요"


def test_read_manual_code_rows_ignores_other_sections():
    # 11.8 에스컬레이션 표처럼 코드가 언급된 다른 절의 표는 일람이 아니다.
    other = table_chunk([S101_ROW], section="11.8 에스컬레이션 기준")

    assert build.read_manual_code_rows([other]) == {}


def test_read_manual_code_rows_unescapes_pipe():
    row = "| S101 | 이름 | 전 모델 | 중 | 고객사 조치 | A \\| B 접점 | 조치 |"

    rows = build.read_manual_code_rows([table_chunk([row])])

    assert rows[("SRM", "S101")]["cause"] == "A | B 접점"


def test_read_manual_code_rows_rejects_wrong_width():
    with pytest.raises(ValidationError):
        build.read_manual_code_rows([table_chunk(["| S101 | 이름 | 중 |"])])


def test_read_manual_code_rows_rejects_duplicate_code():
    chunks = [table_chunk([S101_ROW], chunk_no=6), table_chunk([S101_ROW], chunk_no=7)]

    with pytest.raises(ValidationError):
        build.read_manual_code_rows(chunks)


def test_read_manual_code_rows_same_code_other_equipment():
    # (설비, 코드)가 키라 설비가 다르면 따로 모은다.
    agv = table_chunk([S101_ROW], equipment=Equipment.AGV, manual_id="HMX-AGV-TSM-001")

    rows = build.read_manual_code_rows([table_chunk([S101_ROW]), agv])

    assert set(rows) == {("SRM", "S101"), ("AGV", "S101")}


# ---------------------------------------------------------------------------
# build_rows
# ---------------------------------------------------------------------------


def manual_rows():
    return build.read_manual_code_rows([table_chunk([S101_ROW, S102_ROW])])


def test_build_rows_follows_raw_order():
    raws = [raw(), raw("S102", "과속도 트립", "상", "엔지니어 필요")]

    codes, warnings = build.build_rows(raws, manual_rows())

    s101, s102 = codes
    assert (s101.code, s102.code) == ("S101", "S102")
    assert warnings == []


def test_build_rows_fills_manual_columns():
    [code], _ = build.build_rows([raw()], manual_rows())

    assert code.cause == "센서 오염"
    assert code.manual_id == "HMX-SRM-TSM-001"
    assert code.page == 22
    assert code.section == "9.2 에러 코드 일람"


def test_build_rows_keeps_raw_values_and_warns_on_mismatch():
    # 원자료 심각도·조치 구분은 사람이 정한 값이라 매뉴얼과 달라도 고치지 않는다.
    [code], warnings = build.build_rows([raw(severity="하", action_owner="엔지니어 필요")], manual_rows())

    assert (code.severity, code.action_owner) == ("하", "엔지니어 필요")
    mismatch = [w for w in warnings if w.startswith("SRM/S101")]
    assert len(mismatch) == 2  # severity, action_owner


def test_build_rows_name_ignores_pdf_spacing():
    # PDF 추출이 "과전 류"처럼 단어 중간에 공백을 넣어도 같은 이름으로 본다.
    row = "| S101 | X축 위치 편 차 초과 | 전 모델 | 중 | 고객사 조치 | 원인 | 조치 |"

    _, warnings = build.build_rows([raw()], build.read_manual_code_rows([table_chunk([row])]))

    assert warnings == []


def test_build_rows_rejects_code_missing_in_manual():
    with pytest.raises(ValidationError, match="S199"):
        build.build_rows([raw(), raw("S199")], manual_rows())


def test_build_rows_warns_manual_only_code():
    _, warnings = build.build_rows([raw()], manual_rows())

    assert any("S102" in w and "원자료에 없어" in w for w in warnings)


# ---------------------------------------------------------------------------
# 파일 읽기·쓰기
# ---------------------------------------------------------------------------


def write_raw(path: Path, rows: list[dict], header=build.RAW_COLUMNS) -> Path:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_read_raw_codes(tmp_path):
    path = write_raw(tmp_path / "raw.csv", [raw(), raw("S102")])

    assert [r["code"] for r in build.read_raw_codes(path)] == ["S101", "S102"]


@pytest.mark.parametrize(
    "rows",
    [[raw(), raw()], [raw(name="")]],
    ids=["duplicate", "blank"],
)
def test_read_raw_codes_rejects_bad_rows(tmp_path, rows):
    with pytest.raises(ValidationError):
        build.read_raw_codes(write_raw(tmp_path / "raw.csv", rows))


def test_read_raw_codes_rejects_wrong_header(tmp_path):
    path = write_raw(tmp_path / "raw.csv", [{"equipment": "SRM", "code": "S101"}], header=["equipment", "code"])

    with pytest.raises(ValidationError):
        build.read_raw_codes(path)


def test_write_codes_matches_loader_format(tmp_path):
    codes, _ = build.build_rows([raw()], manual_rows())
    path = tmp_path / "errorcodes.csv"

    build.write_codes(path, codes, {})

    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == build.OUTPUT_COLUMNS  # DB 컬럼 10열 + CSV 전용 1열
        [row] = list(reader)
    assert row["required_safety_rules"] == "[]"
    assert row["page"] == "22"


def test_output_columns_are_error_codes_table_plus_csv_only_column():
    from mpilot.db.schemas import error_codes

    # 사람 검수 참고용 열은 CSV에만 있고 DB 컬럼에는 없다.
    assert build.OUTPUT_COLUMNS == list(error_codes.c.keys()) + ["required_safety_rules"]


def test_read_existing_safety_rules_round_trip(tmp_path):
    rules = {("SRM", "S101"): ["전원을 차단한다"]}
    codes, _ = build.build_rows([raw()], manual_rows())
    path = tmp_path / "errorcodes.csv"
    build.write_codes(path, codes, rules)

    assert build.read_existing_safety_rules(path) == rules


def test_read_existing_safety_rules_missing_file(tmp_path):
    assert build.read_existing_safety_rules(tmp_path / "none.csv") == {}


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def test_main_writes_file_and_returns_0(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(build, "RAW_CODES_PATH", write_raw(tmp_path / "raw.csv", [raw()]))
    monkeypatch.setattr(build, "OUTPUT_PATH", tmp_path / "errorcodes.csv")
    monkeypatch.setattr(build, "parse_manuals", lambda _dir: [table_chunk([S101_ROW])])

    assert build.main() == 0

    assert (tmp_path / "errorcodes.csv").exists()
    assert "1건" in capsys.readouterr().out


def test_main_returns_1_and_writes_nothing_on_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(build, "RAW_CODES_PATH", write_raw(tmp_path / "raw.csv", [raw("S199")]))
    monkeypatch.setattr(build, "OUTPUT_PATH", tmp_path / "errorcodes.csv")
    monkeypatch.setattr(build, "parse_manuals", lambda _dir: [table_chunk([S101_ROW])])

    assert build.main() == 1

    assert not (tmp_path / "errorcodes.csv").exists()
    assert "S199" in capsys.readouterr().err
