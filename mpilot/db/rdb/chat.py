"""엔지니어 대화의 저장·조회, 조치 확정, 조치 기록이 있는 신고(확정 이력) 조회.

역할:
    엔지니어 화면의 질문·답변을 남기고, 조치 결과를 등록해 신고를 완료로 확정한다.
    검색 색인 `history`를 새로 만들 때 쓸 확정 이력을 읽어 준다.

책임:
    - 완료된 건(셀프 가이드 건 포함)에는 질문·조치 결과를 더 저장하지 않는다. 신고를 `SELECT ... FOR UPDATE`로
      잠근 뒤 확인하고, 완료 처리는 조건부 UPDATE로 보호한다(중복 제출·동시 수정 방지는 이것뿐이다).
    - 첫 질문이면 `received` → `in_progress`로 바꾼다. `completed_at`은 `created_at`보다 빠를 수 없다.
    - Chroma 검색 색인은 다루지 않는다. 색인 반영은 `service`가 `vectordb`를 불러 한다.

의존: `rdb._common`, `rdb.setup`(`fetch_error_codes`), `schemas`, `models`만 import한다(의존 규칙 §2.5).
호출: `service`(패키지 `rdb`를 거쳐).
소유: 대화·조치(`fetch_chat_turns`, `save_chat_turn`, `save_confirmed_resolution`)는 이재서,
    `fetch_resolved_cases`는 김성일.
"""

from __future__ import annotations

from sqlalchemy import insert, select, update
from sqlalchemy.exc import SQLAlchemyError

from mpilot.db.rdb._common import (
    _from_db_time,
    _get_engine,
    _load_case_detail,
    _now_for_db,
    _row_to_resolution,
    _row_to_service_case,
)
from mpilot.db.rdb.setup import fetch_error_codes
from mpilot.db.schemas import chat_turns, resolutions, service_cases
from mpilot.models import (
    Answer,
    CaseDetail,
    CaseStatus,
    ChatTurn,
    DependencyError,
    Equipment,
    InvalidStateError,
    NotFoundError,
    Resolution,
    ServiceCase,
    ValidationError,
    raise_if_case_completed,
)

QUESTION_MAX_CHARS = 2000  # 질문 최대 글자 수(앞뒤 공백 제거 후)
RESOLUTION_MAX_CHARS = 5000  # 조치 기록 각 필드 최대 글자 수(앞뒤 공백 제거 후)


def _row_to_chat_turn(row) -> ChatTurn:
    """`chat_turns` 한 행을 `ChatTurn`으로 바꾼다.

    Args:
        row: `select(chat_turns)` 결과 행.

    Returns:
        ChatTurn: 답변을 `Answer`로 되돌리고 시각에 UTC 시간대를 붙인 대화 한 턴.

    Raises:
        DependencyError: 저장된 답변 JSON이 깨져 업무 객체로 바꿀 수 없을 때.
    """
    try:
        return ChatTurn(
            turn_id=row.turn_id,
            case_id=row.case_id,
            question=row.question,
            answer=Answer.model_validate_json(row.answer),
            created_at=_from_db_time(row.created_at),
        )
    except ValueError:
        raise DependencyError(f"저장된 대화의 형식이 올바르지 않습니다: turn_id={row.turn_id}") from None


def _lock_open_case(conn, case_id: str) -> ServiceCase:
    """신고 한 건을 잠그고 읽은 뒤, 아직 완료되지 않았는지 확인한다.

    `SELECT ... FOR UPDATE`로 읽으므로, 같은 건에 대한 다른 저장·완료 처리는 이 트랜잭션이
    끝날 때까지 기다린다. 그래서 "확인한 뒤 저장하기 전에 다른 사람이 완료하는" 일이 생기지 않는다.

    Args:
        conn: `engine.begin()`으로 연 트랜잭션 연결.
        case_id: 접수번호.

    Returns:
        ServiceCase: 잠근 신고 건(상태 `received` 또는 `in_progress`).

    Raises:
        NotFoundError: 그 접수번호의 신고가 없을 때.
        InvalidStateError: 이미 완료된 건이거나 셀프 가이드 건일 때('이미 완료').
    """
    row = conn.execute(
        select(service_cases).where(service_cases.c.case_id == case_id).with_for_update()
    ).first()
    if row is None:
        raise NotFoundError(f"접수번호 {case_id}의 신고가 없습니다.")
    case = _row_to_service_case(row)
    # 셀프 가이드 건은 접수 때 곧바로 completed라서 같은 규칙으로 막힌다.
    raise_if_case_completed(case)
    return case


def fetch_chat_turns(case_id: str) -> list[ChatTurn]:
    """신고의 대화 이력을 조회한다.

    Args:
        case_id: 접수번호.

    Returns:
        list[ChatTurn]: `turn_id` 순(오래된 순) 대화 목록. 대화가 없으면 빈 목록.

    Raises:
        DependencyError: DB 조회에 실패했을 때.
    """
    query = (
        select(chat_turns)
        .where(chat_turns.c.case_id == case_id)
        .order_by(chat_turns.c.turn_id)
    )
    try:
        with _get_engine().connect() as conn:
            rows = conn.execute(query).all()
    except SQLAlchemyError as e:
        raise DependencyError(f"대화 기록을 조회하지 못했습니다({type(e).__name__}).") from None

    turns = []
    for row in rows:
        turns.append(_row_to_chat_turn(row))
    return turns


def save_chat_turn(case_id: str, question: str, answer: Answer) -> ChatTurn:
    """질문·답변을 저장하고 첫 질문이면 조치 중으로 바꾼다.

    보류 판정(`insufficient_evidence`)의 답변도 저장한다. 상태가 `received`이면
    `in_progress`로 바꾸고, 이미 `in_progress`이면 상태는 그대로 둔다. 완료된 건은 저장하지 않는다.

    Args:
        case_id: 접수번호.
        question: 엔지니어의 질문. 앞뒤 공백을 제거한 뒤 1~2,000자.
        answer: 그 질문에 대한 답변(보류 판정 포함).

    Returns:
        ChatTurn: 저장된 대화 한 턴(자동 증가한 `turn_id` 포함).

    Raises:
        ValidationError: 질문이 비었거나 길이 범위를 벗어났을 때.
        NotFoundError: 그 접수번호의 신고가 없을 때.
        InvalidStateError: 이미 완료된 건이거나 셀프 가이드 건일 때('이미 완료').
        DependencyError: DB 저장에 실패했을 때.
    """
    # 1) 질문 길이를 확인한다. 앞뒤 공백은 지우고 저장한다.
    question = question.strip()
    if not question or len(question) > QUESTION_MAX_CHARS:
        raise ValidationError(f"질문은 앞뒤 공백을 빼고 1~{QUESTION_MAX_CHARS:,}자여야 합니다.")

    engine = _get_engine()
    now = _now_for_db()
    try:
        # engine.begin(): 블록이 무사히 끝나면 커밋하고, 중간에 오류가 나면 모두 되돌린다.
        with engine.begin() as conn:
            # 2) 신고를 잠그고 읽는다. 없거나 이미 완료된 건이면 여기서 멈춘다.
            case = _lock_open_case(conn, case_id)

            # 3) 대화 한 턴을 저장한다. turn_id는 DB가 자동으로 매긴다.
            result = conn.execute(
                insert(chat_turns).values(
                    case_id=case_id,
                    question=question,
                    answer=answer.model_dump_json(),
                    created_at=now,
                )
            )
            turn_id = result.inserted_primary_key[0]

            # 4) 첫 질문이면 received → in_progress로 바꾼다.
            if case.status == CaseStatus.RECEIVED:
                conn.execute(
                    update(service_cases)
                    .where(service_cases.c.case_id == case_id)
                    .values(status=CaseStatus.IN_PROGRESS.value)
                )
    except SQLAlchemyError as e:
        raise DependencyError(f"대화를 저장하지 못했습니다({type(e).__name__}).") from None

    return ChatTurn(
        turn_id=turn_id,
        case_id=case_id,
        question=question,
        answer=answer,
        created_at=_from_db_time(now),
    )


def save_confirmed_resolution(case_id: str, record: Resolution) -> CaseDetail:
    """조치 결과를 저장하고 조치 완료로 확정한다.

    `resolutions` 행을 만들고 `status`를 `completed`로 바꾸며 `completed_at`을 기록한다.
    등록과 완료 처리는 한 동작이다. 같은 건을 동시에 완료하는 일은 조건부 UPDATE로 막는다.
    확정 뒤에는 수정하지 않는다.

    Args:
        case_id: 접수번호.
        record: 엔지니어가 쓴 원인·실제 조치·결과. 각 필드는 앞뒤 공백 제거 후 1~5,000자.

    Returns:
        CaseDetail: 완료된 신고 건과 확정된 조치 기록.

    Raises:
        ValidationError: 필드가 비었거나 길이 범위를 벗어났을 때.
        NotFoundError: 그 접수번호의 신고가 없을 때.
        InvalidStateError: 이미 완료된 건이거나 셀프 가이드 건일 때('이미 완료').
        DependencyError: DB 저장에 실패했을 때.
    """
    # 1) 세 필드의 길이를 확인한다. Resolution을 만들 때 이미 검사하지만 저장 직전에 한 번 더 본다.
    cause = record.cause.strip()
    action = record.action.strip()
    result_text = record.result.strip()
    for text in [cause, action, result_text]:
        if not text or len(text) > RESOLUTION_MAX_CHARS:
            raise ValidationError(
                f"원인·조치·결과는 앞뒤 공백을 빼고 각각 1~{RESOLUTION_MAX_CHARS:,}자여야 합니다."
            )

    engine = _get_engine()
    try:
        with engine.begin() as conn:
            # 2) 신고를 잠그고 읽는다. 없거나 이미 완료된 건이면 여기서 멈춘다.
            case = _lock_open_case(conn, case_id)

            # 완료 시각은 지금이다. 다만 신고 시각보다 빠를 수는 없게 맞춘다(서버 시계가 어긋난 경우).
            completed_at = _now_for_db()
            created_at = case.created_at.replace(tzinfo=None)
            if completed_at < created_at:
                completed_at = created_at

            # 3) 조건부 UPDATE: 아직 완료되지 않은 건만 completed로 바꾼다.
            #    바뀐 행이 없으면 다른 요청이 먼저 완료한 것이므로 '이미 완료'로 알린다.
            changed = conn.execute(
                update(service_cases)
                .where(
                    service_cases.c.case_id == case_id,
                    service_cases.c.status != CaseStatus.COMPLETED.value,
                )
                .values(status=CaseStatus.COMPLETED.value, completed_at=completed_at)
            )
            if changed.rowcount == 0:
                raise InvalidStateError("이미 완료")

            # 4) 조치 기록을 저장한다. 3)과 같은 트랜잭션이라 둘 중 하나만 저장되는 일은 없다.
            conn.execute(
                insert(resolutions).values(
                    case_id=case_id, cause=cause, action=action, result=result_text
                )
            )

            # 5) 저장한 결과를 다시 읽어 돌려준다.
            detail = _load_case_detail(conn, case_id)
    except SQLAlchemyError as e:
        raise DependencyError(f"조치 결과를 저장하지 못했습니다({type(e).__name__}).") from None
    return detail


def fetch_resolved_cases() -> list[CaseDetail]:
    """조치 기록이 있는 신고를 조회한다.

    검색 색인의 `history` 컬렉션을 새로 만들 때 쓴다. 시드 이력도 포함한다.
    셀프 가이드 건에는 `resolutions` 행이 없어 포함되지 않는다.

    Returns:
        list[CaseDetail]: `resolution`이 채워진 신고 목록.

    Raises:
        DependencyError: DB 조회에 실패했을 때.
    """
    codes = {(c.equipment, c.code): c for e in Equipment for c in fetch_error_codes(e)}
    try:
        with _get_engine().connect() as conn:
            # resolutions와 내부 조인 → 조치 기록이 없는 건(셀프 가이드·미완료)은 빠진다.
            rows = conn.execute(
                select(service_cases, resolutions.c.cause, resolutions.c.action, resolutions.c.result)
                .join(resolutions, resolutions.c.case_id == service_cases.c.case_id)
                .order_by(service_cases.c.case_id)  # 색인 재생성이 매번 같은 순서가 되도록
            ).all()
    except SQLAlchemyError as e:
        raise DependencyError(f"조치 기록이 있는 신고를 조회하지 못했습니다({type(e).__name__}).") from None
    # 컬럼 이름 바꾸기(customer_memo → memo 등)와 UTC 붙이기는 공통 도구가 한다.
    return [
        CaseDetail(
            service_case=_row_to_service_case(row),
            error_code=codes[(row.equipment, row.code)],
            resolution=_row_to_resolution(row),
        )
        for row in rows
    ]
