"""매뉴얼 파서 안에서 주고받는 중간 자료(pydantic 모델).

역할:
    표 추출·텍스트 경로·번호 매기기 사이에서 오가는 값의 모양을 정한다. 밖으로는 `Chunk`(models)만 나간다.

책임:
    - `RawTable`(표 추출이 만듦), `ChunkDraft`(텍스트·표·OCR 초안의 공통 형식),
      `TextLine`·`PageText`·`Section`(텍스트 경로), `Footer`(꼬리글에서 읽은 출처)를 둔다.
    - 값 검사만 하고 PDF는 읽지 않는다.

의존: `models`만 import한다(의존 규칙 §2.5).
소유: DB 파트(`RawTable`·표 초안: 이재서, 텍스트 경로·`Footer`: 손재호)
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
