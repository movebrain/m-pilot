"""절 나누기·텍스트 초안 테스트: `_read_section_titles`, `_split_sections`, `_to_text_drafts`(#80).

번호 목록이 절 제목으로 잘리지 않는지, 청크가 최대 길이(800자)를 넘지 않는지가 핵심이다.
경계 사례는 가짜 `PageText`로, 실제 모양은 매뉴얼 PDF 4종으로 확인한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mpilot.db.parser import (
    PageText,
    Section,
    TextLine,
    _list_pdf_files,
    _read_section_titles,
    _split_sections,
    _split_text,
    _to_text_drafts,
)
from mpilot.models import ValidationError

MANUALS_DIR = Path(__file__).resolve().parents[1] / "data" / "manuals"
AGV_PDF = MANUALS_DIR / "agv_spec_sheet_dummy_rev1_1.pdf"
MINI_LOADER_PDF = MANUALS_DIR / "mini_loader_spec_sheet_dummy_rev1_1.pdf"
MAX_CHARS = 800


def page(number: int, *lines: tuple[float, str]) -> PageText:
    """가짜 쪽 하나. 줄은 `(top, text)`로 넣는다."""
    return PageText(
        manual_id="HMX-AGV-TSM-001",
        version="1.1",
        page=number,
        lines=[TextLine(top=top, text=text) for top, text in lines],
    )


def contents_page(number: int = 2) -> PageText:
    """절 세 개짜리 가짜 목차 쪽."""
    return page(
        number,
        (60.0, "목차"),
        (98.0, "7. 안전 19"),
        (114.0, "7.4 LOTO (Lock-Out / Tag-Out) 절차 19"),
        (130.0, "8.1 운전 모드 전환 20"),
    )


# ---------------------------------------------------------------------------
# 목차에서 절 제목 읽기
# ---------------------------------------------------------------------------


def test_read_section_titles_reads_contents_across_pages_and_stops_at_body():
    pages = [
        page(1, (60.0, "개정 이력")),
        page(2, (60.0, "목차"), (98.0, "1. 일반 사항 4"), (114.0, "1.1 문서의 목적 4")),
        page(3, (62.0, "부록 A. 주요 파라미터 기본값 42")),
        page(4, (60.0, "1. 일반 사항"), (99.0, "1.1 문서의 목적")),
    ]
    titles, contents_pages = _read_section_titles(pages)
    assert titles == {"1. 일반 사항", "1.1 문서의 목적", "부록 A. 주요 파라미터 기본값"}
    assert contents_pages == {1, 2}


def test_read_section_titles_raises_when_no_contents():
    with pytest.raises(ValidationError):
        _read_section_titles([page(1, (60.0, "1. 일반 사항"))])


def test_read_section_titles_raises_when_contents_is_empty():
    with pytest.raises(ValidationError):
        _read_section_titles([page(2, (60.0, "목차"), (98.0, "제목만 있고 쪽 번호가 없음"))])


# ---------------------------------------------------------------------------
# 절 나누기
# ---------------------------------------------------------------------------


def test_split_sections_does_not_split_numbered_list():
    pages = [
        contents_page(),
        page(
            19,
            (60.0, "7. 안전"),
            (99.0, "7.4 LOTO (Lock-Out / Tag-Out) 절차"),
            (120.0, "1. VCS에서 해당 차량을 [정비 모드]로 전환한다."),
            (135.0, "2. 차량을 정비 구역으로 이동시킨다."),
        ),
    ]
    sections = _split_sections(pages)
    assert len(sections) == 1  # 본문 없는 장 제목 `7. 안전`은 조각을 만들지 않는다
    assert sections[0].title == "7.4 LOTO (Lock-Out / Tag-Out) 절차"
    assert sections[0].text == (
        "7.4 LOTO (Lock-Out / Tag-Out) 절차\n\n"
        "1. VCS에서 해당 차량을 [정비 모드]로 전환한다.\n"
        "2. 차량을 정비 구역으로 이동시킨다."
    )


def test_split_sections_excludes_contents_and_keeps_text_before_first_title_untitled():
    pages = [page(1, (60.0, "개정 이력"), (95.0, "참고 본 문서는 최신 내용입니다.")), contents_page()]
    sections = _split_sections(pages)
    assert [(s.page, s.title) for s in sections] == [(1, None)]
    assert "목차" not in sections[0].text


def test_split_sections_repeats_title_on_each_page():
    pages = [
        contents_page(),
        page(19, (569.0, "7.4 LOTO (Lock-Out / Tag-Out) 절차"), (590.0, "1. 첫 단계.")),
        page(20, (58.0, "2. 다음 쪽으로 넘어간 단계."), (100.0, "8.1 운전 모드 전환"), (121.0, "운전 모드 본문.")),
    ]
    sections = _split_sections(pages)
    assert [(s.page, s.top, s.title) for s in sections] == [
        (19, 569.0, "7.4 LOTO (Lock-Out / Tag-Out) 절차"),
        (20, 58.0, "7.4 LOTO (Lock-Out / Tag-Out) 절차"),
        (20, 100.0, "8.1 운전 모드 전환"),
    ]
    assert sections[1].text == "2. 다음 쪽으로 넘어간 단계."


def test_split_sections_marks_paragraph_gap_with_blank_line():
    pages = [
        contents_page(),
        page(20, (99.0, "8.1 운전 모드 전환"), (121.0, "첫 문단 첫 줄"), (135.3, "첫 문단 둘째 줄"), (161.0, "둘째 문단")),
    ]
    [section] = _split_sections(pages)
    assert section.text == "8.1 운전 모드 전환\n\n첫 문단 첫 줄\n첫 문단 둘째 줄\n\n둘째 문단"


# ---------------------------------------------------------------------------
# 텍스트 초안
# ---------------------------------------------------------------------------


def section(text: str) -> Section:
    return Section(page=20, top=99.0, title="8.1 운전 모드 전환", text=text)


def test_to_text_drafts_keeps_short_section_whole():
    [draft] = _to_text_drafts([section("8.1 운전 모드 전환\n\n본문")])
    assert (draft.page, draft.top, draft.kind, draft.section) == (20, 99.0, "text", "8.1 운전 모드 전환")
    assert draft.text == "8.1 운전 모드 전환\n\n본문"


def test_to_text_drafts_splits_long_section_at_paragraphs():
    paragraphs = [f"{index}번 문단 " + "가" * 300 for index in range(4)]
    drafts = _to_text_drafts([section("\n\n".join(paragraphs))])
    # 문단을 자르지 않고 최대 길이까지 모은다.
    assert [d.text for d in drafts] == ["\n\n".join(paragraphs[:2]), "\n\n".join(paragraphs[2:])]
    assert all(d.section == "8.1 운전 모드 전환" and d.top == 99.0 for d in drafts)


def test_split_text_splits_long_paragraph_at_sentences():
    sentences = [f"{index}번 문장 " + "나" * 250 + "다." for index in range(4)]
    assert _split_text(" ".join(sentences)) == [" ".join(sentences[:3]), sentences[3]]


def test_split_text_cuts_sentence_longer_than_max():
    assert [len(chunk) for chunk in _split_text("가" * (MAX_CHARS * 2 + 10))] == [MAX_CHARS, MAX_CHARS, 10]


def test_split_text_does_not_break_at_list_numbers():
    # `1.` 뒤 공백은 문장 끝이 아니다.
    text = "1. " + "가" * 500 + "다. 2. " + "나" * 500 + "다."
    assert _split_text(text) == ["1. " + "가" * 500 + "다.", "2. " + "나" * 500 + "다."]


def test_to_text_drafts_skips_empty_text():
    assert _to_text_drafts([section("  \n\n "), section("")]) == []


# ---------------------------------------------------------------------------
# 실제 매뉴얼 PDF
# ---------------------------------------------------------------------------


def test_agv_loto_numbered_list_stays_in_one_draft(real_page_texts):
    drafts = _to_text_drafts(_split_sections(real_page_texts(AGV_PDF.name)))
    [loto] = [d for d in drafts if d.section == "7.4 LOTO (Lock-Out / Tag-Out) 절차"]
    assert loto.page == 22  # 합본은 목차가 5쪽으로 늘어 원본(19쪽)보다 3쪽 뒤에 있다
    for step in range(1, 7):
        assert f"\n{step}. " in loto.text


def test_title_keeps_error_codes_printed_in_other_font(real_page_texts):
    # 괄호 속 코드는 다른 글꼴이라 몇 pt 아래에 찍혀 있어도 제목과 같은 줄로 읽혀야 한다.
    titles = {d.section for d in _to_text_drafts(_split_sections(real_page_texts(MINI_LOADER_PDF.name)))}
    assert "11.1 정지 위치 불량 (M405 / M413 반복)" in titles


@pytest.mark.parametrize("pdf_path", _list_pdf_files(MANUALS_DIR), ids=lambda path: path.stem)
def test_real_manual_drafts_are_bounded_and_reproducible(pdf_path, real_page_texts):
    page_texts = real_page_texts(pdf_path.name)
    drafts = _to_text_drafts(_split_sections(page_texts))
    assert drafts
    assert all(0 < len(draft.text) <= MAX_CHARS and draft.text.strip() == draft.text for draft in drafts)
    assert not any(draft.text.startswith("목차") for draft in drafts)
    assert _to_text_drafts(_split_sections(page_texts)) == drafts
