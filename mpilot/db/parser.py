"""매뉴얼 PDF 파싱·청킹: PDF를 출처가 붙은 검색 단위로 나눈다.

역할:
    설비 매뉴얼 PDF(pdfplumber)를 읽어, 쪽·절 출처가 붙은 `Chunk` 목록으로 분해한다.
    이 청크가 `manual` 검색 컬렉션과 고객 안내 인용의 원문이 된다.

책임:
    - 같은 PDF에서는 항상 같은 청크·같은 `chunk_no`를 낸다(재현성).
    - 청크 ID 구성 요소(`manual_id`, `page`, `chunk_no`)를 확정한다.
      ID 형식 `manual:<manual_id>:<page>:<chunk_no>`와 청킹 규칙은 1주차 초에 확정·동결한다.
      사람이 `customer_procedure_ref_ids`를 쓸 때 이 ID를 쓰므로 동결 뒤에는 바꾸지 않는다.
    - 구성 요소에 `:`가 들어 있으면 적재 오류로 본다.
    - DB·벡터 저장소에는 접근하지 않는다. 색인 생성은 `vectordb`가 맡는다.

청크 ID 구성 요소의 출처(#43에서 정함):
    매뉴얼의 표지를 뺀 모든 쪽 우측 하단 꼬리글은 아래 형식이다.

        HMX-AGV-TSM-001 Rev. 1.1 · AGV 기술 사양서 및 운영·정비 매뉴얼 … 3 / 44

    - `page`: 꼬리글에 인쇄된 쪽 번호(`3 / 44`의 3). 사람이 매뉴얼을 펼쳐 확인하는 번호와 같다.
      PDF 안의 순서보다 1 작다(표지가 번호 없이 맨 앞에 있기 때문). 표지는 청킹하지 않는다.
      에러코드 표 CSV의 `page`도 같은 기준을 쓴다.
    - `manual_id`: 꼬리글의 문서번호(`HMX-AGV-TSM-001`). 파일명은 바뀔 수 있어 쓰지 않는다.
      에러코드 표 CSV의 `manual_id`도 같은 값을 쓴다.
    - `version`: 꼬리글의 `Rev.` 뒤 숫자만 쓴다(`"1.1"`). 화면에 `Rev.`를 붙일지는 보여 주는 쪽이 정한다.
    - 꼬리글은 위 값을 읽은 뒤 본문에서 버린다. 청크 본문에는 들어가지 않는다.
    - 한 PDF 안에서 문서번호나 Rev가 쪽마다 다르면 적재 오류로 본다.

의존: `models`만 import한다(의존 규칙 §2.5).
호출: `service.prepare_search_index`가 앱 시작 때 부른다.
소유: DB 파트
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import pdfplumber
from pydantic import BaseModel, ConfigDict, Field, field_validator

from mpilot.models import Chunk, DependencyError, Equipment, MpilotError, ValidationError


def parse_manuals(directory: Path) -> list[Chunk]:
    """매뉴얼 PDF를 출처가 붙은 검색 단위로 분해한다.

    Args:
        directory: 매뉴얼 PDF(설비 4종)가 들어 있는 폴더. `AppConfig.manuals_dir`의 값.

    Returns:
        list[Chunk]: 쪽·절 출처(`manual_id`, `page`, `section`, `chunk_no`, `version`)가 붙은 청크 목록.
            같은 PDF를 넣으면 순서와 ID 구성이 항상 같다.

    Raises:
        ValidationError: 폴더에 PDF가 없거나 설비를 알 수 없을 때, ID 구성 요소에 `:`가 있을 때.
        DependencyError: PDF를 읽거나 파싱하는 데 실패했을 때.
    """
    ...


# ---------------------------------------------------------------------------
# parser 안에서 주고받는 중간 자료
#
# `RawTable`은 표 추출(담당: 이재서)이 만들고, `extract_page_texts`(담당: 손재호)가 표 영역을 빼는 데 쓴다.
# `ChunkDraft`는 텍스트·표·OCR 초안의 공통 형식이며 `number_chunks`(담당: 손재호)가 받아 번호를 매긴다.
# 표·OCR 초안은 절을 알 수 없어 `section=None`으로 두고, 바로 앞 텍스트 초안의 절을 이어받는 일은
# `number_chunks`가 맡는다.
# ---------------------------------------------------------------------------


class RawTable(BaseModel):
    """PDF 한 쪽에서 찾은 표 하나.

    Attributes:
        page: 표가 있는 쪽. 매뉴얼 우측 하단 꼬리글에 인쇄된 쪽 번호이며(`1 / 35`의 1), 표지는 번호가 없어 대상이 아니다.
        index: 그 쪽 안에서 위에서부터의 순서. 1부터 센다.
        bbox: 표 영역 `(x0, top, x1, bottom)`. 단위는 pt이고 원점은 쪽 왼쪽 위다.
            `x0 <= x1`, `top <= bottom`이어야 하며 어긋나면 만들 때 오류가 난다.
        caption: 표 캡션(예: `표 9-4. 에러 코드 일람`). 앞 쪽에서 이어진 표는 앞 표의 캡션을 이어받는다.
            캡션이 없으면 None.
        rows: 행·열 셀 값. None은 병합되었거나 비어 있는 셀이다. 첫 행은 머리글이다.
        continued: 앞 쪽 마지막 표가 이 쪽으로 이어진 것이면 True.
    """

    model_config = ConfigDict(frozen=True)

    page: int = Field(ge=1)
    index: int = Field(ge=1)
    bbox: tuple[float, float, float, float]
    caption: str | None
    rows: list[list[str | None]]
    continued: bool

    @field_validator("bbox")
    @classmethod
    def _check_bbox_order(cls, bbox: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        """표 영역의 좌표가 뒤집히지 않았는지 검사한다.

        Args:
            bbox: `(x0, top, x1, bottom)`.

        Returns:
            tuple[float, float, float, float]: 검사를 통과한 같은 값.

        Raises:
            ValueError: `x0 > x1`이거나 `top > bottom`일 때(pydantic이 검증 오류로 바꾼다).
        """
        x0, top, x1, bottom = bbox
        if x0 > x1 or top > bottom:
            raise ValueError("bbox는 x0 <= x1, top <= bottom이어야 합니다")
        return bbox


class ChunkDraft(BaseModel):
    """번호를 매기기 전의 청크 초안.

    Attributes:
        page: 출처 쪽. 매뉴얼 우측 하단 꼬리글에 인쇄된 쪽 번호이며(`1 / 35`의 1), 표지는 번호가 없어 대상이 아니다.
        top: 쪽 안에서의 세로 위치(pt). 같은 쪽 초안을 위에서부터 정렬하는 데 쓴다.
            같은 쪽·같은 위치면 목록 순서를 유지한다.
        kind: 초안 종류. 본문 `text`, 표 `table`, 이미지 전사 `ocr`.
        section: 출처 절. 알 수 없으면 None.
        text: 초안 본문.
    """

    model_config = ConfigDict(frozen=True) 

    page: int = Field(ge=1)
    top: float
    kind: Literal["text", "table", "ocr"]
    section: str | None
    text: str


# ---------------------------------------------------------------------------
# 텍스트 경로(담당: 손재호)의 중간 자료
#
# 흐름: PDF 한 쪽 → `PageText`(꼬리글·머리글을 뺀 본문 줄) → `Section`(절 제목으로 자른 조각) → `ChunkDraft`
# 표 영역은 `RawTable.bbox`로 `PageText`를 만들 때 이미 빠져 있다.
# ---------------------------------------------------------------------------


class TextLine(BaseModel):
    """쪽 안의 본문 한 줄.

    Attributes:
        top: 줄의 세로 위치(pt). 원점은 쪽 왼쪽 위라서 값이 작을수록 위쪽이다.
            절을 나눌 때와 `ChunkDraft.top`을 정할 때 쓴다.
        text: 줄 내용.
    """

    model_config = ConfigDict(frozen=True)
    # - model_config: pydantic 모델의 동작 설정을 넣는 자리입니다.
    # - ConfigDict(frozen=True): 그 설정 중 "읽기 전용으로 만들기"를 켭니다.


    top: float
    text: str


class PageText(BaseModel):
    """꼬리글에서 출처를 읽고 본문만 남긴 쪽 하나.

    `manual_id`·`version`은 쪽마다 꼬리글에서 읽은 값이다. 한 PDF의 모든 쪽이 같은 값인지는
    `_read_manual_identity`가 확인한다.

    Attributes:
        manual_id: 꼬리글의 문서번호(예: `HMX-AGV-TSM-001`).
        version: 꼬리글의 `Rev.` 뒤 숫자(예: `1.1`).
        page: 꼬리글에 인쇄된 쪽 번호(`3 / 44`의 3).
        lines: 머리글·꼬리글·표 영역을 뺀 본문 줄. 위에서부터 순서대로 놓인다.
    """

    model_config = ConfigDict(frozen=True)

    manual_id: str
    version: str
    page: int = Field(ge=1)
    lines: list[TextLine]


class Section(BaseModel):
    """한 쪽 안에서 절 하나에 속한 본문 조각.

    청크 ID가 쪽 단위(`<page>:<chunk_no>`)라서, 절이 두 쪽에 걸치면 쪽마다 따로 `Section`을 만든다.
    두 조각은 같은 `title`을 가진다.

    Attributes:
        page: 조각이 있는 쪽. `PageText.page`와 같은 인쇄 쪽 번호다.
        top: 조각 첫 줄의 세로 위치(pt). `ChunkDraft.top`으로 그대로 넘어가 표·OCR 초안과 순서를 맞춘다.
        title: 절 제목(예: `8.1 운전 모드 전환`). 문서 첫 절 제목보다 앞에 있는 본문이면 None.
        text: 조각 본문. 줄을 줄바꿈으로 이은 것이다.
    """

    model_config = ConfigDict(frozen=True)

    page: int = Field(ge=1)
    top: float
    title: str | None
    text: str


# ---------------------------------------------------------------------------
# 꼬리글 읽기
#
# 꼬리글 한 줄에서 청크 ID에 필요한 `manual_id`·`page`와 메타데이터 `version`을 한 번에 읽는다.
# 이 줄은 값을 읽은 뒤 본문에서 버리므로 청크 본문에는 들어가지 않는다.
# ---------------------------------------------------------------------------


class Footer(BaseModel):
    """꼬리글 한 줄에서 읽은 출처 정보.

    Attributes:
        manual_id: 문서번호(예: `HMX-AGV-TSM-001`).
        version: `Rev.` 뒤 숫자(예: `1.1`).
        page: 인쇄된 쪽 번호(`3 / 44`의 3).
        total_pages: 인쇄된 전체 쪽 수(`3 / 44`의 44). 표지는 세지 않는다.
    """

    model_config = ConfigDict(frozen=True)

    manual_id: str
    version: str
    page: int = Field(ge=1)
    total_pages: int = Field(ge=1)


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
