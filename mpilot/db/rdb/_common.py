"""rdb 패키지의 공통 도구: 엔진 보관, 시각·JSON 변환, DB 행 → 업무 객체 변환.

역할:
    `setup`·`cases`·`chat`이 함께 쓰는 도구를 한곳에 둔다. 공개 함수는 없다(패키지 밖에서 부르지 않는다).

책임:
    - `connect`가 만든 SQLAlchemy 엔진을 `_engine` 한 곳에 보관한다. 다른 파일과 테스트는 이 모듈의
      `_engine`을 읽고 바꾼다(패키지 `rdb`에 따로 두지 않는다).
    - 시각 컬럼(DATETIME)에는 UTC 기준, 시간대 없는 값으로 저장하고, 꺼낼 때 UTC 시간대를 붙인다.
    - DB 컬럼 `customer_memo`·`self_guide_answer`를 모델 필드 `memo`·`guidance_answer`로 바꿔 담는다(spec §3.3).
    - 저장된 값(JSON, 열거형 값)이 깨져 업무 객체로 바꿀 수 없으면 행 변환 함수가 DependencyError를 낸다.

    _get_engine()            connect가 만든 엔진 꺼내기
    _now_for_db()            지금 시각을 DATETIME 저장용 값으로
    _to_db_time(value)       시간대가 있는 시각을 DATETIME 저장용 값으로(시드 적재 등)
    _from_db_time(value)     DATETIME에서 꺼낸 값에 UTC 시간대 붙이기
    _to_json_list(values)    문자열 목록을 JSON 배열 문자열로
    _row_to_error_code(row)  error_codes 행 → ErrorCode
    _row_to_service_case(row) service_cases 행 → ServiceCase
    _row_to_resolution(row)  resolutions 행 → Resolution
    _load_case_detail(conn, case_id)  접수번호 한 건 → CaseDetail(없으면 NotFoundError)

의존: `schemas`, `models`만 import한다(의존 규칙 §2.5).
호출: `rdb.setup`, `rdb.cases`, `rdb.chat`.
소유: DB 파트 공동(손재호·이재서·김성일). 바꿀 때는 DB 파트에 먼저 알린다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import select

from mpilot.db.schemas import error_codes, resolutions, service_cases
from mpilot.models import (
    Answer,
    CaseDetail,
    DependencyError,
    ErrorCode,
    NotFoundError,
    Resolution,
    ServiceCase,
    ValidationError,
)

_engine = None  # connect()가 만든 SQLAlchemy 엔진. 아직 연결 전이면 None


def _get_engine():
    """`connect`로 만든 엔진을 돌려준다.

    Returns:
        Engine: 이 모듈이 보관 중인 SQLAlchemy 엔진.

    Raises:
        DependencyError: `connect`를 부르기 전일 때.
    """
    if _engine is None:
        raise DependencyError("DB가 연결되지 않았습니다. connect를 먼저 호출하세요.")
    return _engine


def _now_for_db() -> datetime:
    """지금 시각을 DATETIME 컬럼에 넣을 값으로 돌려준다(UTC 기준, 시간대 없음, 초 단위).

    Returns:
        datetime: 시간대와 마이크로초를 뗀 현재 UTC 시각.
    """
    now = datetime.now(UTC)
    return now.replace(microsecond=0, tzinfo=None)


def _to_db_time(value: datetime) -> datetime:
    """시간대가 있는 시각을 DATETIME 컬럼에 넣을 값으로 바꾼다(UTC 기준, 시간대 없음, 초 단위).

    Args:
        value: 바꿀 시각. 시간대가 있어야 한다(예: CSV의 `2026-03-02T09:15:00+09:00`).

    Returns:
        datetime: UTC로 바꾼 뒤 시간대와 마이크로초를 뗀 시각.

    Raises:
        ValidationError: 시간대가 없는 시각일 때(어느 시간대인지 추측하지 않는다).
    """
    if value.tzinfo is None:
        raise ValidationError("시간대가 없는 시각은 저장할 수 없습니다.")
    utc_value = value.astimezone(UTC)
    return utc_value.replace(microsecond=0, tzinfo=None)


def _from_db_time(value: datetime | None) -> datetime | None:
    """DATETIME 컬럼에서 꺼낸 값에 UTC 시간대를 붙인다.

    Args:
        value: DB에서 꺼낸 시간대 없는 UTC 시각. NULL이면 `None`.

    Returns:
        datetime | None: UTC 시간대가 붙은 시각. 입력이 `None`이면 `None`.
    """
    if value is None:
        return None
    return value.replace(tzinfo=UTC)


def _to_json_list(values: list[str]) -> str:
    """문자열 목록을 JSON 배열 문자열로 바꾼다(한글은 그대로 둔다).

    Args:
        values: 저장할 문자열 목록(예: `customer_procedure_ref_ids`).

    Returns:
        str: JSON 배열 문자열. 빈 목록이면 `[]`.
    """
    return json.dumps(values, ensure_ascii=False)


def _row_to_error_code(row) -> ErrorCode:
    """`error_codes` 한 행을 `ErrorCode`로 바꾼다.

    Args:
        row: `select(error_codes)` 결과 행.

    Returns:
        ErrorCode: 목록 열 2개를 JSON에서 되돌린 에러코드 표 한 행.

    Raises:
        DependencyError: 저장된 값이 깨져 업무 객체로 바꿀 수 없을 때.
    """
    try:
        return ErrorCode(
            equipment=row.equipment,
            code=row.code,
            name=row.name,
            severity=row.severity,
            action_owner=row.action_owner,
            cause=row.cause,
            action=row.action,
            manual_id=row.manual_id,
            page=row.page,
            section=row.section,
            customer_procedure_ref_ids=json.loads(row.customer_procedure_ref_ids),
            required_safety_rules=json.loads(row.required_safety_rules),
        )
    except ValueError:  # json 오류와 pydantic 검증 오류는 모두 ValueError다
        raise DependencyError(
            f"저장된 에러코드의 형식이 올바르지 않습니다: {row.equipment}:{row.code}"
        ) from None


def _row_to_service_case(row) -> ServiceCase:
    """`service_cases` 한 행을 `ServiceCase`로 바꾼다.

    DB 컬럼 `customer_memo`는 `memo`로, `self_guide_answer`는 `guidance_answer`로 바꿔 담는다.

    Args:
        row: `select(service_cases)` 결과 행.

    Returns:
        ServiceCase: 시각에 UTC 시간대를 붙이고 셀프 가이드를 `Answer`로 되돌린 신고 건.

    Raises:
        DependencyError: 저장된 값이 깨져 업무 객체로 바꿀 수 없을 때.
    """
    try:
        guidance_answer = None
        if row.self_guide_answer is not None:
            guidance_answer = Answer.model_validate_json(row.self_guide_answer)
        return ServiceCase(
            case_id=row.case_id,
            equipment=row.equipment,
            code=row.code,
            memo=row.customer_memo,
            source=row.source,
            status=row.status,
            route=row.route,
            guide_failure=row.guide_failure,
            guidance_answer=guidance_answer,
            created_at=_from_db_time(row.created_at),
            completed_at=_from_db_time(row.completed_at),
        )
    except ValueError:
        raise DependencyError(f"저장된 신고의 형식이 올바르지 않습니다: {row.case_id}") from None


def _row_to_resolution(row) -> Resolution | None:
    """`resolutions` 한 행을 `Resolution`으로 바꾼다.

    Args:
        row: `select(resolutions)` 결과 행. 조치 기록이 없으면 `None`.

    Returns:
        Resolution | None: 조치 기록. 입력이 `None`이면 `None`.

    Raises:
        DependencyError: 저장된 값이 깨져 업무 객체로 바꿀 수 없을 때.
    """
    if row is None:
        return None
    try:
        return Resolution(cause=row.cause, action=row.action, result=row.result)
    except ValueError:
        raise DependencyError(f"저장된 조치 기록의 형식이 올바르지 않습니다: {row.case_id}") from None


def _load_case_detail(conn, case_id: str) -> CaseDetail:
    """열린 연결에서 접수번호 한 건의 신고·에러코드·조치 기록을 읽어 묶는다.

    목록·상세 조회와 저장 뒤 반환에서 같은 모양의 `CaseDetail`을 만들 때 쓴다.

    Args:
        conn: 사용 중인 DB 연결(`engine.connect()`나 `engine.begin()`으로 연 것).
        case_id: 접수번호.

    Returns:
        CaseDetail: 신고 건, 신고한 에러코드 행, 조치 기록(없으면 `None`).

    Raises:
        NotFoundError: 그 접수번호의 신고가 없을 때.
        DependencyError: 저장된 값이 깨져 업무 객체로 바꿀 수 없을 때.
    """
    # 1) 신고 건
    case_row = conn.execute(
        select(service_cases).where(service_cases.c.case_id == case_id)
    ).first()
    if case_row is None:
        raise NotFoundError(f"접수번호 {case_id}의 신고가 없습니다.")

    # 2) 신고한 에러코드 행. 외래키가 있어 반드시 있다.
    code_row = conn.execute(
        select(error_codes).where(
            error_codes.c.equipment == case_row.equipment,
            error_codes.c.code == case_row.code,
        )
    ).first()

    # 3) 조치 기록. 현장 조치로 완료된 건에만 있다.
    resolution_row = conn.execute(
        select(resolutions).where(resolutions.c.case_id == case_id)
    ).first()

    return CaseDetail(
        service_case=_row_to_service_case(case_row),
        error_code=_row_to_error_code(code_row),
        resolution=_row_to_resolution(resolution_row),
    )
