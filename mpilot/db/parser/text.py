"""매뉴얼 PDF 텍스트 경로: 꼬리글 읽기, 쪽별 본문 추출(#79), 절 나누기와 텍스트 초안(#80).

역할:
    PDF 쪽마다 꼬리글로 출처(`manual_id`·쪽·버전)를 읽고, 표 영역·머리글·꼬리글을 뺀 본문을 목차의 절 제목으로 나눠
    텍스트 초안(`ChunkDraft(kind="text")`)을 만든다.

책임:
    - 한 PDF 안에서 문서번호·버전이 쪽마다 다르면 적재 오류로 본다.
    - 절은 목차를 기준으로 나누고, 최대 길이를 넘는 조각만 문단·문장 경계에서 자른다.

의존: `parser.types`, `models`만 import한다(의존 규칙 §2.5).
소유: DB 파트(손재호)
"""

from __future__ import annotations

import re
from pathlib import Path

import pdfplumber

from mpilot.db.parser.types import ChunkDraft, Footer, PageText, RawTable, Section, TextLine
from mpilot.models import DependencyError, Equipment, MpilotError, ValidationError

# ---------------------------------------------------------------------------
# 꼬리글 읽기
#
# 꼬리글 한 줄에서 청크 ID에 필요한 `manual_id`·`page`와 메타데이터 `version`을 한 번에 읽는다.
# 이 줄은 값을 읽은 뒤 본문에서 버리므로 청크 본문에는 들어가지 않는다.
# ---------------------------------------------------------------------------

# 꼬리글 형식: `<문서번호> Rev. <버전> · <문서 제목 등> <쪽> / <전체 쪽>`
#   - manual_id: `HMX-`로 시작하고 영문 대문자·숫자·`-`만 쓴다. `:`가 끼어들 수 없어 청크 ID 규칙도 지킨다.
#   - version: `1.1`, `2.1`처럼 점으로 이은 숫자.
#   - 가운데 제목 부분(`.*`)은 매뉴얼마다 달라서 내용을 보지 않는다.
#   - 맨 끝 `<쪽> / <전체 쪽>`이 인쇄 쪽 번호다.
_FOOTER_PATTERN = re.compile(
    r"^(?P<manual_id>HMX-[A-Z0-9-]+)"
    r" Rev\. (?P<version>\d+(?:\.\d+)*)"
    r" .*"
    r" (?P<page>\d+) / (?P<total_pages>\d+)$"
)


def _parse_footer(line: str) -> Footer | None:
    """줄 하나가 꼬리글이면 출처 정보를 읽는다.

    pdfplumber로 뽑은 쪽 텍스트의 맨 아래 줄을 넣는 것을 전제로 한다. 꼬리글이 없는 표지에서는 None이
    나오므로, 호출하는 쪽은 None인 쪽을 표지로 보고 청킹에서 빼면 된다.

    Args:
        line: 검사할 한 줄. 앞뒤 공백은 무시한다.

    Returns:
        Footer | None: 꼬리글 형식이면 읽은 값, 아니면 None.
    """
    match = _FOOTER_PATTERN.match(line.strip())
    if match is None:
        return None
    return Footer(
        manual_id=match["manual_id"],
        version=match["version"],
        page=int(match["page"]),
        total_pages=int(match["total_pages"]),
    )


def _read_manual_identity(footers: list[Footer]) -> tuple[str, str]:
    """한 PDF의 꼬리글들이 같은 매뉴얼을 가리키는지 확인하고 그 문서번호와 버전을 돌려준다.

    쪽마다 문서번호나 Rev가 다르면 어느 값을 청크 ID에 써야 할지 정할 수 없으므로, 추측하지 않고 적재 오류로 낸다.

    Args:
        footers: 한 PDF에서 표지를 뺀 쪽들의 꼬리글. 쪽 순서대로 넣는다.

    Returns:
        tuple[str, str]: `(manual_id, version)`.

    Raises:
        ValidationError: 꼬리글이 하나도 없을 때, 쪽마다 문서번호나 Rev가 다를 때.
    """
    if not footers:
        raise ValidationError("매뉴얼 꼬리글을 찾지 못했습니다")
    # 모든 쪽의 (문서번호, 버전) 조합을 모아 한 가지인지 본다.
    identities = {(footer.manual_id, footer.version) for footer in footers}
    if len(identities) > 1:
        raise ValidationError(f"한 매뉴얼 안에서 꼬리글의 문서번호·버전이 다릅니다: {sorted(identities)}")
    return identities.pop()



# ---------------------------------------------------------------------------
# 쪽별 본문 추출(#79)
#
# 흐름: 폴더 → PDF 목록 → PDF마다 쪽을 돌며 꼬리글로 출처를 읽고, 표 영역·머리글·꼬리글을 뺀 줄로 `PageText`를 만든다.
# 설비는 `PageText`에 담지 않고, 읽어 낸 `manual_id`로 PDF마다 한 번 정한다.
# ---------------------------------------------------------------------------

# 표지를 뺀 모든 쪽 맨 위 머리글. 예: `HYUNDAI MOVEX · AGV (Automated Guided Vehicle) — DUMMY DATA`
_HEADER_PATTERN = re.compile(r"^HYUNDAI MOVEX · .+ — DUMMY DATA$")

# 글자를 한 줄로 묶는 세로 위치 허용 오차(pt). pdfplumber 기본값 3pt로는 다른 글꼴로 찍힌 에러 코드(약 3.5pt 아래)가
# 별도 줄로 떨어진다(예: `11.1 … ( 반복)` + `A316`). 일반 줄 간격은 12pt 이상이라 5pt로 넓혀도 다른 줄이 섞이지 않는다.
_LINE_Y_TOLERANCE = 5

# 문서번호 `HMX-<설비 코드>-...`의 설비 코드 → 설비.
_EQUIPMENT_CODES = {
    "AGV": Equipment.AGV,
    "MLD": Equipment.MINI_LOADER,
    "RTV": Equipment.RTV,
    "SRM": Equipment.SRM,
}


def _list_pdf_files(directory: Path) -> list[Path]:
    """폴더 안의 매뉴얼 PDF를 이름순으로 모은다.

    이름순으로 고정해야 같은 폴더에서 늘 같은 순서로 청크가 나온다. 하위 폴더는 보지 않는다.

    Args:
        directory: 매뉴얼 PDF가 들어 있는 폴더.

    Returns:
        list[Path]: 확장자가 `.pdf`(대소문자 무시)인 파일 경로. 파일 이름순이다.

    Raises:
        ValidationError: 폴더가 없거나 PDF가 하나도 없을 때.
    """
    if not directory.is_dir():
        raise ValidationError(f"매뉴얼 폴더가 없습니다: {directory}")
    files = sorted(
        (path for path in directory.iterdir() if path.is_file() and path.suffix.lower() == ".pdf"),
        key=lambda path: path.name,
    )
    if not files:
        raise ValidationError(f"매뉴얼 폴더에 PDF가 없습니다: {directory}")
    return files


def _equipment_from_manual_id(manual_id: str) -> Equipment:
    """문서번호의 설비 코드로 설비를 정한다.

    파일명은 바뀔 수 있어 쓰지 않고, 꼬리글에서 읽은 문서번호만 본다.

    Args:
        manual_id: 꼬리글의 문서번호(예: `HMX-MLD-TSM-001`).

    Returns:
        Equipment: 설비 코드(`AGV`, `MLD`, `RTV`, `SRM`)에 맞는 설비.

    Raises:
        ValidationError: 설비 코드를 모를 때.
    """
    parts = manual_id.split("-")
    code = parts[1] if len(parts) > 1 else ""
    if code not in _EQUIPMENT_CODES:
        raise ValidationError(f"문서번호의 설비 코드를 알 수 없습니다: {manual_id}")
    return _EQUIPMENT_CODES[code]


def _is_char_in_table_area(char: dict, table_boxes: list[tuple[float, float, float, float]]) -> bool:
    """글자 하나가 표 영역 안에 있는지 본다.

    글자 상자의 중심으로 판단한다. 경계에 걸친 글자 때문에 표 테두리 바로 옆 본문 글자가 함께 지워지지 않게 하기 위해서다.

    Args:
        char: pdfplumber의 글자 객체(`page.chars`의 원소, `object_type == "char"`).
            글자 상자 `x0`, `x1`, `top`, `bottom`(pt, 원점은 쪽 왼쪽 위)을 쓴다.
        table_boxes: 같은 쪽 표들의 영역 `RawTable.bbox` 목록. 각각 `(x0, top, x1, bottom)`.

    Returns:
        bool: 글자 중심이 어느 한 표 영역 안(경계 포함)에 있으면 True.
    """
    center_x = (char["x0"] + char["x1"]) / 2
    center_y = (char["top"] + char["bottom"]) / 2
    return any(x0 <= center_x <= x1 and top <= center_y <= bottom for x0, top, x1, bottom in table_boxes)


def _extract_page_texts(pdf_path: Path, tables: list[RawTable]) -> list[PageText]:
    """매뉴얼 PDF 하나를 쪽마다 본문 줄만 담은 `PageText`로 바꾼다.

    - 쪽마다 맨 아래 줄을 꼬리글로 읽어 `manual_id`·`version`·`page`를 채운다.
    - 첫 쪽에 꼬리글이 없으면 표지로 보고 건너뛴다. 첫 쪽이 아닌데 꼬리글이 없으면 적재 오류다.
    - 그 쪽(인쇄 쪽 번호 기준) 표 영역 안의 글자를 지운 뒤 줄을 다시 뽑고, 머리글·꼬리글 줄을 버린다.
    - 끝으로 모든 쪽의 문서번호·버전이 같은지 `_read_manual_identity`로 확인한다.

    Args:
        pdf_path: 매뉴얼 PDF 경로.
        tables: 이 PDF에서 찾은 표. `RawTable.page`는 인쇄 쪽 번호이며, 다른 쪽 표는 무시한다.

    Returns:
        list[PageText]: 표지를 뺀 쪽들. PDF 쪽 순서대로이고, 각 `lines`는 위에서부터 순서대로다.

    Raises:
        ValidationError: 표지가 아닌 쪽에 꼬리글이 없을 때, 꼬리글이 하나도 없을 때,
            쪽마다 문서번호나 Rev가 다를 때.
        DependencyError: PDF를 열거나 읽지 못했을 때.
    """
    try:  # PDF 열기 실패만 따로 잡는다
        pdf = pdfplumber.open(pdf_path)  # PDF 파일을 연다(쪽 내용은 아직 읽지 않음)
    except Exception as error:  # 파일 없음, PDF 형식 아님 등 열기 실패 전부
        raise DependencyError(f"매뉴얼 PDF를 열지 못했습니다: {pdf_path.name}") from error  # 외부 의존 실패로 바꿔 올린다

    page_texts: list[PageText] = []  # 돌려줄 쪽 목록(표지 제외)
    footers: list[Footer] = []  # 쪽마다 읽은 꼬리글. 끝에서 문서번호·버전 일치 확인에 쓴다
    try:  # 쪽을 읽다가 나는 pdfplumber 오류를 잡기 위한 범위
        with pdf:  # 다 읽으면(오류가 나도) PDF 파일을 닫는다
            for index, page in enumerate(pdf.pages):  # PDF 쪽 순서대로 돈다. index 0이 첫 쪽(표지 후보)
                raw_lines = page.extract_text_lines(y_tolerance=_LINE_Y_TOLERANCE)  # 표 영역을 빼기 전, 쪽 전체의 줄(위에서부터 정렬, top 포함)
                footer = _parse_footer(raw_lines[-1]["text"]) if raw_lines else None  # 맨 아래 줄을 꼬리글로 읽는다. 빈 쪽이면 None
                if footer is None:  # 꼬리글이 없는 쪽
                    if index == 0:  # 첫 쪽이면
                        continue  # 표지로 보고 청킹 대상에서 뺀다
                    raise ValidationError(f"{pdf_path.name} {index + 1}번째 쪽에 꼬리글이 없습니다")  # 첫 쪽이 아니면 출처를 정할 수 없어 적재 오류
                footers.append(footer)  # 문서번호·버전 일치 확인용으로 모은다

                table_boxes = [table.bbox for table in tables if table.page == footer.page]  # 이 쪽(인쇄 쪽 번호 기준) 표들의 영역만 고른다
                if table_boxes:  # 이 쪽에 표가 있을 때만 다시 뽑는다
                    # 줄이 아니라 글자 단위로 지워야 표 옆에 나란히 놓인 본문이 살아남는다.
                    # filter에는 글자 말고 선·사각형 등도 들어오는데, 본문 줄에는 영향이 없어 그대로 둔다.
                    page = page.filter(  # 조건이 참인 객체만 남긴 새 쪽을 만든다
                        lambda pdf_object: pdf_object["object_type"] != "char"  # 글자가 아닌 객체는 남긴다
                        or not _is_char_in_table_area(pdf_object, table_boxes)  # 글자는 표 영역 밖일 때만 남긴다
                    )  # filter 호출 끝
                    raw_lines = page.extract_text_lines(y_tolerance=_LINE_Y_TOLERANCE)  # 표 글자를 지운 쪽에서 줄을 다시 뽑는다

                lines = [TextLine(top=line["top"], text=line["text"]) for line in raw_lines]  # pdfplumber 줄 dict를 TextLine으로 바꾼다
                if lines and _parse_footer(lines[-1].text) is not None:  # 맨 아래 줄이 꼬리글이면
                    lines.pop()  # 본문에서 버린다(값은 위에서 이미 읽음)
                if lines and _HEADER_PATTERN.match(lines[0].text.strip()):  # 맨 위 줄이 머리글이면
                    lines.pop(0)  # 본문에서 버린다
                page_texts.append(  # 이 쪽의 결과를 쌓는다
                    PageText(manual_id=footer.manual_id, version=footer.version, page=footer.page, lines=lines)  # 꼬리글 출처 + 본문 줄
                )  # append 호출 끝
    except MpilotError:  # 위에서 낸 ValidationError 등 우리 오류는
        raise  # DependencyError로 바꾸지 않고 그대로 올린다
    except Exception as error:  # 쪽 내용을 읽다가 난 pdfplumber·pdfminer 오류
        raise DependencyError(f"매뉴얼 PDF를 읽지 못했습니다: {pdf_path.name}") from error  # 외부 의존 실패로 바꿔 올린다

    _read_manual_identity(footers)  # 모든 쪽의 문서번호·버전이 같은지 확인한다. 다르거나 꼬리글이 하나도 없으면 ValidationError
    return page_texts  # 표지를 뺀 쪽들을 PDF 쪽 순서대로 돌려준다



# ---------------------------------------------------------------------------
# 절 나누기와 텍스트 초안(#80)
#
# 흐름: `PageText` 목록 → 목차에서 절 제목 목록을 읽는다 → 본문 줄이 절 제목과 같을 때만 나눠 `Section`
#       → 최대 길이를 넘는 조각만 문단, 그다음 문장 경계에서 나눠 `ChunkDraft(kind="text")`
# 번호 목록(`1. VCS에서 …`)은 절 제목(`1. 일반 사항`)과 모양이 같아서 정규식이 아니라 목차를 기준으로 삼는다.
# ---------------------------------------------------------------------------

# 텍스트 청크 최대 길이(글자 수, 줄바꿈 포함). 청킹 규칙의 일부라 동결 대상이다.
# 근거(#80 실측): 표를 뺀 쪽별 절 조각 310개의 중앙값은 120자, p95는 439자이고 800자를 넘는 것은 4개다.
# 고객 안내에 인용할 절차 절(7.4 LOTO 최대 659자, 8.x 최대 465자)이 쪼개지지 않게 정했다.
_MAX_TEXT_CHUNK_CHARS = 800

# 문단 경계로 보는 줄 간격(pt). 같은 문단 안은 약 14pt, 번호 항목 사이는 약 15pt, 문단·절 사이는 21pt 이상이다(4종 실측).
_PARAGRAPH_GAP = 18.0

# 목차 한 줄: `<절 제목> <쪽 번호>`. 예: `7.4 LOTO (Lock-Out / Tag-Out) 절차 19`
_CONTENTS_LINE_PATTERN = re.compile(r"^(?P<title>.+?) (?P<page>\d+)$")

# 문장 끝: 숫자가 아닌 글자 뒤 마침표·물음표·느낌표와 그 뒤 공백. `1.` 같은 번호와 `1.5` 같은 수는 끊지 않는다.
_SENTENCE_END_PATTERN = re.compile(r"(?<=\D[.?!])\s+")


def _read_section_titles(page_texts: list[PageText]) -> tuple[set[str], set[int]]:
    """매뉴얼 목차에서 절 제목을 읽는다.

    첫 줄이 `목차`인 쪽에서 시작하고, 그다음 쪽도 첫 줄이 목차 줄(`<제목> <쪽 번호>`)이면 목차가 이어진 것으로 본다.

    Args:
        page_texts: 한 매뉴얼의 쪽들. `_extract_page_texts`의 결과.

    Returns:
        tuple[set[str], set[int]]: `(쪽 번호를 뗀 절 제목, 목차 쪽의 page_texts 안 위치)`.

    Raises:
        ValidationError: 목차 쪽이 없거나 목차에서 제목을 하나도 읽지 못했을 때.
    """
    start = next((i for i, page_text in enumerate(page_texts) if _first_line(page_text) == "목차"), None)
    if start is None:
        raise ValidationError("매뉴얼 목차를 찾지 못했습니다")
    contents_pages = {start}
    for index in range(start + 1, len(page_texts)):
        if not _CONTENTS_LINE_PATTERN.match(_first_line(page_texts[index])):
            break  # 첫 줄이 목차 줄이 아니면 본문이 시작된 것이다
        contents_pages.add(index)
    titles = {
        match["title"]
        for index in contents_pages
        for line in page_texts[index].lines
        if (match := _CONTENTS_LINE_PATTERN.match(line.text.strip()))
    }
    if not titles:
        raise ValidationError("매뉴얼 목차에서 절 제목을 읽지 못했습니다")
    return titles, contents_pages


def _first_line(page_text: PageText) -> str:
    """쪽의 첫 줄을 앞뒤 공백을 떼어 돌려준다. 줄이 없으면 빈 문자열."""
    return page_text.lines[0].text.strip() if page_text.lines else ""


def _make_section(page: int, title: str | None, lines: list[TextLine]) -> list[Section]:
    """한 쪽에서 모은 줄로 `Section`을 만든다.

    Args:
        page: 줄이 있는 쪽.
        title: 줄이 속한 절 제목. 첫 절 앞이면 None.
        lines: 위에서부터 모은 줄. 절이 이 쪽에서 시작했으면 첫 줄이 제목 줄이다.

    Returns:
        list[Section]: 조각 하나. 줄이 없거나 제목 줄뿐이면 빈 목록.
    """
    body = lines[1:] if lines and lines[0].text.strip() == title else lines
    if not body:
        return []
    return [Section(page=page, top=lines[0].top, title=title, text=_join_lines(lines))]


def _join_lines(lines: list[TextLine]) -> str:
    """줄을 이어 본문을 만든다. 같은 문단은 줄바꿈 하나로, 줄 간격이 `_PARAGRAPH_GAP`보다 넓은 곳은 빈 줄로 잇는다.

    Args:
        lines: 위에서부터 순서대로 놓인 줄. 하나 이상이어야 한다.

    Returns:
        str: 이은 본문.
    """
    text = lines[0].text
    for previous, line in zip(lines, lines[1:]):
        text += ("\n\n" if line.top - previous.top > _PARAGRAPH_GAP else "\n") + line.text
    return text


def _split_sections(page_texts: list[PageText]) -> list[Section]:
    """매뉴얼 하나의 쪽들을 목차의 절 제목 기준으로 나눈다.

    - 본문 줄이 목차의 절 제목과 같을 때만 새 절을 시작한다. 번호 목록 줄은 목차에 없으므로 절을 나누지 않는다.
    - 제목 줄은 조각 본문의 첫 줄로 남긴다.
    - 절이 쪽을 넘어가면 쪽마다 `Section`을 만들고 같은 `title`을 붙인다. 첫 절 제목보다 앞의 본문은 `title=None`이다.
    - 목차 쪽은 결과에서 뺀다. 제목 줄뿐인 조각(장 제목 바로 뒤에 절 제목이 오는 경우 등)은 만들지 않는다.

    Args:
        page_texts: 한 매뉴얼의 쪽들. `_extract_page_texts`의 결과이며 쪽 순서대로다.

    Returns:
        list[Section]: 쪽 순서, 쪽 안에서는 위에서부터의 순서. 문단 경계는 빈 줄로 남는다(`_join_lines`).

    Raises:
        ValidationError: 목차를 찾지 못했을 때.
    """
    titles, contents_pages = _read_section_titles(page_texts)
    sections: list[Section] = []
    title: str | None = None  # 지금 읽고 있는 절. 쪽을 넘어가도 이어진다
    for index, page_text in enumerate(page_texts):
        if index in contents_pages:
            continue
        lines: list[TextLine] = []  # 이 쪽에서 지금 절에 속한 줄
        for line in page_text.lines:
            if line.text.strip() in titles:  # 새 절이 시작되면 앞 절 조각을 닫는다
                sections += _make_section(page_text.page, title, lines)
                title, lines = line.text.strip(), []
            lines.append(line)
        sections += _make_section(page_text.page, title, lines)
    return sections


def _pack(parts: list[str], separator: str) -> list[str]:
    """조각을 앞에서부터 이어 붙이되, 하나가 최대 길이(`_MAX_TEXT_CHUNK_CHARS`)를 넘지 않게 묶는다.

    Args:
        parts: 순서대로 놓인 조각.
        separator: 조각 사이에 넣을 구분자.

    Returns:
        list[str]: 묶은 조각. 최대 길이보다 긴 조각은 그대로 하나가 된다.
    """
    chunks: list[str] = []
    for part in parts:
        if chunks and len(chunks[-1]) + len(separator) + len(part) <= _MAX_TEXT_CHUNK_CHARS:
            chunks[-1] += separator + part
        else:
            chunks.append(part)
    return chunks


def _split_text(text: str) -> list[str]:
    """본문을 최대 길이(`_MAX_TEXT_CHUNK_CHARS`) 이하 조각으로 나눈다.

    최대 길이 이하면 그대로 둔다. 넘으면 문단(빈 줄) 경계로 나누고, 그래도 넘는 문단은 문장 경계로,
    그래도 넘는 문장은 글자 수로 자른 뒤 최대 길이 안에서 다시 묶는다.

    Args:
        text: 절 조각 본문.

    Returns:
        list[str]: 앞뒤 공백을 뗀 조각. 각각 최대 길이 이하이며 빈 조각은 없다. 본문이 비면 빈 목록.
    """
    text = text.strip()
    if len(text) <= _MAX_TEXT_CHUNK_CHARS:
        return [text] if text else []
    paragraphs: list[str] = []
    for paragraph in filter(str.strip, text.split("\n\n")):
        if len(paragraph) <= _MAX_TEXT_CHUNK_CHARS:
            paragraphs.append(paragraph)
            continue
        pieces = [
            sentence[start:start + _MAX_TEXT_CHUNK_CHARS]
            for sentence in _SENTENCE_END_PATTERN.split(paragraph)
            for start in range(0, len(sentence), _MAX_TEXT_CHUNK_CHARS)
        ]
        paragraphs += _pack(pieces, " ")
    return [chunk.strip() for chunk in _pack(paragraphs, "\n\n")]


def _to_text_drafts(sections: list[Section]) -> list[ChunkDraft]:
    """절 조각을 텍스트 청크 초안으로 바꾼다.

    청크 최대 길이는 800자(`_MAX_TEXT_CHUNK_CHARS`, 줄바꿈 포함)이며 동결 대상이다.
    최대 길이를 넘는 조각만 문단, 그다음 문장 경계에서 나눈다(`_split_text`).
    한 조각에서 나온 초안은 모두 조각의 `page`·`top`·`title`을 받는다. 같은 위치의 초안끼리는
    목록 순서가 유지되므로 번호를 매길 때도 본문 순서가 지켜진다.

    Args:
        sections: `_split_sections`의 결과.

    Returns:
        list[ChunkDraft]: `kind="text"` 초안. 입력 순서대로이고 빈 본문 초안은 없다.
    """
    return [
        ChunkDraft(page=section.page, top=section.top, kind="text", section=section.title, text=text)
        for section in sections
        for text in _split_text(section.text)
    ]
