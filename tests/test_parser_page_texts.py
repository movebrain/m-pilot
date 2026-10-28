"""쪽별 본문 추출 테스트: `_list_pdf_files`, `_equipment_from_manual_id`, `_extract_page_texts`(#79).

청크 본문에는 순수 본문만, 출처에는 꼬리글 값만 들어가야 한다. 정상 흐름은 실제 매뉴얼 PDF 4종으로,
그런 PDF로는 만들 수 없는 오류 상황은 가짜 PDF 객체로 확인한다. 표는 가짜 `RawTable`로 대신한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mpilot.db import parser
from mpilot.db.parser import (
    RawTable,
    _equipment_from_manual_id,
    _extract_page_texts,
    _is_char_in_table_area,
    _list_pdf_files,
    _parse_footer,
)
from mpilot.models import DependencyError, Equipment, ValidationError

MANUALS_DIR = Path(__file__).resolve().parents[1] / "data" / "manuals"
AGV_PDF = MANUALS_DIR / "agv_spec_sheet_dummy_rev1_1.pdf"

AGV_HEADER = "HYUNDAI MOVEX · AGV (Automated Guided Vehicle) — DUMMY DATA"


def agv_footer(page: int, version: str = "1.1") -> str:
    return f"HMX-AGV-TSM-001 Rev. {version} · AGV 기술 사양서 및 운영·정비 매뉴얼 교육용 가상 문서 {page} / 44"


def fake_table(page: int, bbox: tuple[float, float, float, float]) -> RawTable:
    return RawTable(page=page, index=1, bbox=bbox, caption=None, rows=[["가"]], continued=False)


# ---------------------------------------------------------------------------
# 가짜 PDF: `extract_text_lines`·`filter`만 흉내 낸다. 줄 하나를 글자 하나처럼 다룬다.
# ---------------------------------------------------------------------------


def char(text: str, x0: float, x1: float, top: float) -> dict:
    """pdfplumber 글자 객체를 흉내 낸 줄 하나."""
    return {"object_type": "char", "text": text, "x0": x0, "x1": x1, "top": top, "bottom": top + 9}


class FakePage:
    def __init__(self, texts: list[str] | None = None, objects: list[dict] | None = None):
        if objects is None:
            objects = [
                char(text, x0=50.0, x1=500.0, top=20.0 * i)
                for i, text in enumerate(texts or [])
            ]
        self.objects = objects

    def extract_text_lines(self, **kwargs) -> list[dict]:
        return list(self.objects)

    def filter(self, keep) -> FakePage:
        return FakePage(objects=[obj for obj in self.objects if keep(obj)])


class FakePdf:
    def __init__(self, pages: list[FakePage]):
        self.pages = pages

    def __enter__(self) -> FakePdf:
        return self

    def __exit__(self, *exc) -> None:
        return None


@pytest.fixture
def open_fake_pdf(monkeypatch):
    """`pdfplumber.open`이 주어진 쪽들로 된 가짜 PDF를 돌려주게 한다."""

    def install(pages: list[FakePage]) -> None:
        monkeypatch.setattr(parser.pdfplumber, "open", lambda path: FakePdf(pages))

    return install


# ---------------------------------------------------------------------------
# _list_pdf_files
# ---------------------------------------------------------------------------


def test_list_pdf_files_returns_only_pdfs_sorted_by_name(tmp_path):
    for name in ["srm.pdf", "agv.PDF", "notes.txt", "mini.pdf"]:
        (tmp_path / name).write_bytes(b"")
    (tmp_path / "sub.pdf").mkdir()  # 폴더는 파일이 아니다

    assert [path.name for path in _list_pdf_files(tmp_path)] == ["agv.PDF", "mini.pdf", "srm.pdf"]


def test_list_pdf_files_finds_real_manuals():
    assert [path.name for path in _list_pdf_files(MANUALS_DIR)] == [
        "agv_spec_sheet_dummy_rev1_1.pdf",
        "mini_loader_spec_sheet_dummy_rev1_1.pdf",
        "rtv_spec_sheet_dummy_rev2_1.pdf",
        "srm_spec_sheet_dummy_rev1_2.pdf",
    ]


def test_list_pdf_files_rejects_folder_without_pdf(tmp_path):
    (tmp_path / "notes.txt").write_bytes(b"")
    with pytest.raises(ValidationError):
        _list_pdf_files(tmp_path)


def test_list_pdf_files_rejects_missing_folder(tmp_path):
    with pytest.raises(ValidationError):
        _list_pdf_files(tmp_path / "없음")


# ---------------------------------------------------------------------------
# _equipment_from_manual_id
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("manual_id", "equipment"),
    [
        ("HMX-AGV-TSM-001", Equipment.AGV),
        ("HMX-MLD-TSM-001", Equipment.MINI_LOADER),
        ("HMX-RTV-TSM-001", Equipment.RTV),
        ("HMX-SRM-TSM-001", Equipment.SRM),
    ],
)
def test_equipment_from_manual_id_maps_code(manual_id, equipment):
    assert _equipment_from_manual_id(manual_id) is equipment


@pytest.mark.parametrize("manual_id", ["HMX-XYZ-TSM-001", "HMX", "", "HMX-agv-TSM-001"])
def test_equipment_from_manual_id_rejects_unknown_code(manual_id):
    with pytest.raises(ValidationError):
        _equipment_from_manual_id(manual_id)


# ---------------------------------------------------------------------------
# _extract_page_texts: 실제 매뉴얼
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("file_name", "manual_id", "version", "equipment"),
    [
        # 파일명의 rev는 원본 개정이고, 합본은 원본에 13장을 더한 개정이라 꼬리글 버전이 0.1 높다.
        ("agv_spec_sheet_dummy_rev1_1.pdf", "HMX-AGV-TSM-001", "1.2", Equipment.AGV),
        ("mini_loader_spec_sheet_dummy_rev1_1.pdf", "HMX-MLD-TSM-001", "1.2", Equipment.MINI_LOADER),
        ("rtv_spec_sheet_dummy_rev2_1.pdf", "HMX-RTV-TSM-001", "2.2", Equipment.RTV),
        ("srm_spec_sheet_dummy_rev1_2.pdf", "HMX-SRM-TSM-001", "1.3", Equipment.SRM),
    ],
)
def test_extract_page_texts_reads_real_manuals(file_name, manual_id, version, equipment, real_page_texts):
    pages = real_page_texts(file_name)  # PDF를 읽는 데 오래 걸려 세션에서 한 번만 읽는다(conftest)

    # 표지는 빠지고 인쇄 쪽 번호가 1부터 이어진다.
    assert [page.page for page in pages] == list(range(1, len(pages) + 1))
    assert {(page.manual_id, page.version) for page in pages} == {(manual_id, version)}
    assert _equipment_from_manual_id(manual_id) is equipment

    for page in pages:
        assert page.lines, f"{page.page}쪽에 본문이 없습니다"
        texts = [line.text for line in page.lines]
        # 머리글·꼬리글은 본문에 남지 않는다.
        assert not any(text.startswith("HYUNDAI MOVEX ·") for text in texts)
        assert not any(_parse_footer(text) for text in texts)
        # 줄은 위에서부터 놓인다.
        tops = [line.top for line in page.lines]
        assert tops == sorted(tops)


def test_extract_page_texts_removes_text_inside_table_bbox(real_page_texts, disk_parse):
    without_table = real_page_texts(AGV_PDF.name)
    page3 = next(page for page in without_table if page.page == 3)
    target, following = page3.lines[1], page3.lines[2]
    # 둘째 줄만 덮는 표 영역. 같은 영역이 다른 쪽에 있으면 3쪽에는 영향이 없어야 한다.
    bbox = (0.0, target.top - 1, 600.0, following.top - 1)

    with_table = disk_parse.page_texts(AGV_PDF, [fake_table(3, bbox)])  # 실제 추출 결과를 디스크 캐시로 받는다
    with_other_page_table = disk_parse.page_texts(AGV_PDF, [fake_table(4, bbox)])

    page3_with_table = next(page for page in with_table if page.page == 3)
    assert page3_with_table.lines == [line for line in page3.lines if line != target]
    assert next(page for page in with_other_page_table if page.page == 3).lines == page3.lines


# ---------------------------------------------------------------------------
# _extract_page_texts: 가짜 PDF로 만든 경계·오류 상황
# ---------------------------------------------------------------------------


def test_extract_page_texts_skips_cover_and_strips_header_footer(open_fake_pdf):
    open_fake_pdf(
        [
            FakePage(["[ 교육용 가상 문서 · DUMMY DATA ]", "AGV"]),  # 표지
            FakePage([AGV_HEADER, "1. 개요", "본문", agv_footer(1)]),
            FakePage([AGV_HEADER, "2. 안전", agv_footer(2)]),
        ]
    )

    pages = _extract_page_texts(Path("agv.pdf"), tables=[])

    assert [(page.page, [line.text for line in page.lines]) for page in pages] == [
        (1, ["1. 개요", "본문"]),
        (2, ["2. 안전"]),
    ]
    assert [line.top for line in pages[0].lines] == [20.0, 40.0]


def test_extract_page_texts_keeps_body_line_that_looks_like_header_when_not_first(open_fake_pdf):
    # 머리글은 맨 위 줄일 때만 버린다.
    open_fake_pdf([FakePage(["표지"]), FakePage(["본문", AGV_HEADER, agv_footer(1)])])

    pages = _extract_page_texts(Path("agv.pdf"), tables=[])

    assert [line.text for line in pages[0].lines] == ["본문", AGV_HEADER]


def test_extract_page_texts_keeps_text_beside_table(open_fake_pdf):
    # 같은 높이라도 표 영역 밖(왼쪽) 글자는 남는다.
    beside = char("옆 본문", x0=10.0, x1=40.0, top=100.0)
    inside = char("표 셀", x0=300.0, x1=340.0, top=100.0)
    footer = char(agv_footer(1), x0=50.0, x1=500.0, top=818.0)
    open_fake_pdf([FakePage(["표지"]), FakePage(objects=[beside, inside, footer])])

    pages = _extract_page_texts(Path("agv.pdf"), tables=[fake_table(1, (200.0, 90.0, 550.0, 200.0))])

    assert [line.text for line in pages[0].lines] == ["옆 본문"]


@pytest.mark.parametrize(
    ("x0", "x1", "expected"),
    [
        (300.0, 340.0, True),  # 완전히 안
        (190.0, 230.0, True),  # 왼쪽 경계에 걸쳤지만 중심(210)은 안
        (170.0, 210.0, False),  # 왼쪽 경계에 걸쳤지만 중심(190)은 밖
        (10.0, 40.0, False),  # 완전히 밖
    ],
)
def test_is_char_in_table_area_judges_by_char_center(x0, x1, expected):
    table_boxes = [(200.0, 90.0, 550.0, 200.0)]
    assert _is_char_in_table_area(char("가", x0=x0, x1=x1, top=100.0), table_boxes) is expected


def test_extract_page_texts_rejects_missing_footer_after_cover(open_fake_pdf):
    open_fake_pdf([FakePage(["표지"]), FakePage([AGV_HEADER, "본문", agv_footer(1)]), FakePage([AGV_HEADER, "본문"])])
    with pytest.raises(ValidationError):
        _extract_page_texts(Path("agv.pdf"), tables=[])


def test_extract_page_texts_rejects_empty_page_after_cover(open_fake_pdf):
    open_fake_pdf([FakePage(["표지"]), FakePage([])])
    with pytest.raises(ValidationError):
        _extract_page_texts(Path("agv.pdf"), tables=[])


def test_extract_page_texts_rejects_mixed_versions(open_fake_pdf):
    open_fake_pdf([FakePage(["표지"]), FakePage(["본문", agv_footer(1)]), FakePage(["본문", agv_footer(2, version="1.2")])])
    with pytest.raises(ValidationError):
        _extract_page_texts(Path("agv.pdf"), tables=[])


def test_extract_page_texts_rejects_pdf_with_only_cover(open_fake_pdf):
    open_fake_pdf([FakePage(["표지"])])
    with pytest.raises(ValidationError):
        _extract_page_texts(Path("agv.pdf"), tables=[])


def test_extract_page_texts_wraps_read_failure(open_fake_pdf):
    class BrokenPage(FakePage):
        def extract_text_lines(self, **kwargs):
            raise RuntimeError("깨진 쪽")

    open_fake_pdf([BrokenPage()])
    with pytest.raises(DependencyError):
        _extract_page_texts(Path("agv.pdf"), tables=[])


@pytest.mark.parametrize("content", [b"not a pdf", None])
def test_extract_page_texts_raises_dependency_error_when_pdf_cannot_open(tmp_path, content):
    path = tmp_path / "broken.pdf"
    if content is not None:  # None이면 파일이 없는 경우
        path.write_bytes(content)
    with pytest.raises(DependencyError):
        _extract_page_texts(path, tables=[])
