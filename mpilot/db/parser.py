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

의존: `models`만 import한다(의존 규칙 §2.5).
호출: `service.prepare_search_index`가 앱 시작 때 부른다.
소유: DB 파트
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from mpilot.models import Chunk


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
