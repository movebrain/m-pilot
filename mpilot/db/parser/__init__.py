"""매뉴얼 PDF 파싱·청킹: PDF를 출처가 붙은 검색 단위로 나눈다.

역할:
    설비 매뉴얼 PDF(pdfplumber)를 읽어, 쪽·절 출처가 붙은 `Chunk` 목록으로 분해한다.
    이 청크가 `manual` 검색 컬렉션과 고객 안내 인용의 원문이 된다.

책임:
    - 같은 PDF에서는 항상 같은 청크·같은 `chunk_no`를 낸다(재현성).
    - 청크 ID 구성 요소(`manual_id`, `page`, `chunk_no`)를 확정한다.
      ID 형식 `manual:<manual_id>:<page>:<chunk_no>`와 청킹 규칙은 1주차 초에 확정·동결한다.
      `scripts.build_error_codes`가 이 ID로 `customer_procedure_ref_ids`를 만들므로 동결 뒤에는 바꾸지 않는다.
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

패키지 구성(#139에서 parser.py를 나눔. 함수 본문은 그대로 옮겼다):
    - `__init__`: `parse_manuals`(전체 흐름 조립). 밖에서 쓰는 것은 이 함수뿐이다.
    - `types`: parser 안에서 주고받는 중간 자료(`RawTable`, `ChunkDraft`, `TextLine`, `PageText`, `Section`, `Footer`)
    - `text`: 꼬리글 읽기, 쪽별 본문 추출(#79), 절 나누기·텍스트 초안(#80)
    - `tables`: 표 추출·검증·표 초안
    - `chunks`: 청크 번호 매기기, 청크 ID 중복 검사(#81)
    - `ocr`: [보류] 이미지 페이지 OCR(주석)
    `parse_manuals`가 쓰는 하위 모듈 함수는 이 모듈 이름공간에서 찾으므로, 테스트가 바꿔 끼울 때는
    `mpilot.db.parser`(이 패키지)에 해야 효과가 있다.

의존: 패키지 안 하위 모듈과 `models`만 import한다(의존 규칙 §2.5).
호출: `service.prepare_search_index`가 앱 시작 때 부른다.
소유: DB 파트
"""

from __future__ import annotations

from pathlib import Path

from mpilot.db.parser.chunks import _check_unique_chunk_ids, _number_chunks
from mpilot.db.parser.tables import _extract_tables, _table_to_drafts
from mpilot.db.parser.text import (
    _equipment_from_manual_id,
    _extract_page_texts,
    _list_pdf_files,
    _split_sections,
    _to_text_drafts,
)
from mpilot.models import Chunk, Equipment, ValidationError

__all__ = ["parse_manuals"]


def parse_manuals(directory: Path) -> list[Chunk]:
    """매뉴얼 PDF를 출처가 붙은 검색 단위로 분해한다.

    PDF마다(파일 이름순) 표 추출 → 표 영역을 뺀 쪽별 본문 → 절 나누기 → 텍스트·표 초안 → 번호 매기기 순으로 잇는다.
    `manual_id`·`version`은 꼬리글에서 읽은 `PageText`에서, 설비는 문서번호의 설비 코드로 정한다.
    이미지 페이지 OCR 초안은 OCR을 보류한 동안 넣지 않는다(`ocr` 모듈의 주석을 풀 때 여기에 더한다).

    Args:
        directory: 매뉴얼 PDF(설비 4종)가 들어 있는 폴더. `AppConfig.manuals_dir`의 값.

    Returns:
        list[Chunk]: 쪽·절 출처(`manual_id`, `page`, `section`, `chunk_no`, `version`)가 붙은 청크 목록.
            같은 PDF를 넣으면 순서와 ID 구성이 항상 같다.

    Raises:
        ValidationError: 폴더에 PDF가 없거나 설비를 알 수 없을 때, 같은 설비 매뉴얼이 두 개일 때,
            ID 구성 요소에 `:`가 있을 때, 청크 ID가 겹칠 때.
        DependencyError: PDF를 읽거나 파싱하는 데 실패했을 때.
    """
    chunks: list[Chunk] = []  # 돌려줄 청크(매뉴얼 파일 이름순, 매뉴얼 안에서는 위치순)
    files_by_equipment: dict[Equipment, str] = {}  # 설비 → 그 설비 매뉴얼 파일 이름. 같은 설비 중복 확인용
    for pdf_path in _list_pdf_files(directory):  # 파일 이름순이라 늘 같은 순서로 돈다
        tables = _extract_tables(pdf_path)  # 표를 먼저 뽑아야 본문에서 표 영역을 뺄 수 있다
        page_texts = _extract_page_texts(pdf_path, tables)  # 표지를 뺀 쪽별 본문. 꼬리글이 하나도 없으면 여기서 ValidationError
        manual_id, version = page_texts[0].manual_id, page_texts[0].version  # 모든 쪽이 같은 값임을 _extract_page_texts가 확인했다
        equipment = _equipment_from_manual_id(manual_id)  # 파일명이 아니라 문서번호의 설비 코드로 정한다
        if equipment in files_by_equipment:  # 같은 설비 매뉴얼이 이미 있으면(예: 구 Rev 파일이 남아 있음)
            raise ValidationError(  # 어느 쪽을 써야 할지 추측하지 않고 적재 오류로 낸다
                f"같은 설비({equipment.value}) 매뉴얼이 두 개입니다: {files_by_equipment[equipment]}, {pdf_path.name}"
            )  # raise 끝
        files_by_equipment[equipment] = pdf_path.name  # 이 설비의 매뉴얼로 기록한다
        drafts = _to_text_drafts(_split_sections(page_texts))  # 목차 기준으로 절을 나눈 텍스트 초안
        for table in tables:  # 표마다
            drafts += _table_to_drafts(table)  # 캡션과 마크다운 표로 된 표 초안을 더한다
        chunks += _number_chunks(drafts, manual_id, version, equipment)  # 위치순 정렬, 절 채우기, 쪽마다 번호
    _check_unique_chunk_ids(chunks)  # 매뉴얼 사이에서도 ID가 겹치지 않는지 마지막으로 확인한다
    return chunks  # 매뉴얼 4종의 청크
