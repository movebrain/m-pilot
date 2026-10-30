"""매뉴얼 청크 디스크 캐시 테스트: `parse_manuals`의 캐시 경로(#161).

캐시 동작은 실제 PDF 파싱(`_parse_pdfs`)을 횟수를 세는 가짜로 바꾸고, 임시 폴더의 가짜 PDF 파일(내용만 지문에 쓰인다)로 확인한다.
캐시에서 불러온 결과가 새로 파싱한 결과와 같은지는 실제 매뉴얼 PDF 4종의 청크로 확인한다(`pdf` 마커).
conftest가 테스트 동안 캐시를 꺼 두므로 여기서는 테스트마다 다시 켠다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mpilot.db import parser
from mpilot.db.parser import cache, parse_manuals
from mpilot.models import Chunk, Equipment

MANUALS_DIR = Path(__file__).resolve().parents[1] / "data" / "manuals"

CHUNKS = [
    Chunk(
        manual_id="HMX-AGV-TSM-001",
        equipment=Equipment.AGV,
        page=3,
        section="9.2 에러 코드 일람",
        chunk_no=1,
        version="1.1",
        text="표 9-1. 에러 코드 일람\n| 코드 | 명칭 |\n| --- | --- |\n| A302 | 구동 모터 과열 |",
    ),
    Chunk(
        manual_id="HMX-SRM-TSM-001",
        equipment=Equipment.SRM,
        page=12,
        section="7.1 일상 점검",
        chunk_no=2,
        version="1.2",
        text="전원을 차단한 뒤 점검한다.",
    ),
]


@pytest.fixture
def manuals(tmp_path, monkeypatch):
    """임시 매뉴얼 폴더(가짜 PDF 2개)를 만들고 캐시를 켠 뒤, 파싱 횟수를 세는 가짜 `_parse_pdfs`를 끼운다."""
    directory = tmp_path / "manuals"
    directory.mkdir()
    (directory / "agv.pdf").write_bytes(b"%PDF agv")
    (directory / "srm.pdf").write_bytes(b"%PDF srm")
    calls: list[list[str]] = []

    def parse_pdfs(pdf_paths: list[Path]) -> list[Chunk]:
        calls.append([path.name for path in pdf_paths])
        return list(CHUNKS)

    monkeypatch.setattr(parser, "_CACHE_ENABLED", True)
    monkeypatch.setattr(parser, "_parse_pdfs", parse_pdfs)
    return directory, calls


def cache_files(directory: Path) -> list[Path]:
    return sorted((directory.parent / "cache").glob("manual_chunks-*.json"))


def test_second_run_loads_cache_without_parsing(manuals):
    directory, calls = manuals

    first = parse_manuals(directory)
    second = parse_manuals(directory)

    assert calls == [["agv.pdf", "srm.pdf"]]  # 두 번째는 파싱하지 않는다
    assert first == second == CHUNKS
    assert len(cache_files(directory)) == 1  # 매뉴얼 폴더 옆 cache/에 저장한다


def test_changed_pdf_is_parsed_again_and_old_cache_removed(manuals):
    directory, calls = manuals
    parse_manuals(directory)
    old = cache_files(directory)

    (directory / "srm.pdf").write_bytes(b"%PDF srm rev2")  # PDF 내용이 바뀌었다
    parse_manuals(directory)

    assert len(calls) == 2
    assert len(cache_files(directory)) == 1 and cache_files(directory) != old  # 옛 캐시는 지운다


def test_added_pdf_is_parsed_again(manuals):
    directory, calls = manuals
    parse_manuals(directory)

    (directory / "rtv.pdf").write_bytes(b"%PDF rtv")
    parse_manuals(directory)

    assert calls[-1] == ["agv.pdf", "rtv.pdf", "srm.pdf"]


def test_changed_parser_source_is_parsed_again(manuals, tmp_path, monkeypatch):
    directory, calls = manuals
    package = tmp_path / "parser_src"  # 지문이 읽는 parser 패키지 소스를 임시 사본으로 바꿔 끼운다
    package.mkdir()
    (package / "text.py").write_text("RULE = 1\n", encoding="utf-8")
    monkeypatch.setattr(cache, "__file__", str(package / "cache.py"))
    parse_manuals(directory)

    (package / "text.py").write_text("RULE = 2\n", encoding="utf-8")  # 청킹 규칙이 바뀌었다
    parse_manuals(directory)

    assert len(calls) == 2


def test_changed_pdfplumber_version_is_parsed_again(manuals, monkeypatch):
    directory, calls = manuals
    parse_manuals(directory)

    monkeypatch.setattr(cache.pdfplumber, "__version__", "0.0.0-test")
    parse_manuals(directory)

    assert len(calls) == 2


@pytest.mark.parametrize(
    "content",
    [
        b"{not json",  # 쓰다 끊긴 파일
        b'[{"manual_id": "HMX-AGV-TSM-001"}]',  # Chunk 필드가 바뀌어 형식이 맞지 않는 옛 캐시
    ],
)
def test_broken_cache_is_parsed_again_and_rewritten(manuals, content):
    directory, calls = manuals
    parse_manuals(directory)
    [file] = cache_files(directory)
    file.write_bytes(content)

    assert parse_manuals(directory) == CHUNKS  # 오류 없이 다시 파싱한다
    assert len(calls) == 2
    assert cache._load_chunks(file) == CHUNKS  # 새로 저장했다


def test_cache_save_failure_does_not_block_parsing(manuals):
    directory, calls = manuals
    (directory.parent / "cache").write_text("폴더 자리에 파일이 있어 캐시를 저장할 수 없다", encoding="utf-8")

    assert parse_manuals(directory) == CHUNKS
    assert parse_manuals(directory) == CHUNKS
    assert len(calls) == 2  # 저장하지 못했으니 매번 파싱한다


def test_cache_disabled_does_not_touch_disk(manuals, monkeypatch):
    directory, calls = manuals
    monkeypatch.setattr(parser, "_CACHE_ENABLED", False)

    parse_manuals(directory)
    parse_manuals(directory)

    assert len(calls) == 2
    assert not (directory.parent / "cache").exists()


# ---------------------------------------------------------------------------
# 실제 매뉴얼 PDF 4종
# ---------------------------------------------------------------------------


def test_real_chunks_round_trip_through_cache(disk_parse, monkeypatch, tmp_path):
    # PDF를 읽는 두 함수만 테스트용 디스크 캐시판으로 바꿔(4종에 3분 가까이 걸린다) 실제 청크를 만든다.
    monkeypatch.setattr(parser, "_extract_tables", lambda path: disk_parse.tables(Path(path)))
    monkeypatch.setattr(parser, "_extract_page_texts", lambda path, tables: disk_parse.page_texts(Path(path), tables))
    chunks = parser._parse_pdfs(parser._list_pdf_files(MANUALS_DIR))
    file = tmp_path / "cache" / "manual_chunks-test.json"

    cache._save_chunks(file, chunks)
    loaded = cache._load_chunks(file)

    assert loaded == chunks  # 청크 수·순서·ID 구성 요소·본문이 모두 같다
    assert [f"manual:{c.manual_id}:{c.page}:{c.chunk_no}" for c in loaded] == [
        f"manual:{c.manual_id}:{c.page}:{c.chunk_no}" for c in chunks
    ]
