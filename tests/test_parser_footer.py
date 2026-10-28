"""꼬리글 읽기 테스트: `_parse_footer`, `_read_manual_identity`(#43).

꼬리글에서 청크 ID의 `manual_id`·`page`와 메타데이터 `version`을 읽으므로, 형식 해석이 어긋나면
청크 ID 전체가 바뀐다. 실제 매뉴얼 PDF 4종으로도 확인한다.
"""

from __future__ import annotations

from pathlib import Path

import pdfplumber
import pytest

from mpilot.db.parser import Footer, _parse_footer, _read_manual_identity
from mpilot.models import ValidationError

MANUALS_DIR = Path(__file__).resolve().parents[1] / "data" / "manuals"

# 실제 꼬리글 한 줄(AGV 매뉴얼 PDF 4번째 쪽).
AGV_FOOTER = "HMX-AGV-TSM-001 Rev. 1.1 · AGV 기술 사양서 및 운영·정비 매뉴얼 교육용 가상 문서 3 / 44"


def footer(manual_id: str = "HMX-AGV-TSM-001", version: str = "1.1", page: int = 1) -> Footer:
    return Footer(manual_id=manual_id, version=version, page=page, total_pages=44)


def test_parse_footer_reads_id_version_and_printed_page():
    assert _parse_footer(AGV_FOOTER) == Footer(manual_id="HMX-AGV-TSM-001", version="1.1", page=3, total_pages=44)


def test_parse_footer_ignores_surrounding_spaces():
    assert _parse_footer(f"  {AGV_FOOTER}  ") is not None


@pytest.mark.parametrize(
    "line",
    [
        "8.1 운전 모드 전환",  # 본문 줄
        "HYUNDAI MOVEX · AGV (Automated Guided Vehicle) — DUMMY DATA",  # 머리글
        "HMX-AGV-TSM-001 Rev. 1.1 · AGV 기술 사양서",  # 쪽 번호 없음
        "HMX-AGV-TSM-001 · AGV 기술 사양서 3 / 44",  # Rev 없음
        "HMX:AGV-TSM-001 Rev. 1.1 · AGV 기술 사양서 3 / 44",  # 문서번호에 `:`
        "",
    ],
)
def test_parse_footer_returns_none_for_other_lines(line):
    assert _parse_footer(line) is None


def test_read_manual_identity_returns_common_id_and_version():
    assert _read_manual_identity([footer(page=1), footer(page=2)]) == ("HMX-AGV-TSM-001", "1.1")


@pytest.mark.parametrize(
    "footers",
    [
        [],  # 꼬리글 없음
        [footer(page=1), footer(version="1.2", page=2)],  # 쪽마다 버전이 다름
        [footer(page=1), footer(manual_id="HMX-SRM-TSM-001", page=2)],  # 쪽마다 문서번호가 다름
    ],
)
def test_read_manual_identity_rejects_missing_or_mixed(footers):
    with pytest.raises(ValidationError):
        _read_manual_identity(footers)


@pytest.mark.parametrize(
    ("file_name", "manual_id", "version"),
    [
        ("agv_spec_sheet_dummy_rev1_1.pdf", "HMX-AGV-TSM-001", "1.1"),
        ("mini_loader_spec_sheet_dummy_rev1_1.pdf", "HMX-MLD-TSM-001", "1.1"),
        ("rtv_spec_sheet_dummy_rev2_1.pdf", "HMX-RTV-TSM-001", "2.1"),
        ("srm_spec_sheet_dummy_rev1_2.pdf", "HMX-SRM-TSM-001", "1.2"),
    ],
)
def test_real_manuals_have_footer_on_every_page_but_cover(file_name, manual_id, version):
    # 표지(PDF 1번째 쪽)만 꼬리글이 없고, 나머지 쪽은 인쇄 쪽 번호 = PDF 순서 - 1 이어야 한다.
    with pdfplumber.open(MANUALS_DIR / file_name) as pdf:
        last_lines = [(page.extract_text() or "").splitlines()[-1:] for page in pdf.pages]
    footers = [_parse_footer(lines[0]) if lines else None for lines in last_lines]

    assert footers[0] is None
    body = footers[1:]
    assert all(f is not None for f in body)
    assert [f.page for f in body] == list(range(1, len(body) + 1))
    assert {f.total_pages for f in body} == {len(body)}
    assert _read_manual_identity(body) == (manual_id, version)
