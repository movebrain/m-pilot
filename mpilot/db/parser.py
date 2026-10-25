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
