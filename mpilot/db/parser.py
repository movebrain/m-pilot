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
    - 이미지 페이지 OCR(`set_vision_model` 등)은 현재 매뉴얼 4종에 이미지 페이지가 없어 파일 끝에 주석으로 보류해 두었다.

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

# import base64  # OCR 보류(파일 끝 주석 참고)
# import io  # OCR 보류
import re
from pathlib import Path
from typing import Literal

import pdfplumber
# from langchain_core.language_models import BaseChatModel  # OCR 보류
# from langchain_core.messages import HumanMessage  # OCR 보류
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic import ValidationError as PydanticValidationError

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
# `ChunkDraft`는 텍스트·표·OCR 초안의 공통 형식이며 `_number_chunks`(담당: 손재호)가 받아 번호를 매긴다.
# 표·OCR 초안은 절을 알 수 없어 `section=None`으로 두고, 바로 앞 텍스트 초안의 절을 이어받는 일은
# `_number_chunks`가 맡는다.
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
        text: 조각 본문. 줄을 줄바꿈으로 이은 것이며, 문단 경계는 빈 줄(줄바꿈 두 개)이다.
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


# ---------------------------------------------------------------------------
# 청크 번호 매기기(#81)
#
# 흐름: 텍스트·표·OCR 초안 → `(page, top)` 순 정렬 → 비어 있는 절 채우기 → 쪽마다 `chunk_no` → `Chunk`
# 청크 ID `manual:<manual_id>:<page>:<chunk_no>`가 여기서 정해지므로, 같은 초안이면 늘 같은 번호가 나와야 한다.
# ---------------------------------------------------------------------------


def _fill_section(draft: ChunkDraft, previous: str | None, drafts: list[ChunkDraft]) -> str:
    """초안의 절 이름을 정한다. 초안에 절이 있으면 그대로 쓰고, 없으면 아래 순서로 채운다.

    1. 바로 앞 초안의 절(표·OCR 초안은 앞 텍스트의 절을 이어받는다)
    2. 앞 초안이 없으면(매뉴얼 맨 앞) 같은 쪽 첫 표의 캡션
    3. 그것도 없으면 초안 본문의 첫 줄

    Args:
        draft: 절을 정할 초안.
        previous: 정렬 순서상 바로 앞 초안의 (채운) 절. 매뉴얼의 첫 초안이면 None.
        drafts: 매뉴얼의 전체 초안. 위치순으로 정렬된 것이며, 같은 쪽 표 캡션을 찾는 데 쓴다.

    Returns:
        str: 절 이름. 앞뒤 공백을 뗐다.

    Raises:
        ValidationError: 위 순서로도 절 이름을 정할 수 없을 때(본문이 비어 있을 때).
    """
    if draft.section is not None:  # 텍스트 초안처럼 절을 이미 알면
        return draft.section  # 그대로 쓴다
    if previous is not None:  # 앞 초안이 있으면(표·OCR 초안 대부분)
        return previous  # 앞 초안의 절을 이어받는다. 쪽을 넘어가도 이어진다
    for page_draft in (other for other in drafts if other.page == draft.page):  # 매뉴얼 맨 앞이면 같은 쪽 초안을 위에서부터 본다
        caption = page_draft.text.strip().split("\n")[0].strip()  # 표 초안의 첫 줄은 캡션이다
        if page_draft.kind == "table" and caption and not caption.startswith("|"):  # 캡션 없는 표는 첫 줄이 머리글 행(`| … |`)이라 건너뛴다
            return caption  # 같은 쪽 첫 표의 캡션을 절 이름으로 쓴다
    first_line = draft.text.strip().split("\n")[0].strip()  # 캡션도 없으면 초안 본문의 첫 줄을 쓴다
    if not first_line:  # 본문까지 비어 있으면
        raise ValidationError(f"{draft.page}쪽 초안의 절을 정할 수 없습니다")  # 추측하지 않고 적재 오류로 낸다
    return first_line  # 초안 본문의 첫 줄


def _number_chunks(drafts: list[ChunkDraft], manual_id: str, version: str, equipment: Equipment) -> list[Chunk]:
    """매뉴얼 하나의 초안을 위치순으로 정렬하고 쪽마다 번호를 매겨 `Chunk`로 만든다.

    - `(page, top)` 순으로 정렬한다. 같은 쪽·같은 위치면 입력 순서를 유지한다(안정 정렬).
    - `chunk_no`는 쪽마다 1부터 매긴다.
    - `section`이 None인 초안은 `_fill_section` 규칙으로 채운다.

    Args:
        drafts: 한 매뉴얼의 텍스트·표·OCR 초안. 순서는 상관없다.
        manual_id: 꼬리글의 문서번호. 청크 ID 구성 요소다.
        version: 꼬리글의 `Rev.` 뒤 숫자.
        equipment: 매뉴얼의 설비.

    Returns:
        list[Chunk]: 쪽 순서, 쪽 안에서는 위에서부터의 순서. 같은 초안을 넣으면 늘 같은 결과다.

    Raises:
        ValidationError: 청크 ID 구성 요소(`manual_id`)에 `:`가 있을 때, 절을 정할 수 없을 때,
            `Chunk` 값 검증에 실패했을 때.
    """
    if ":" in manual_id:  # 청크 ID `manual:<manual_id>:<page>:<chunk_no>`의 구분자가 끼어 있으면
        raise ValidationError(f"청크 ID 구성 요소에 ':'가 있습니다: {manual_id}")  # ID를 나눌 수 없어 적재 오류로 낸다(쪽·번호는 정수라 검사하지 않음)
    ordered = sorted(drafts, key=lambda draft: (draft.page, draft.top))  # 쪽 순서, 쪽 안에서는 위에서부터. 같은 위치면 입력 순서 유지(안정 정렬)
    chunks: list[Chunk] = []  # 돌려줄 청크
    previous: str | None = None  # 바로 앞 초안의 (채운) 절. 첫 초안이면 None
    for draft in ordered:  # 위치순으로 돈다
        section = _fill_section(draft, previous, ordered)  # 절이 비어 있으면 앞 초안 → 같은 쪽 표 캡션 → 본문 첫 줄 순으로 채운다
        chunk_no = 1 if not chunks or chunks[-1].page != draft.page else chunks[-1].chunk_no + 1  # 쪽이 바뀌면 1부터, 같은 쪽이면 앞 번호 + 1
        try:  # Chunk 값 검증 오류만 따로 잡는다
            chunk = Chunk(  # 초안에 매뉴얼 출처를 붙여 청크를 만든다
                manual_id=manual_id,
                equipment=equipment,
                page=draft.page,
                section=section,
                chunk_no=chunk_no,
                version=version,
                text=draft.text,
            )  # Chunk 생성 끝
        except PydanticValidationError as error:  # 설비 값이 잘못되는 등 pydantic 검증 실패면
            raise ValidationError(f"{manual_id} {draft.page}쪽 청크 값이 올바르지 않습니다: {error}") from error  # 우리 오류로 바꿔 올린다
        chunks.append(chunk)  # 위치순 그대로 쌓는다
        previous = section  # 다음 초안이 이어받을 절
    return chunks  # 쪽 순서, 쪽 안에서는 위에서부터의 순서
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


# ---------------------------------------------------------------------------
# [보류] 이미지 페이지·OCR(담당: 이재서)
#
# 현재 매뉴얼 4종에는 이미지 페이지가 없어 OCR이 실행되지 않으므로 주석으로 막아 두었다(#103 리뷰).
# 다시 쓸 때: 아래 줄 맨 앞의 `# `를 지우고, 파일 위쪽 import 4줄(base64, io, BaseChatModel, HumanMessage)과
# tests/test_parser_tables_ocr.py의 OCR 테스트 주석도 함께 푼다.
# ---------------------------------------------------------------------------

# # ---------------------------------------------------------------------------
# # 이미지 페이지·OCR(담당: 이재서)
# #
# # 흐름: `_find_image_pages`(글자가 없거나 큰 그림이 있는 쪽) → `_ocr_page`(300dpi 캡처 → 비전 모델 전사)
# #       → `_ocr_to_drafts`(문단 묶기) → `ChunkDraft`
# # 캡처·base64·HumanMessage(image_url) 방식은 수업(Day11 멀티모달) 코드를 따른다.
# # ---------------------------------------------------------------------------
#
# _IMAGE_AREA_RATIO = 0.25
# """쪽 면적에서 이미지가 차지하는 비율이 이 값 이상이면 이미지 페이지로 본다."""
#
# # 프롬프트는 수업(Day11 멀티모달·소득세법 워크북)의 'OCR 작업' 문장을 따른다. 전문 용어가 많으면 모델이 답을
# # 거부할 수 있어 '분석'이 아니라 '전사'임을 밝히고, 거부하면 영어 프롬프트로 한 번 다시 시도한다.
# _OCR_PROMPT = (
#     "다음 이미지는 설비 매뉴얼의 한 페이지입니다. OCR 작업입니다: "
#     "이미지에 적힌 글자·숫자를 보이는 그대로 옮기고, 표는 각 행과 열을 마크다운 표로 옮기세요. "
#     "해석·요약하지 말고 그대로 전사만 하세요. 글자가 없으면 아무것도 출력하지 마세요."
# )
# _OCR_RETRY_PROMPT = (
#     "Transcribe all text in this image exactly as written. Put any table into a markdown table. "
#     "Copy text and numbers exactly. Plain OCR task."
# )
# # 수업 코드처럼 답에 이 문구가 들어 있으면 거부로 본다(소문자로 바꿔 비교한다).
# _REFUSAL_MARKERS = ("can't assist", "cannot assist", "i'm sorry", "i am sorry")
# _OCR_DPI = 300
# """캡처 해상도. 표의 작은 숫자도 또렷하게 읽히도록 수업에서 검증한 값을 쓴다."""
#
# # `set_vision_model`이 넣어 주는 비전 모델. 아직 설정 전이면 None.
# _vision_model: BaseChatModel | None = None
#
#
# def set_vision_model(vision_model: BaseChatModel) -> None:
#     """이미지 페이지 전사(`_ocr_page`)에 쓸 비전 모델을 설정한다.
#
#     Args:
#         vision_model: 이미지를 입력받는 LangChain 채팅 모델(예: gpt-4o). `app`이 설정(API 키, 제한 시간)으로
#             만들어 넘긴다. temperature=0을 권장한다.
#
#     Returns:
#         None: 모듈 안에 모델을 보관한다. 이후 `_ocr_page`는 이 모델을 쓴다.
#     """
#     global _vision_model
#     _vision_model = vision_model
#
#
# def _image_area(image: dict) -> float:
#     """pdfplumber가 알려 준 이미지 하나의 면적을 구한다.
#
#     Args:
#         image: `page.images`의 한 항목(`x0`, `x1`, `top`, `bottom`).
#
#     Returns:
#         float: 면적(pt²). 좌표가 뒤집혀 폭이나 높이가 음수면 그 값을 0으로 본다.
#     """
#     width = float(image["x1"]) - float(image["x0"])
#     height = float(image["bottom"]) - float(image["top"])
#     if width < 0:
#         width = 0.0
#     if height < 0:
#         height = 0.0
#     return width * height
#
#
# def _is_image_page(text: str, page_area: float, image_areas: list[float]) -> bool:
#     """한 쪽이 이미지 페이지인지 판정한다(순수 함수).
#
#     Args:
#         text: 그 쪽에서 추출한 글자.
#         page_area: 쪽 면적(pt²).
#         image_areas: 그 쪽 이미지들의 면적(pt²).
#
#     Returns:
#         bool: 글자가 없거나, 이미지 면적 합이 쪽 면적의 `_IMAGE_AREA_RATIO` 이상이면 True.
#     """
#     # 1) 글자가 하나도 없으면 스캔본 같은 이미지 페이지다.
#     if not text.strip():
#         return True
#     # 2) 쪽 크기를 알 수 없으면 비율을 구할 수 없다.
#     if page_area <= 0:
#         return False
#     # 3) 이미지가 쪽의 25% 이상을 차지하면 이미지 페이지다.
#     return sum(image_areas) / page_area >= _IMAGE_AREA_RATIO
#
#
# def _find_image_pages(path: Path) -> list[int]:
#     """텍스트가 없거나 큰 이미지가 있는 페이지를 찾는다.
#
#     표지(PDF 첫 쪽)는 청킹 대상이 아니라 찾지 않는다.
#
#     Args:
#         path: 매뉴얼 PDF 파일 경로.
#
#     Returns:
#         list[int]: OCR이 필요한 쪽 번호 목록. 꼬리글의 인쇄 쪽 번호이고 오름차순이다.
#             꼬리글이 이미지라 읽히지 않는 쪽은 PDF 쪽 순서 - 1로 센다.
#
#     Raises:
#         DependencyError: PDF를 읽는 데 실패했을 때.
#     """
#     pages: list[int] = []
#     with _open_pdf(path) as pdf:
#         try:
#             # 첫 쪽(표지)은 청킹 대상이 아니라 둘째 쪽부터 본다.
#             for page in pdf.pages[1:]:
#                 # 1) 쪽 안 이미지들의 면적을 모은다.
#                 image_areas = []
#                 for image in page.images:
#                     image_areas.append(_image_area(image))
#
#                 # 2) 글자와 면적으로 이미지 페이지인지 판정한다.
#                 text = page.extract_text() or ""
#                 page_area = float(page.width) * float(page.height)
#                 if _is_image_page(text, page_area, image_areas):
#                     # 3) 이미지 페이지면 인쇄 쪽 번호를 기록한다.
#                     pages.append(_printed_page_number(page.extract_text_lines(), page.page_number))
#         except Exception as e:  # pdfminer·pdfplumber 오류 종류가 많아 한곳에서 의존 오류로 바꾼다.
#             raise DependencyError(f"PDF 쪽을 읽지 못했습니다: {Path(path).name}({type(e).__name__})") from None
#     return pages
#
#
# def _ocr_page(path: Path, page_no: int) -> str:
#     """페이지를 300dpi로 캡처해 비전 모델로 전사한다.
#
#     전사는 '보이는 그대로'이며 해석·요약하지 않는다. 표는 마크다운 표로 옮긴다.
#     모델이 거부하면 영어 프롬프트로 한 번 다시 시도한다(수업 방식).
#     모델 출력이라 같은 쪽이라도 실행마다 글자가 조금 달라질 수 있다.
#
#     Args:
#         path: 매뉴얼 PDF 파일 경로.
#         page_no: 전사할 쪽 번호. 꼬리글의 인쇄 쪽 번호이고 1부터 센다(표지는 대상이 아니다).
#             PDF 쪽 순서로는 `page_no + 1`번째 쪽이다.
#
#     Returns:
#         str: 전사한 글자. 앞뒤 공백을 지운다. 글자가 없는 쪽이면 빈 문자열.
#
#     Raises:
#         ValidationError: `page_no`가 표지를 뺀 쪽 범위를 벗어날 때.
#         DependencyError: 비전 모델이 설정되지 않았거나, PDF 캡처·모델 호출이 실패하거나 시간이 초과될 때,
#             다시 시도해도 모델이 전사를 거부할 때.
#     """
#     # 1) 비전 모델이 없으면 PDF를 열기 전에 알린다.
#     if _vision_model is None:
#         raise DependencyError("비전 모델이 설정되지 않았습니다(set_vision_model을 먼저 호출해야 합니다).")
#
#     with _open_pdf(path) as pdf:
#         # 2) 쪽 번호가 표지를 뺀 범위(1 ~ 전체 쪽 수 - 1) 안인지 확인한다.
#         last_page_no = len(pdf.pages) - 1
#         if page_no < 1 or page_no > last_page_no:
#             raise ValidationError(f"쪽 번호가 범위를 벗어났습니다: {page_no} (1~{last_page_no}, 표지 제외)")
#
#         # 3) 쪽을 300dpi 이미지로 캡처해 PNG로 메모리에 저장한다.
#         #    표지가 0번이라 인쇄 쪽 번호가 곧 `pdf.pages`의 인덱스다.
#         try:
#             image = pdf.pages[page_no].to_image(resolution=_OCR_DPI)
#             buffer = io.BytesIO()
#             image.original.save(buffer, format="PNG")
#         except Exception as e:
#             raise DependencyError(f"쪽을 이미지로 캡처하지 못했습니다: p.{page_no}({type(e).__name__})") from None
#
#     # 4) 모델에 보낼 수 있게 base64 글자로 바꾼다.
#     encoded_png = base64.b64encode(buffer.getvalue()).decode("ascii")
#
#     # 5) 한국어 프롬프트로 전사한다.
#     text = _transcribe(_OCR_PROMPT, encoded_png, page_no)
#     if not _is_refusal(text):
#         return text
#
#     # 6) 거부하면 영어 프롬프트로 한 번 더 시도한다.
#     text = _transcribe(_OCR_RETRY_PROMPT, encoded_png, page_no)
#     if _is_refusal(text):
#         # 거부 문구가 청크로 저장되면 검색 근거에 엉뚱한 글이 섞이므로 저장하지 않고 알린다.
#         raise DependencyError(f"비전 모델이 전사를 거부했습니다: p.{page_no}")
#     return text
#
#
# def _transcribe(prompt: str, encoded_png: str, page_no: int) -> str:
#     """이미지 한 장을 비전 모델로 전사한다.
#
#     Args:
#         prompt: 전사 지시문.
#         encoded_png: base64로 인코딩한 PNG.
#         page_no: 오류 문구에 쓸 쪽 번호.
#
#     Returns:
#         str: 모델이 돌려준 글자. 앞뒤 공백을 지운다.
#
#     Raises:
#         DependencyError: 모델 호출이 실패하거나 시간이 초과될 때.
#     """
#     # 1) 글자 지시문과 이미지를 한 메시지에 담는다(수업 코드와 같은 형식).
#     message = HumanMessage(
#         content=[
#             {"type": "text", "text": prompt},
#             {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded_png}", "detail": "high"}},
#         ]
#     )
#
#     # 2) 모델을 부른다.
#     try:
#         response = _vision_model.invoke([message])
#     except Exception as e:  # OpenAI 오류·시간 초과 종류가 많아 한곳에서 의존 오류로 바꾼다.
#         raise DependencyError(f"비전 모델 호출에 실패했습니다: p.{page_no}({type(e).__name__})") from None
#
#     # 3) 일부 모델은 내용을 조각 목록으로 돌려주므로 글자만 이어 붙인다.
#     content = response.content
#     if isinstance(content, list):
#         pieces = []
#         for part in content:
#             if isinstance(part, dict):
#                 pieces.append(part.get("text", ""))
#             else:
#                 pieces.append(str(part))
#         content = "".join(pieces)
#     return str(content).strip()
#
#
# def _is_refusal(text: str) -> bool:
#     """모델 답이 전사 거부 문구인지 판정한다(수업 코드의 'sorry'·'can't assist' 확인과 같은 방식).
#
#     Args:
#         text: 모델이 돌려준 글자.
#
#     Returns:
#         bool: 거부 문구가 들어 있으면 True.
#     """
#     lowered = text.lower()
#     for marker in _REFUSAL_MARKERS:
#         if marker in lowered:
#             return True
#     return False
#
#
# def _ocr_to_drafts(text: str, page_no: int) -> list[ChunkDraft]:
#     """OCR 결과를 청크 초안으로 만든다.
#
#     빈 줄로 나뉜 문단을 차례로 모아 `DRAFT_MAX_CHARS` 이하로 묶는다. 문단은 중간에서 자르지 않으므로
#     한 문단이 기준보다 길면 그 문단만 담은 초안이 기준을 넘을 수 있다.
#
#     Args:
#         text: `_ocr_page`가 돌려준 전사 글자.
#         page_no: 전사한 쪽 번호. 1부터 센다.
#
#     Returns:
#         list[ChunkDraft]: `kind="ocr"`, `top=0.0`, `section=None`인 초안 목록. 글자가 없으면 빈 목록.
#     """
#     # 1) 빈 줄(공백만 있는 줄 포함)을 기준으로 문단을 나누고, 빈 문단은 버린다.
#     paragraphs = []
#     for piece in re.split(r"\n\s*\n", text):
#         piece = piece.strip()
#         if piece:
#             paragraphs.append(piece)
#
#     # 2) 문단을 차례로 이어 붙이다가, 다음 문단을 더하면 기준 글자 수를 넘을 때 새 묶음을 시작한다.
#     groups: list[str] = []
#     current = ""
#     for paragraph in paragraphs:
#         if current:
#             candidate = current + "\n\n" + paragraph
#         else:
#             candidate = paragraph
#
#         if current and len(candidate) > DRAFT_MAX_CHARS:
#             groups.append(current)
#             current = paragraph
#         else:
#             current = candidate
#     if current:
#         groups.append(current)
#
#     # 3) 묶음마다 초안을 만든다. 이미지 쪽은 위치를 알 수 없어 `top=0.0`(쪽 맨 위)으로 둔다.
#     drafts = []
#     for group in groups:
#         drafts.append(ChunkDraft(page=page_no, top=0.0, kind="ocr", section=None, text=group))
#     return drafts
