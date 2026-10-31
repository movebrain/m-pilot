"""매뉴얼 PDF 표 추출·검증과 표 초안.

역할:
    PDF 쪽마다 표를 찾아 캡션·위치가 붙은 `RawTable`로 만들고, 마크다운 표 초안(`ChunkDraft(kind="table")`)으로 바꾼다.

책임:
    - 쪽 번호는 꼬리글의 인쇄 번호이고(텍스트 경로와 같은 `_parse_footer`), 표지는 건너뛴다.
    - 쪽을 넘어 이어진 표는 앞 표의 캡션을 이어받는다. 큰 표는 머리글을 복제해 행 단위로 나눈다.

의존: `parser.text`(꼬리글 해석), `parser.types`, `models`만 import한다(의존 규칙 §2.5).
소유: DB 파트(이재서)
"""

from __future__ import annotations

import re
from pathlib import Path

import pdfplumber

from mpilot.db.parser.text import _parse_footer
from mpilot.db.parser.types import ChunkDraft, RawTable
from mpilot.models import DependencyError

# ---------------------------------------------------------------------------
# 표 추출·초안(담당: 이재서)
#
# 흐름: PDF → `_extract_tables`(쪽마다 표 찾기) → `RawTable` → `_table_to_drafts`(마크다운 표) → `ChunkDraft`
# `_validate_table`은 빈 칸·병합 의심 칸을 찾아 사람이 확인할 수 있게 한다.
# ---------------------------------------------------------------------------

DRAFT_MAX_CHARS = 1000
"""표·OCR 초안 한 개의 목표 최대 글자 수. 한 행·한 문단이 이보다 길면 자르지 않고 그대로 둔다."""

# 표 바로 위 줄의 캡션. 매뉴얼마다 `표 9-4.`, `표 A-1.`, `표 B‑1.`(줄바꿈 없는 하이픈) 형태가 섞여 있고,
# Mini Loader 매뉴얼은 일부 숫자가 `(cid:22)`처럼 글꼴 코드로 나온다.
#   - `표` 뒤에 번호 한 덩어리(예: `9`, `B`, `(cid:22)`)
#   - 그 뒤에 `-`·`‑`·`–` 중 하나와 번호 한 덩어리가 더 올 수 있다(예: `-4`, `‑1`)
#   - 그 뒤에 `.`이 오고, 이어서 캡션 글자가 하나 이상 있다.
_CAPTION_PATTERN = re.compile(r"^표\s*[^\s.]+(?:\s*[-‑–]\s*[^\s.]+)?\s*\.\s*\S")

_CAPTION_MAX_GAP = 30.0
"""표 윗변과 캡션 줄 아랫변 사이의 최대 간격(pt). 실측은 3~7pt."""


def _open_pdf(path: Path) -> pdfplumber.PDF:
    """PDF를 연다. 열 수 없으면 의존 오류로 바꾼다.

    Args:
        path: PDF 파일 경로.

    Returns:
        pdfplumber.PDF: 열린 PDF. `with` 문으로 닫는다.

    Raises:
        DependencyError: 파일이 없거나 PDF로 읽을 수 없을 때.
    """
    try:
        return pdfplumber.open(path)
    except Exception as e:  # pdfminer 오류 종류가 많아 한곳에서 의존 오류로 바꾼다.
        raise DependencyError(f"PDF를 열지 못했습니다: {Path(path).name}({type(e).__name__})") from None


def _printed_page_number(lines: list[dict], pdf_page_number: int) -> int:
    """PDF 한 쪽의 꼬리글에 인쇄된 쪽 번호를 구한다.

    꼬리글 해석은 `_parse_footer`(텍스트 경로와 같은 규칙)에 맡긴다.
    꼬리글이 이미지라 글자로 읽히지 않는 쪽은 표지를 뺀 PDF 순서(PDF 쪽 순서 - 1)로 추정한다.

    Args:
        lines: `page.extract_text_lines()` 결과(각 줄의 `text`, `bottom`).
        pdf_page_number: PDF 안의 쪽 순서(1부터, 표지가 1). 표지는 이 함수로 부르지 않는다.

    Returns:
        int: 맨 아래 줄이 꼬리글이면 `N / T`의 N. 꼬리글을 읽을 수 없으면 `pdf_page_number - 1`.
    """
    # 1) 글자가 하나도 없는 쪽이면 PDF 순서로 추정한다.
    if not lines:
        return pdf_page_number - 1

    # 2) 맨 아래 줄(아랫변 `bottom`이 가장 큰 줄)을 찾는다. 꼬리글은 쪽 맨 아래에 있다.
    last_line = lines[0]
    for line in lines:
        if line["bottom"] > last_line["bottom"]:
            last_line = line

    # 3) 그 줄을 꼬리글로 읽어 본다. 꼬리글이 아니면 PDF 순서로 추정한다.
    footer = _parse_footer(last_line["text"])
    if footer is None:
        return pdf_page_number - 1
    return footer.page


def _find_caption(lines: list[dict], table_top: float) -> str | None:
    """표 바로 위 줄에서 캡션을 찾는다.

    Args:
        lines: `page.extract_text_lines()` 결과(각 줄의 `text`, `bottom`).
        table_top: 표 윗변 위치(pt).

    Returns:
        str | None: 표 윗변에서 `_CAPTION_MAX_GAP` 안에 있는 가장 가까운 캡션 줄. 없으면 None.
    """
    # 1) 표보다 위에 있는 줄만 고른다(글꼴에 따라 1pt쯤 겹칠 수 있어 여유를 둔다).
    lines_above = []
    for line in lines:
        if line["bottom"] <= table_top + 1:
            lines_above.append(line)

    # 2) 표에 가까운 줄(아랫변이 큰 줄)부터 차례로 본다.
    lines_above.sort(key=lambda line: line["bottom"], reverse=True)

    for line in lines_above:
        # 3) 표에서 너무 멀어지면 더 볼 필요가 없다.
        if table_top - line["bottom"] > _CAPTION_MAX_GAP:
            break
        # 4) 캡션 형식(`표 9-4. ...`)이면 그 줄이 캡션이다.
        text = line["text"].strip()
        if _CAPTION_PATTERN.match(text):
            return text
    return None


def _is_continued_table(
    previous: RawTable | None,
    page: int,
    index: int,
    caption: str | None,
    rows: list[list[str | None]],
) -> bool:
    """이 표가 앞 쪽 마지막 표가 이어진 것인지 판정한다.

    아래 조건을 모두 만족하면 이어진 표로 본다.
    캡션이 없다 → 쪽 맨 위 표다 → 앞 표가 바로 앞 쪽에 있다 → 두 표 모두 행이 있다 → 머리글 행이 같다.

    Args:
        previous: 바로 앞에서 찾은 표. 이 PDF의 첫 표면 None.
        page: 이 표가 있는 쪽(인쇄 쪽 번호).
        index: 이 표의 쪽 안 순서(1부터).
        caption: 이 표 위에서 찾은 캡션. 없으면 None.
        rows: 이 표의 행·열 셀 값.

    Returns:
        bool: 앞 표가 이어진 것이면 True.
    """
    # 1) 자기 캡션이 있으면 새 표다.
    if caption is not None:
        return False
    # 2) 쪽 맨 위 표가 아니면 이어진 표가 아니다.
    if index != 1:
        return False
    # 3) 앞 표가 없거나 바로 앞 쪽에 있지 않으면 이어진 표가 아니다.
    if previous is None:
        return False
    if previous.page != page - 1:
        return False
    # 4) 두 표 중 하나라도 행이 없으면 머리글을 비교할 수 없다.
    if not rows or not previous.rows:
        return False
    # 5) 머리글 행(첫 행)이 같으면 이어진 표다.
    return rows[0] == previous.rows[0]


def _extract_tables(path: Path) -> list[RawTable]:
    """페이지별 표를 행·열 구조로 추출하고 캡션과 위치(bbox)를 붙인다.

    표지(PDF 첫 쪽)는 청킹 대상이 아니라 건너뛴다. `RawTable.page`는 꼬리글의 인쇄 쪽 번호다.
    다음 쪽 맨 위 표가 캡션 없이 앞 쪽 마지막 표와 같은 머리글로 시작하면, 쪽을 넘어 이어진 표로 보고
    앞 표의 캡션을 이어받는다.

    Args:
        path: 매뉴얼 PDF 파일 경로.

    Returns:
        list[RawTable]: 쪽 순서, 쪽 안에서는 위에서부터의 순서로 정렬한 표 목록. 같은 PDF면 항상 같다.

    Raises:
        DependencyError: PDF를 읽거나 표를 찾는 데 실패했을 때.
    """
    tables: list[RawTable] = []
    with _open_pdf(path) as pdf:
        try:
            # 첫 쪽(표지)은 청킹 대상이 아니라 둘째 쪽부터 본다.
            for page in pdf.pages[1:]:
                # 1) 쪽의 글자 줄과 인쇄 쪽 번호를 읽는다.
                lines = page.extract_text_lines()
                printed_page = _printed_page_number(lines, page.page_number)

                # 2) 표를 찾아 위에서부터(같은 높이면 왼쪽부터) 정렬한다. bbox는 (x0, top, x1, bottom)이다.
                found_tables = page.find_tables()
                found_tables.sort(key=lambda found: (found.bbox[1], found.bbox[0]))

                for index, found in enumerate(found_tables, start=1):
                    # 3) 셀 값을 행 목록으로 옮긴다.
                    rows = []
                    for row in found.extract():
                        rows.append(list(row))

                    # 4) 캡션을 찾고, 앞 쪽에서 이어진 표면 앞 표의 캡션을 이어받는다.
                    caption = _find_caption(lines, found.bbox[1])
                    previous = None
                    if tables:
                        previous = tables[-1]
                    continued = _is_continued_table(previous, printed_page, index, caption, rows)
                    if continued:
                        caption = previous.caption

                    # 5) RawTable로 만들어 모은다.
                    x0, top, x1, bottom = found.bbox
                    tables.append(
                        RawTable(
                            page=printed_page,
                            index=index,
                            bbox=(float(x0), float(top), float(x1), float(bottom)),
                            caption=caption,
                            rows=rows,
                            continued=continued,
                        )
                    )
        except Exception as e:  # pdfminer·pdfplumber 오류 종류가 많아 한곳에서 의존 오류로 바꾼다.
            raise DependencyError(f"표를 추출하지 못했습니다: {Path(path).name}({type(e).__name__})") from None
    return tables


def _table_width(rows: list[list[str | None]]) -> int:
    """표의 열 수를 구한다. 행마다 칸 수가 다를 수 있어 가장 긴 행을 기준으로 한다.

    Args:
        rows: 표의 행·열 셀 값.

    Returns:
        int: 가장 긴 행의 칸 수. 행이 없으면 0.
    """
    width = 0
    for row in rows:
        width = max(width, len(row))
    return width


def _cell_at(row: list[str | None], column: int) -> str | None:
    """행에서 한 칸의 값을 꺼낸다. 행이 짧아 그 칸이 없으면 None으로 본다.

    Args:
        row: 셀 값 목록.
        column: 꺼낼 열 번호(0부터).

    Returns:
        str | None: 셀 값. 칸이 없으면 None.
    """
    if column < len(row):
        return row[column]
    return None


def _validate_table(rows: list[list[str | None]]) -> list[tuple[int, int]]:
    """빈 셀과 병합 의심 셀의 좌표를 찾는다.

    `None`(병합되었거나 추출되지 않은 셀), 공백뿐인 셀, 가장 긴 행보다 짧은 행의 모자란 칸을 문제로 본다.

    Args:
        rows: 표의 행·열 셀 값(`RawTable.rows`).

    Returns:
        list[tuple[int, int]]: 문제 셀의 `(행, 열)` 좌표(0부터). 행 우선 순서. 문제가 없으면 빈 목록.
    """
    width = _table_width(rows)
    problems: list[tuple[int, int]] = []
    for row_no, row in enumerate(rows):
        for column in range(width):
            # 모자란 칸은 None으로 꺼내지므로 병합·빈 셀과 같이 처리된다.
            cell = _cell_at(row, column)
            # None이거나 공백뿐이면 문제 칸이다.
            if cell is None or cell.strip() == "":
                problems.append((row_no, column))
    return problems


def _cell_text(cell: str | None) -> str:
    """셀 값을 마크다운 표 칸 글자로 바꾼다(줄바꿈은 공백, `|`는 이스케이프).

    Args:
        cell: 셀 값. None이면 빈 칸.

    Returns:
        str: 마크다운 표에 넣을 한 줄 글자.
    """
    if cell is None:
        return ""
    # 줄바꿈·연속 공백을 공백 하나로 바꾼다.
    text = " ".join(cell.split())
    # `|`는 마크다운 표의 칸 구분자라 `\|`로 바꾼다.
    return text.replace("|", "\\|")


def _markdown_row(row: list[str | None], width: int) -> str:
    """한 행을 마크다운 표 줄로 만든다. 모자란 칸은 빈 칸으로 채운다.

    Args:
        row: 셀 값 목록.
        width: 표의 열 수.

    Returns:
        str: `| a | b |` 형태의 한 줄.
    """
    cells = []
    for column in range(width):
        cells.append(_cell_text(_cell_at(row, column)))
    return "| " + " | ".join(cells) + " |"


def _table_to_drafts(table: RawTable) -> list[ChunkDraft]:
    """표를 청크 초안으로 만든다.

    초안 본문은 캡션 한 줄과 마크다운 표다. 전체가 `DRAFT_MAX_CHARS` 이하면 초안 1개, 넘으면 행 단위로 나누고
    나눈 초안마다 캡션과 머리글 행을 다시 붙인다. 행은 중간에서 자르지 않으므로 한 행이 기준보다 길면
    그 행만 담은 초안이 기준을 넘을 수 있다.

    Args:
        table: `_extract_tables`가 만든 표.

    Returns:
        list[ChunkDraft]: `kind="table"`, `top=표 윗변`, `section=None`인 초안 목록. 행이 없으면 빈 목록.
    """
    if not table.rows:
        return []
    width = _table_width(table.rows)

    # 1) 초안마다 맨 앞에 붙일 줄: 캡션, 머리글 행, 구분선.
    head_lines = []
    if table.caption:
        if table.continued:
            head_lines.append(table.caption + " (계속)")
        else:
            head_lines.append(table.caption)
    head_lines.append(_markdown_row(table.rows[0], width))
    head_lines.append("| " + " | ".join(["---"] * width) + " |")

    # 2) 머리글을 뺀 나머지 행을 마크다운 줄로 바꾼다.
    body_lines = []
    for row in table.rows[1:]:
        body_lines.append(_markdown_row(row, width))

    # 3) 행을 차례로 담다가, 다음 행을 더하면 기준 글자 수를 넘을 때 새 묶음을 시작한다.
    #    묶음이 비어 있으면 아무리 긴 행이라도 일단 담는다(행을 자르지 않기 위해).
    groups: list[list[str]] = []
    current: list[str] = []
    for line in body_lines:
        candidate = "\n".join(head_lines + current + [line])
        if current and len(candidate) > DRAFT_MAX_CHARS:
            groups.append(current)
            current = []
        current.append(line)
    groups.append(current)

    # 4) 묶음마다 머리글을 붙여 초안으로 만든다.
    drafts = []
    for group in groups:
        text = "\n".join(head_lines + group)
        drafts.append(ChunkDraft(page=table.page, top=table.bbox[1], kind="table", section=None, text=text))
    return drafts
