"""청크 번호 매기기 테스트: `_number_chunks`(#81).

청크 ID `manual:<manual_id>:<page>:<chunk_no>`가 여기서 정해지므로 정렬·번호·절 채우기가 늘 같아야 한다.
초안은 가짜 `ChunkDraft`로 만든다.
"""

from __future__ import annotations

import random

import pytest

from mpilot.db.parser.chunks import _number_chunks
from mpilot.db.parser.types import ChunkDraft
from mpilot.models import Equipment, ValidationError

MANUAL_ID = "HMX-AGV-TSM-001"


def draft(page: int, top: float, text: str, kind: str = "text", section: str | None = None) -> ChunkDraft:
    return ChunkDraft(page=page, top=top, kind=kind, section=section, text=text)


def number(drafts: list[ChunkDraft], manual_id: str = MANUAL_ID):
    return _number_chunks(drafts, manual_id=manual_id, version="1.1", equipment=Equipment.AGV)


def test_number_chunks_sorts_by_position_and_restarts_number_on_each_page():
    drafts = [
        draft(20, 300.0, "20쪽 아래", section="8.2"),
        draft(19, 500.0, "19쪽 아래", section="7.4"),
        draft(20, 60.0, "20쪽 위", section="8.1"),
        draft(19, 100.0, "19쪽 위", section="7.3"),
    ]
    chunks = number(drafts)
    assert [(c.page, c.chunk_no, c.text) for c in chunks] == [
        (19, 1, "19쪽 위"),
        (19, 2, "19쪽 아래"),
        (20, 1, "20쪽 위"),
        (20, 2, "20쪽 아래"),
    ]


def test_number_chunks_keeps_input_order_for_same_position():
    # 긴 절에서 나뉜 텍스트 초안은 같은 top을 공유한다. 본문 순서가 그대로 번호 순서가 되어야 한다.
    drafts = [draft(10, 99.0, "앞 조각", section="3.2"), draft(10, 99.0, "뒤 조각", section="3.2")]
    assert [c.text for c in number(drafts)] == ["앞 조각", "뒤 조각"]


def test_number_chunks_copies_source_metadata():
    [chunk] = number([draft(4, 60.0, "본문", section="1.1 문서의 목적")])
    assert (chunk.manual_id, chunk.version, chunk.equipment) == (MANUAL_ID, "1.1", Equipment.AGV)
    assert (chunk.page, chunk.section, chunk.chunk_no, chunk.text) == (4, "1.1 문서의 목적", 1, "본문")


def test_number_chunks_is_reproducible_regardless_of_input_order():
    drafts = [draft(page, top, f"{page}-{top}", section="절") for page in (3, 4, 5) for top in (60.0, 200.0, 400.0)]
    shuffled = drafts[:]
    random.Random(0).shuffle(shuffled)
    assert number(shuffled) == number(drafts)
    assert number(drafts) == number(drafts)


def test_number_chunks_returns_empty_for_no_drafts():
    assert number([]) == []


# ---------------------------------------------------------------------------
# 절 채우기
# ---------------------------------------------------------------------------


def test_table_inherits_section_of_previous_draft_even_across_pages():
    drafts = [
        draft(21, 60.0, "9.2 에러 코드 일람\n\n본문", section="9.2 에러 코드 일람"),
        draft(21, 120.0, "표 9-4. 에러 코드 일람\n| 코드 | 명칭 |", kind="table"),
        draft(22, 58.0, "표 9-4. 에러 코드 일람 (계속)\n| 코드 | 명칭 |", kind="table"),
    ]
    assert [c.section for c in number(drafts)] == ["9.2 에러 코드 일람"] * 3


def test_first_draft_without_section_takes_caption_of_first_table_on_same_page():
    # 1쪽 개정 이력: 첫 절보다 앞이라 앞 초안이 없고, 표 캡션은 본문보다 아래에 있다.
    drafts = [
        draft(1, 60.3, "개정 이력"),
        draft(1, 95.5, "표 0-1. 문서 개정 이력\n| 개정 | 일자 |", kind="table"),
    ]
    assert [c.section for c in number(drafts)] == ["표 0-1. 문서 개정 이력"] * 2


def test_first_draft_without_section_or_caption_takes_its_first_line():
    drafts = [
        draft(1, 60.3, "개정 이력\n참고 본 문서는 최신 내용입니다."),
        draft(1, 95.5, "| 개정 | 일자 |\n| --- | --- |", kind="table"),  # 캡션 없는 표는 머리글 행으로 시작한다
    ]
    assert [c.section for c in number(drafts)] == ["개정 이력"] * 2


def test_draft_section_wins_over_previous_section():
    drafts = [draft(5, 60.0, "a", section="2.1"), draft(5, 100.0, "b", section="2.2")]
    assert [c.section for c in number(drafts)] == ["2.1", "2.2"]


def test_raises_when_section_cannot_be_decided():
    with pytest.raises(ValidationError):
        number([draft(1, 60.0, "  ")])


# ---------------------------------------------------------------------------
# ID 구성 요소와 값 검증
# ---------------------------------------------------------------------------


def test_raises_when_manual_id_has_colon():
    with pytest.raises(ValidationError):
        number([draft(4, 60.0, "본문", section="1.1")], manual_id="HMX:AGV")


def test_pydantic_error_is_converted_to_validation_error():
    with pytest.raises(ValidationError) as caught:
        _number_chunks([draft(4, 60.0, "본문", section="1.1")], manual_id=MANUAL_ID, version="1.1", equipment="UFO")
    assert not caught.type.__module__.startswith("pydantic")
