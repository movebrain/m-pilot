"""파싱 디스크 캐시(`tests/conftest.py`의 `_DiskParse`) 테스트.

PDF를 실제로 읽지 않도록 읽는 함수를 가짜로 바꾸고, 캐시가 언제 쓰이고 언제 버려지는지만 본다.
"""

from __future__ import annotations

from pathlib import Path

import conftest
import pytest

from mpilot.db.parser import RawTable


def table(caption: str) -> RawTable:
    return RawTable(page=3, index=1, bbox=(0.0, 0.0, 1.0, 1.0), caption=caption, rows=[["a"]], continued=False)


@pytest.fixture
def disk(tmp_path, monkeypatch):
    """캐시 폴더를 tmp_path로 돌리고, 표 추출 호출 횟수를 세는 `_DiskParse`와 가짜 PDF 경로를 돌려준다."""
    monkeypatch.setattr(conftest, "PARSE_CACHE_DIR", tmp_path / "cache")
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"pdf-v1")
    disk = conftest._DiskParse()
    calls: list[Path] = []

    def fake_extract_tables(path: Path) -> list[RawTable]:
        calls.append(path)
        return [table("표 1. 가짜")]

    disk._extract_tables = fake_extract_tables
    return disk, pdf, calls


def test_second_read_comes_from_cache(disk):
    parse, pdf, calls = disk
    assert parse.tables(pdf) == [table("표 1. 가짜")]
    assert parse.tables(pdf) == [table("표 1. 가짜")]
    assert len(calls) == 1


def test_changed_pdf_is_read_again_and_old_cache_is_removed(disk):
    parse, pdf, calls = disk
    parse.tables(pdf)
    pdf.write_bytes(b"pdf-v2")
    parse.tables(pdf)
    assert len(calls) == 2
    assert len(list(conftest.PARSE_CACHE_DIR.glob("tables-a-*.json"))) == 1


def test_page_texts_for_different_tables_keep_their_own_cache(disk):
    parse, pdf, _ = disk
    runs: list[list] = []
    parse._extract_page_texts = lambda path, tables: runs.append(tables) or []
    for _ in range(2):  # 표 목록이 다른 두 호출이 서로의 캐시를 지우면 두 번째 바퀴에서 다시 읽게 된다
        parse.page_texts(pdf, [])
        parse.page_texts(pdf, [table("표 1. 가짜")])
    assert len(runs) == 2


def test_broken_cache_file_is_read_again(disk):
    parse, pdf, calls = disk
    parse.tables(pdf)
    (cache_file,) = conftest.PARSE_CACHE_DIR.glob("tables-a-*.json")
    cache_file.write_text('[{"page": "not a number"}]')
    assert parse.tables(pdf) == [table("표 1. 가짜")]
    assert len(calls) == 2


def test_page_texts_cache_depends_on_tables(disk):
    parse, pdf, _ = disk
    runs: list[list] = []

    def fake_extract_page_texts(path: Path, tables: list) -> list:
        runs.append(tables)
        return []

    parse._extract_page_texts = fake_extract_page_texts
    parse.page_texts(pdf, [])
    parse.page_texts(pdf, [])
    parse.page_texts(pdf, [table("표 1. 가짜")])  # 빼는 표가 달라졌으니 다시 읽는다
    assert len(runs) == 2
