"""parser 표 추출 테스트: _extract_tables, _validate_table, _table_to_drafts.

저장소의 실제 매뉴얼 PDF로 확인한다. 이미지 페이지·OCR 테스트(_find_image_pages, _ocr_page, _ocr_to_drafts,
set_vision_model)는 parser의 OCR 코드와 함께 주석으로 보류해 두었다(파일 아래쪽).
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import pytest
# from PIL import Image, ImageDraw  # OCR 테스트 보류

from mpilot.db import parser
from mpilot.db.parser import ChunkDraft, RawTable
from mpilot.models import DependencyError  # OCR 테스트를 되살리면 ValidationError도 import한다

ROOT = Path(__file__).resolve().parents[1]
MANUALS = ROOT / "data" / "manuals"
CODES_CSV = ROOT / "data" / "raw" / "error_code_table.csv"
MANUAL_EQUIPMENT = {
    "srm_spec_sheet_dummy_rev1_2.pdf": "SRM",
    "rtv_spec_sheet_dummy_rev2_1.pdf": "RTV",
    "agv_spec_sheet_dummy_rev1_1.pdf": "AGV",
    "mini_loader_spec_sheet_dummy_rev1_1.pdf": "Mini Loader",
}
SRM_PDF = MANUALS / "srm_spec_sheet_dummy_rev1_2.pdf"


@pytest.fixture(scope="module")
def manual_tables(real_tables):
    """매뉴얼 4종의 표. 세션에서 한 번만 추출해(conftest) 다른 테스트와 나눠 쓴다(PDF 4종 추출에 1분 넘게 걸린다)."""
    return {name: real_tables(name) for name in MANUAL_EQUIPMENT}


def error_codes_in(tables: list[RawTable]) -> list[str]:
    """에러 코드 일람 표의 첫 열에서 코드를 모은다."""
    codes = []
    for t in tables:
        if t.caption and "에러 코드 일람" in t.caption and t.rows and t.rows[0][0] == "코드":
            codes += [r[0] for r in t.rows[1:] if r[0] and re.fullmatch(r"[A-Z]\d{3}", r[0])]
    return codes


def table(rows, caption="표 9-4. 에러 코드 일람", continued=False) -> RawTable:
    return RawTable(page=3, index=1, bbox=(40.0, 120.0, 550.0, 700.0), caption=caption, rows=rows, continued=continued)


# --- OCR 테스트 준비물(보류: parser의 OCR 코드와 함께 주석 처리) ---
# def image_pdf(path: Path, pages: int = 1) -> Path:
#     """표지 한 쪽 뒤에 글자 없이 그림만 있는 쪽이 `pages`개 있는 PDF를 만든다(스캔본처럼 텍스트를 읽을 수 없는 쪽).
#
#     매뉴얼처럼 PDF 첫 쪽은 표지라 청킹 대상이 아니다. 그래서 인쇄 쪽 번호 1은 PDF 둘째 쪽이다.
#     """
#     images = []
#     for i in range(pages + 1):
#         img = Image.new("RGB", (595, 842), "white")
#         ImageDraw.Draw(img).rectangle((60, 80 + i * 10, 500, 300), outline="black", width=4)
#         images.append(img)
#     images[0].save(path, "PDF", save_all=True, append_images=images[1:])
#     return path
#
#
# class FakeVisionModel:
#     """받은 메시지를 기록하고 정해진 답을 돌려주는 가짜 비전 모델."""
#
#     def __init__(self, reply="| 항목 | 값 |\n| --- | --- |\n| 정격 하중 | 1,000 kg |", error=None, replies=None):
#         self.replies = list(replies) if replies is not None else [reply]
#         self.error = error
#         self.calls = []
#
#     def invoke(self, messages):
#         self.calls.append(messages)
#         if self.error:
#             raise self.error
#         reply = self.replies[min(len(self.calls), len(self.replies)) - 1]
#
#         class Response:
#             content = reply
#
#         return Response()
#
#
# @pytest.fixture
# def vision(monkeypatch):
#     """테스트마다 비전 모델 설정을 비운 상태로 시작하고 끝나면 되돌린다."""
#     monkeypatch.setattr(parser, "_vision_model", None)
#     return parser


# --- _extract_tables ------------------------------------------------------


def test_extract_tables_finds_all_error_codes_matching_csv(manual_tables):
    with CODES_CSV.open(encoding="utf-8-sig") as f:
        expected = {(row["equipment"], row["code"]) for row in csv.DictReader(f)}
    found = []
    for name, tables in manual_tables.items():
        codes = error_codes_in(tables)
        assert len(codes) == len(set(codes)), f"{name}: 같은 코드가 두 번 추출됨"
        found += [(MANUAL_EQUIPMENT[name], c) for c in codes]
    assert len(found) == 249
    assert set(found) == expected


def test_extract_tables_srm_codes_are_s101_to_s150(manual_tables):
    codes = error_codes_in(manual_tables[SRM_PDF.name])
    assert codes == [f"S{n}" for n in range(101, 151)]


def test_extract_tables_attaches_caption_and_bbox(manual_tables):
    tables = manual_tables[SRM_PDF.name]
    first_error_table = next(t for t in tables if t.caption == "표 9-4. 에러 코드 일람")
    assert first_error_table.continued is False
    x0, top, x1, bottom = first_error_table.bbox
    assert 0 <= x0 < x1 <= 600 and 0 <= top < bottom <= 842
    assert first_error_table.rows[0] == ["코드", "명칭", "대상 모델", "심각도", "조치 구분", "발생 조건 · 원인", "조치"]


def test_extract_tables_continued_table_inherits_caption(manual_tables):
    # SRM 꼬리글 24쪽의 에러 코드 일람이 25쪽 맨 위로 이어진다. 25쪽 표에는 캡션이 없고 머리글이 반복된다.
    tables = manual_tables[SRM_PDF.name]
    p25 = next(t for t in tables if t.page == 25 and t.index == 1)
    assert p25.continued is True
    assert p25.caption == "표 9-4. 에러 코드 일람"


def test_extract_tables_page_is_footer_number_and_skips_cover(manual_tables):
    # 쪽 번호는 우측 하단 꼬리글의 인쇄 번호다(PDF 25쪽 꼬리글이 `24 / 111`). 표지(PDF 1쪽)의 표는 빠진다.
    # 합본은 목차가 4쪽으로 늘어 원본(22쪽)보다 2쪽 뒤에 있다.
    tables = manual_tables[SRM_PDF.name]
    first_error_table = next(t for t in tables if t.caption == "표 9-4. 에러 코드 일람")
    assert first_error_table.page == 24
    assert min(t.page for t in tables) == 1
    assert all(t.rows[0][0] != "문서 번호" for t in tables)  # 표지의 문서 정보 표


@pytest.mark.parametrize(
    ("last_line", "pdf_page", "expected"),
    [
        ("HMX-SRM-TSM-001 Rev. 1.2 · SRM 기술 사양서 교육용 가상 문서 22 / 35", 23, 22),
        # 꼬리글 해석은 `_parse_footer`에 맡긴다. 문서번호·Rev가 없는 줄은 꼬리글로 보지 않고 PDF 순서로 센다.
        ("교육용 가상 문서 1 / 35  ", 2, 1),
        ("꼬리글이 없는 마지막 줄", 7, 6),  # 읽을 수 없으면 표지를 뺀 PDF 순서
        # PDF 순서와 다른 번호가 인쇄돼 있으면 꼬리글의 번호를 쓴다(PDF 순서로 추정하지 않음을 확인).
        ("HMX-SRM-TSM-001 Rev. 1.2 · SRM 기술 사양서 교육용 가상 문서 22 / 35", 5, 22),
    ],
)
def test_printed_page_number_reads_footer_or_falls_back(last_line, pdf_page, expected):
    lines = [{"text": "본문", "bottom": 100.0}, {"text": last_line, "bottom": 818.0}]
    assert parser._printed_page_number(lines, pdf_page) == expected


def test_printed_page_number_without_any_text_falls_back():
    assert parser._printed_page_number([], 5) == 4


def test_extract_tables_reads_captions_with_hyphen(manual_tables):
    captions = {t.caption for t in manual_tables[SRM_PDF.name]}
    assert "표 B-1. 계산 조건 (SRM)" in captions  # 합본 PDF는 텍스트 레이어에 일반 하이픈(U+002D)을 쓴다


def test_caption_pattern_accepts_non_breaking_hyphen():
    # 원본 PDF는 'B‑1'의 하이픈이 U+2011이었다. 합본에는 없지만 그런 캡션도 읽어야 하므로 패턴으로 지킨다.
    assert parser._CAPTION_PATTERN.match("표 B‑1. 계산 조건 (SRM)")


def test_extract_tables_orders_by_page_then_position(manual_tables):
    for tables in manual_tables.values():
        keys = [(t.page, t.index) for t in tables]
        assert keys == sorted(keys)
        for a, b in zip(tables, tables[1:]):
            if a.page == b.page:
                assert a.bbox[1] <= b.bbox[1]


def test_extract_tables_is_reproducible(manual_tables):
    assert parser._extract_tables(SRM_PDF) == manual_tables[SRM_PDF.name]


def test_extract_tables_wraps_unreadable_pdf_as_dependency_error(tmp_path):
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"not a pdf")
    with pytest.raises(DependencyError):
        parser._extract_tables(broken)
    with pytest.raises(DependencyError):
        parser._extract_tables(tmp_path / "missing.pdf")


# --- _validate_table ------------------------------------------------------


def test_validate_table_reports_none_blank_and_short_rows():
    rows = [
        ["코드", "명칭", "조치"],
        ["S101", None, "재시도"],
        ["S102", "  ", "점검"],
        ["S103", "과주행"],
    ]
    assert parser._validate_table(rows) == [(1, 1), (2, 1), (3, 2)]


def test_validate_table_returns_empty_for_clean_or_empty_table():
    assert parser._validate_table([["a", "b"], ["c", "d"]]) == []
    assert parser._validate_table([]) == []


# --- _table_to_drafts -----------------------------------------------------


def test_table_to_drafts_small_table_is_one_markdown_draft():
    drafts = parser._table_to_drafts(table([["코드", "명칭"], ["S101", "X축 위치 편차\n초과"], ["S102", "a|b"]]))
    assert drafts == [
        ChunkDraft(
            page=3,
            top=120.0,
            kind="table",
            section=None,
            text="표 9-4. 에러 코드 일람\n| 코드 | 명칭 |\n| --- | --- |\n| S101 | X축 위치 편차 초과 |\n| S102 | a\\|b |",
        )
    ]


def test_table_to_drafts_splits_large_table_and_repeats_header():
    rows = [["코드", "조치"]] + [[f"S{n}", "가" * 60] for n in range(101, 151)]
    drafts = parser._table_to_drafts(table(rows))
    assert len(drafts) > 1
    for d in drafts:
        assert len(d.text) <= parser.DRAFT_MAX_CHARS
        assert d.text.startswith("표 9-4. 에러 코드 일람\n| 코드 | 조치 |\n| --- | --- |\n")
    # 모든 행이 한 번씩, 순서대로 들어 있다.
    found = [line.split(" | ")[0][2:] for d in drafts for line in d.text.splitlines()[3:]]
    assert found == [f"S{n}" for n in range(101, 151)]


def test_table_to_drafts_does_not_cut_a_long_row():
    long_cell = "나" * (parser.DRAFT_MAX_CHARS + 50)
    drafts = parser._table_to_drafts(table([["코드", "조치"], ["S101", "짧음"], ["S102", long_cell], ["S103", "짧음"]]))
    assert len(drafts) == 3
    assert long_cell in drafts[1].text


def test_table_to_drafts_marks_continued_and_handles_missing_caption():
    cont = parser._table_to_drafts(table([["코드"], ["S110"]], continued=True))
    assert cont[0].text.startswith("표 9-4. 에러 코드 일람 (계속)\n")
    no_caption = parser._table_to_drafts(table([["항목", "값"], ["하중"]], caption=None))
    assert no_caption[0].text == "| 항목 | 값 |\n| --- | --- |\n| 하중 |  |"
    assert parser._table_to_drafts(table([])) == []


# --- [보류] 이미지 페이지·OCR 테스트(parser의 OCR 코드가 주석이라 함께 막아 둠) ---

# # --- _find_image_pages ----------------------------------------------------
#
#
# @pytest.mark.parametrize(
#     ("text", "areas", "expected"),
#     [
#         ("", [], True),  # 글자 없음
#         ("본문", [], False),
#         ("본문", [0.25 * 1000], True),  # 경계값: 25%면 이미지 페이지
#         ("본문", [0.1 * 1000, 0.1 * 1000], False),  # 합 20%
#     ],
# )
# def test_is_image_page_rule(text, areas, expected):
#     assert parser._is_image_page(text, 1000.0, areas) is expected
#
#
# def test_find_image_pages_detects_image_only_pages_and_skips_cover(tmp_path):
#     # PDF는 표지 + 그림 쪽 2개. 표지는 청킹 대상이 아니라 빠지고, 나머지는 인쇄 번호 1, 2로 센다.
#     assert parser._find_image_pages(image_pdf(tmp_path / "scan.pdf", pages=2)) == [1, 2]
#
#
# def test_find_image_pages_is_empty_for_current_manual():
#     # 현재 매뉴얼에는 이미지 페이지가 없다(실측). 바뀌면 OCR 경로가 실제 실행에 들어온다.
#     assert parser._find_image_pages(SRM_PDF) == []
#
#
# def test_find_image_pages_wraps_unreadable_pdf(tmp_path):
#     with pytest.raises(DependencyError):
#         parser._find_image_pages(tmp_path / "missing.pdf")
#
#
# # --- set_vision_model / _ocr_page ------------------------------------------
#
#
# def test_ocr_page_sends_300dpi_png_with_transcription_prompt(vision, tmp_path):
#     fake = FakeVisionModel(reply="  전사 결과\n  ")
#     vision.set_vision_model(fake)
#     assert vision._ocr_page(image_pdf(tmp_path / "scan.pdf"), 1) == "전사 결과"
#
#     (message,) = fake.calls[0]
#     text_part, image_part = message.content
#     assert "OCR 작업" in text_part["text"] and "그대로 전사" in text_part["text"]
#     assert len(fake.calls) == 1  # 거부가 아니면 다시 부르지 않는다
#     url = image_part["image_url"]["url"]
#     assert url.startswith("data:image/png;base64,")
#     assert image_part["image_url"]["detail"] == "high"
#     # 300dpi: A4 폭 595pt → 약 2480px
#     import base64
#     import io
#
#     width, _ = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))).size
#     assert abs(width - 595 * 300 / 72) <= 3
#
#
# def test_ocr_page_without_model_is_dependency_error(vision, tmp_path):
#     with pytest.raises(DependencyError, match="set_vision_model"):
#         vision._ocr_page(image_pdf(tmp_path / "scan.pdf"), 1)
#
#
# def test_ocr_page_wraps_model_failure(vision, tmp_path):
#     vision.set_vision_model(FakeVisionModel(error=TimeoutError("timed out")))
#     with pytest.raises(DependencyError, match="TimeoutError"):
#         vision._ocr_page(image_pdf(tmp_path / "scan.pdf"), 1)
#
#
# @pytest.mark.parametrize("page_no", [0, 2])  # 0은 표지, 2는 표지를 뺀 쪽 수(1)를 넘음
# def test_ocr_page_rejects_page_out_of_range(vision, tmp_path, page_no):
#     vision.set_vision_model(FakeVisionModel())
#     with pytest.raises(ValidationError):
#         vision._ocr_page(image_pdf(tmp_path / "scan.pdf"), page_no)
#
#
# def test_ocr_page_retries_in_english_once_after_refusal(vision, tmp_path):
#     fake = FakeVisionModel(replies=["I'm sorry, I can't assist with that.", "| S135 | 시간 초과 |"])
#     vision.set_vision_model(fake)
#     assert vision._ocr_page(image_pdf(tmp_path / "scan.pdf"), 1) == "| S135 | 시간 초과 |"
#     assert len(fake.calls) == 2
#     retry_text = fake.calls[1][0].content[0]["text"]
#     assert "Plain OCR task" in retry_text
#     assert fake.calls[1][0].content[1] == fake.calls[0][0].content[1]  # 같은 이미지를 다시 보낸다
#
#
# def test_ocr_page_refused_twice_is_dependency_error(vision, tmp_path):
#     fake = FakeVisionModel(replies=["I'm sorry, I can't assist with that."])
#     vision.set_vision_model(fake)
#     with pytest.raises(DependencyError, match="거부"):
#         vision._ocr_page(image_pdf(tmp_path / "scan.pdf"), 1)
#     assert len(fake.calls) == 2
#
#
# def test_ocr_page_joins_list_content(vision, tmp_path):
#     fake = FakeVisionModel(reply=[{"type": "text", "text": "첫 줄"}, {"type": "text", "text": " 둘째"}])
#     vision.set_vision_model(fake)
#     assert vision._ocr_page(image_pdf(tmp_path / "scan.pdf"), 1) == "첫 줄 둘째"
#
#
# # --- _ocr_to_drafts -------------------------------------------------------
#
#
# def test_ocr_to_drafts_empty_text_gives_no_drafts():
#     assert parser._ocr_to_drafts("", 5) == []
#     assert parser._ocr_to_drafts("  \n\n ", 5) == []
#
#
# def test_ocr_to_drafts_short_text_is_one_draft():
#     assert parser._ocr_to_drafts("첫 문단\n\n둘째 문단", 5) == [
#         ChunkDraft(page=5, top=0.0, kind="ocr", section=None, text="첫 문단\n\n둘째 문단")
#     ]
#
#
# def test_ocr_to_drafts_groups_paragraphs_under_limit_without_cutting():
#     paragraphs = [f"{i}번 " + "다" * 300 for i in range(6)] + ["라" * (parser.DRAFT_MAX_CHARS + 10)]
#     drafts = parser._ocr_to_drafts("\n\n".join(paragraphs), 7)
#     assert all(d.kind == "ocr" and d.page == 7 for d in drafts)
#     assert all(len(d.text) <= parser.DRAFT_MAX_CHARS for d in drafts[:-1])
#     assert drafts[-1].text == paragraphs[-1]  # 긴 문단은 자르지 않는다
#     assert "\n\n".join(d.text for d in drafts) == "\n\n".join(paragraphs)
