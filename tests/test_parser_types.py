"""parser 공유 타입 테스트: RawTable, ChunkDraft(#43).

두 타입은 표 경로(이재서)와 텍스트 경로(손재호)가 주고받는 형식이라, 필드와 불변·값 제약이 바뀌면 바로 알 수 있게 한다.
"""

from __future__ import annotations

import pydantic
import pytest

from mpilot.db.parser import ChunkDraft, RawTable


def raw_table(**overrides) -> RawTable:
    values = dict(
        page=23,
        index=1,
        bbox=(45.5, 57.5, 549.4, 787.1),
        caption="표 9-4. 에러 코드 일람",
        rows=[["코드", "명칭"], ["S101", None]],
        continued=False,
    )
    values.update(overrides)
    return RawTable(**values)


def test_raw_table_keeps_fields_and_allows_none_cells_and_caption():
    table = raw_table(caption=None)
    assert table.caption is None
    assert table.rows[1][1] is None
    assert table.bbox == (45.5, 57.5, 549.4, 787.1)


def test_chunk_draft_accepts_three_kinds():
    for kind in ("text", "table", "ocr"):
        draft = ChunkDraft(page=1, top=0.0, kind=kind, section=None, text="본문")
        assert draft.kind == kind


@pytest.mark.parametrize(
    "make",
    [
        lambda: raw_table(page=0),  # 쪽은 1부터
        lambda: raw_table(index=0),  # 쪽 안 순서도 1부터
        lambda: raw_table(bbox=(1.0, 2.0, 3.0)),  # bbox는 4개 값
        lambda: raw_table(bbox=(100.0, 200.0, 50.0, 250.0)),  # x0 > x1
        lambda: raw_table(bbox=(10.0, 300.0, 50.0, 250.0)),  # top > bottom
        lambda: ChunkDraft(page=0, top=0.0, kind="text", section=None, text="x"),
        lambda: ChunkDraft(page=1, top=0.0, kind="image", section=None, text="x"),  # 정해진 종류만
    ],
)
def test_types_reject_invalid_values(make):
    with pytest.raises(pydantic.ValidationError):
        make()


def test_raw_table_bbox_allows_equal_coordinates():
    # 폭이나 높이가 0인 영역(x0 == x1, top == bottom)은 뒤집힌 것이 아니라서 허용한다.
    assert raw_table(bbox=(10.0, 20.0, 10.0, 20.0)).bbox == (10.0, 20.0, 10.0, 20.0)


def test_types_are_frozen():
    with pytest.raises(pydantic.ValidationError):
        raw_table().page = 2
    draft = ChunkDraft(page=1, top=0.0, kind="text", section="9.4", text="본문")
    with pytest.raises(pydantic.ValidationError):
        draft.section = "9.5"
