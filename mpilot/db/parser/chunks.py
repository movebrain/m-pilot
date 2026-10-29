"""매뉴얼 청크 번호 매기기와 청크 ID 중복 검사(#81).

역할:
    텍스트·표·OCR 초안을 위치순으로 정렬하고 쪽마다 `chunk_no`를 매겨 `Chunk`로 만든다.

책임:
    - 청크 ID `manual:<manual_id>:<page>:<chunk_no>`가 여기서 정해진다. 같은 초안이면 늘 같은 번호가 나와야 한다.
    - 절을 알 수 없는 표·OCR 초안은 바로 앞 텍스트 초안의 절을 이어받는다.

의존: `parser.types`, `models`만 import한다(의존 규칙 §2.5).
소유: DB 파트(손재호)
"""

from __future__ import annotations

from pydantic import ValidationError as PydanticValidationError

from mpilot.db.parser.types import ChunkDraft
from mpilot.models import Chunk, Equipment, ValidationError

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


def _check_unique_chunk_ids(chunks: list[Chunk]) -> None:
    """청크 ID `manual:<manual_id>:<page>:<chunk_no>`가 겹치지 않는지 확인한다.

    ID가 겹치면 색인에서 한 청크가 다른 청크를 덮어써 인용 출처가 틀어지므로 적재 오류로 낸다.

    Args:
        chunks: 매뉴얼 전체의 청크.

    Returns:
        None: 겹치는 ID가 없으면 아무것도 돌려주지 않는다.

    Raises:
        ValidationError: 같은 ID가 두 번 이상 나올 때. 겹친 ID를 메시지에 담는다.
    """
    seen: set[str] = set()  # 이미 나온 ID
    duplicates: list[str] = []  # 두 번째로 나온 ID(나온 순서대로)
    for chunk in chunks:  # 청크 순서대로
        chunk_id = f"manual:{chunk.manual_id}:{chunk.page}:{chunk.chunk_no}"  # 검색 문서 ID와 같은 형식
        if chunk_id in seen:  # 앞에서 이미 나왔으면
            duplicates.append(chunk_id)  # 겹친 ID로 모은다
        seen.add(chunk_id)  # 나온 ID로 기록한다
    if duplicates:  # 하나라도 겹치면
        raise ValidationError(f"청크 ID가 겹칩니다: {duplicates[:5]}")  # 앞 5개만 보여 준다
