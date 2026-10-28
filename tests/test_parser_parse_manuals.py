"""매뉴얼 파싱 조립 테스트: `parse_manuals`(#116).

조립 순서·같은 설비 중복·ID 중복은 PDF를 읽는 함수만 가짜로 바꿔 확인하고,
재현성과 ID 유일성은 실제 매뉴얼 PDF로 확인한다. 실제 PDF 파싱은 오래 걸려(합본 4종에 3분 가까이) 표 추출은 세션에서
한 번만 하고(conftest), 재현성은 가장 작은 PDF 하나를 처음부터 다시 파싱해 확인한다.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from mpilot.db import parser
from mpilot.db.parser import PageText, RawTable, TextLine, parse_manuals
from mpilot.models import Chunk, Equipment, ValidationError

MANUALS_DIR = Path(__file__).resolve().parents[1] / "data" / "manuals"


def page(manual_id: str, number: int, *lines: tuple[float, str]) -> PageText:
    return PageText(
        manual_id=manual_id,
        version="1.1",
        page=number,
        lines=[TextLine(top=top, text=text) for top, text in lines],
    )


def fake_manual(manual_id: str) -> list[PageText]:
    """목차 한 쪽과 본문 한 쪽짜리 가짜 매뉴얼."""
    return [
        page(manual_id, 2, (60.0, "목차"), (98.0, "9.2 에러 코드 일람 3")),
        page(manual_id, 3, (60.0, "9.2 에러 코드 일람"), (82.0, "에러 코드는 아래 표와 같다.")),
    ]


def fake_table(number: int = 3) -> RawTable:
    return RawTable(
        page=number,
        index=1,
        bbox=(40.0, 120.0, 550.0, 300.0),
        caption="표 9-1. 에러 코드 일람",
        rows=[["코드", "명칭"], ["A301", "주행 이상"]],
        continued=False,
    )


@pytest.fixture
def fake_pdfs(monkeypatch):
    """PDF 대신 파일 이름 → 문서번호를 받아, PDF를 읽는 세 함수를 가짜로 바꾼다."""
    received_tables: dict[str, list[RawTable]] = {}

    def install(manual_ids: dict[str, str]) -> dict[str, list[RawTable]]:
        paths = [Path(name) for name in sorted(manual_ids)]
        monkeypatch.setattr(parser, "_list_pdf_files", lambda directory: paths)
        monkeypatch.setattr(parser, "_extract_tables", lambda path: [fake_table()])

        def extract_page_texts(path: Path, tables: list[RawTable]) -> list[PageText]:
            received_tables[path.name] = tables
            return fake_manual(manual_ids[path.name])

        monkeypatch.setattr(parser, "_extract_page_texts", extract_page_texts)
        return received_tables

    return install


def test_parse_manuals_joins_text_and_table_drafts_in_position_order(fake_pdfs):
    received = fake_pdfs({"agv.pdf": "HMX-AGV-TSM-001", "srm.pdf": "HMX-SRM-TSM-001"})
    chunks = parse_manuals(Path("manuals"))
    assert received["agv.pdf"] == [fake_table()]  # 본문에서 표 영역을 빼도록 표를 넘긴다
    assert [(c.manual_id, c.page, c.chunk_no) for c in chunks] == [
        ("HMX-AGV-TSM-001", 3, 1),
        ("HMX-AGV-TSM-001", 3, 2),
        ("HMX-SRM-TSM-001", 3, 1),
        ("HMX-SRM-TSM-001", 3, 2),
    ]
    text, table = chunks[0], chunks[1]
    assert text.text == "9.2 에러 코드 일람\n\n에러 코드는 아래 표와 같다."
    assert table.text.startswith("표 9-1. 에러 코드 일람\n| 코드 | 명칭 |")
    assert table.section == "9.2 에러 코드 일람"  # 표는 앞 텍스트의 절을 이어받는다
    assert [c.equipment for c in chunks] == [Equipment.AGV, Equipment.AGV, Equipment.SRM, Equipment.SRM]
    assert {c.version for c in chunks} == {"1.1"}


def test_parse_manuals_raises_for_two_manuals_of_same_equipment(fake_pdfs):
    fake_pdfs({"agv_rev1_0.pdf": "HMX-AGV-TSM-001", "agv_rev1_1.pdf": "HMX-AGV-TSM-002"})
    with pytest.raises(ValidationError, match="AGV"):
        parse_manuals(Path("manuals"))


def test_parse_manuals_raises_for_duplicate_chunk_ids(fake_pdfs, monkeypatch):
    fake_pdfs({"agv.pdf": "HMX-AGV-TSM-001"})
    chunk = Chunk(
        manual_id="HMX-AGV-TSM-001", equipment=Equipment.AGV, page=3, section="9.2", chunk_no=1, version="1.1", text="x"
    )
    monkeypatch.setattr(parser, "_number_chunks", lambda *args: [chunk, chunk])
    with pytest.raises(ValidationError, match="manual:HMX-AGV-TSM-001:3:1"):
        parse_manuals(Path("manuals"))


def test_parse_manuals_raises_when_folder_has_no_pdf(tmp_path):
    with pytest.raises(ValidationError):
        parse_manuals(tmp_path)


# ---------------------------------------------------------------------------
# 실제 매뉴얼 PDF 4종
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real_chunks(disk_parse) -> list[Chunk]:
    # PDF를 읽는 두 함수(표, 표를 뺀 본문)만 캐시판으로 바꾼다(4종에 3분 가까이 걸린다). 나머지는 실제 `parse_manuals`가 그대로 한다.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(parser, "_extract_tables", lambda path: disk_parse.tables(Path(path)))
        patch.setattr(parser, "_extract_page_texts", lambda path, tables: disk_parse.page_texts(Path(path), tables))
        return parse_manuals(MANUALS_DIR)


def test_real_manuals_cover_four_equipment_with_unique_ids(real_chunks):
    assert {c.equipment for c in real_chunks} == set(Equipment)
    ids = [f"manual:{c.manual_id}:{c.page}:{c.chunk_no}" for c in real_chunks]
    assert len(ids) == len(set(ids))
    assert all(c.section.strip() and c.text.strip() for c in real_chunks)


def test_real_manuals_are_reproducible(real_chunks, tmp_path):
    # 가장 작은 매뉴얼 하나를 캐시 없이 처음부터 다시 파싱해 같은 청크가 나오는지 본다(4종을 다시 읽으면 3분 가까이 걸린다).
    shutil.copy(MANUALS_DIR / "mini_loader_spec_sheet_dummy_rev1_1.pdf", tmp_path)

    assert parse_manuals(tmp_path) == [c for c in real_chunks if c.equipment is Equipment.MINI_LOADER]
