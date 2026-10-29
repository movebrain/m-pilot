"""신고·대화·조치의 저장·조회와 초기 자료 적재(MySQL, SQLAlchemy Core).

역할:
    관계형 DB가 신고 건·조치 기록·대화·에러코드 표의 정본이다. 이 모듈이 그 저장과 조회를 맡는다.

책임:
    - 에러코드 CSV와 시드 이력 CSV를 검증한 뒤 적재한다. 값을 자동으로 보정하지 않고, 오류가 있으면 중단한다.
    - 신고를 상태 전이 규칙(spec §2.10)에 맞춰 저장한다. 완료된 건에는 질문·조치 결과를 더 저장하지 않는다.
      완료 처리는 조건부 UPDATE로 보호한다(중복 제출·동시 수정 방지는 이것뿐이다).
    - 목록은 최신순으로 돌려준다. 고객에게 어떤 필드를 보일지는 `service`가 정한다.
    - 시각 컬럼(DATETIME)에는 UTC 기준, 시간대 없는 값으로 저장하고, 꺼낼 때 UTC 시간대를 붙여
      datetime으로 돌려준다. `completed_at`은 `created_at`보다 빠를 수 없다.
    - DB 컬럼 `customer_memo`·`self_guide_answer`와 모델 필드 `memo`·`guidance_answer`를
      서로 변환한다(spec §3.3). 컬럼 이름은 이 모듈 밖으로 내보내지 않는다.
    - SQL과 DB 접속은 이 모듈(과 `schemas`)에만 둔다. 모듈을 import할 때 DB에 접속하지 않는다.
    - Chroma 검색 색인은 다루지 않는다. 색인은 `vectordb`가 맡는다.

의존: `schemas`, `models`만 import한다(의존 규칙 §2.5). RAG 모듈은 부르지 않는다.
호출: `service`, `app`, `scripts.init_db`.
소유: DB 파트
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, insert, select, update
from sqlalchemy.exc import ArgumentError, SQLAlchemyError

from mpilot.db.schemas import chat_turns, error_codes, metadata, resolutions, service_cases
from mpilot.models import (
    Answer,
    CaseDetail,
    CaseInput,
    CaseStatus,
    ChatTurn,
    DependencyError,
    ErrorCode,
    Equipment,
    GuideFailure,
    InvalidStateError,
    NotFoundError,
    Resolution,
    Route,
    ServiceCase,
    Source,
    ValidationError,
    raise_if_case_completed,
)

# ---------------------------------------------------------------------------
# 공통 도구(DB 파트 3명이 함께 쓴다. 바꿀 때는 DB 파트에 먼저 알린다.)
#
#   _get_engine()            connect가 만든 엔진 꺼내기
#   _now_for_db()            지금 시각을 DATETIME 저장용 값으로
#   _to_db_time(value)       시간대가 있는 시각을 DATETIME 저장용 값으로(시드 적재 등)
#   _from_db_time(value)     DATETIME에서 꺼낸 값에 UTC 시간대 붙이기
#   _to_json_list(values)    문자열 목록을 JSON 배열 문자열로
#   _row_to_error_code(row)  error_codes 행 → ErrorCode
#   _row_to_service_case(row) service_cases 행 → ServiceCase
#   _row_to_resolution(row)  resolutions 행 → Resolution
#   _load_case_detail(conn, case_id)  접수번호 한 건 → CaseDetail(없으면 NotFoundError)
#
# 저장된 값(JSON, 열거형 값)이 깨져 업무 객체로 바꿀 수 없으면 행 변환 함수가 DependencyError를 낸다.
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# spec §2.3.1 함수
# ---------------------------------------------------------------------------


def connect(db_url: str) -> None:
    """관계형 DB 연결을 준비한다.

    Args:
        db_url: SQLAlchemy 접속 URL(예: `mysql+pymysql://...`, 비밀번호 포함).
            `AppConfig.db_url`의 값이며 로그에 남기지 않는다.

    Returns:
        None: 모듈 안에 엔진을 보관한다. 이후 함수는 이 연결을 쓴다.

    Raises:
        ValidationError: URL 형식이 틀렸을 때.
        DependencyError: DB에 연결할 수 없을 때.
    """
    global _engine

    # 1) 엔진을 만든다. URL 형식이나 드라이버 이름이 틀리면 여기서 오류가 난다.
    #    URL에는 비밀번호가 있으므로 오류 문구에 원문을 넣지 않는다.
    try:
        # pool_pre_ping: 오래 쉬어서 끊긴 연결을 쓰기 전에 다시 잇는다(시연 중 끊김 방지).
        engine = create_engine(db_url, pool_pre_ping=True)
    except (ArgumentError, ValueError):
        raise ValidationError("DB 접속 URL 형식이 올바르지 않습니다.") from None

    # 2) 실제로 접속되는지 한 번 확인한다.
    try:
        with engine.connect():
            pass
    except SQLAlchemyError as e:
        engine.dispose()
        raise DependencyError(
            f"DB에 연결할 수 없습니다({type(e).__name__}). 접속 정보와 DB 실행 상태를 확인하세요."
        ) from None

    # 3) 다시 연결하는 경우 이전 연결은 닫고 새 엔진을 보관한다.
    if _engine is not None:
        _engine.dispose()
    _engine = engine


def reset_schema() -> None:
    """모든 테이블을 삭제하고 다시 만든다.

    `scripts.init_db`가 실행할 때마다 부른다. 시연 중에 다시 부르면 새로 확정한 이력이 사라진다.

    Returns:
        None: 빈 테이블 4개(`error_codes`, `service_cases`, `resolutions`, `chat_turns`)가 만들어진다.

    Raises:
        DependencyError: 테이블 삭제·생성에 실패했을 때.
    """
    engine = _get_engine()
    try:
        # 외래키 순서(지울 때 자식 → 부모, 만들 때 부모 → 자식)는 SQLAlchemy가 맞춘다.
        metadata.drop_all(engine)
        metadata.create_all(engine)
    except SQLAlchemyError as e:
        raise DependencyError(f"테이블을 다시 만들지 못했습니다({type(e).__name__}).") from None


def load_error_codes_from_csv(path: Path) -> list[ErrorCode]:
    """에러코드 CSV를 검증해 적재한다.

    필수값, `(equipment, code)` 중복, 설비 4종, 출처(`manual_id`, `page`, `section`)를 검증한다.
    두 목록 열은 JSON 배열 문자열이며 비어 있으면 `[]`다. 틀린 값은 고치지 않고 적재를 멈춘다.

    Args:
        path: 에러코드 CSV 경로(`AppConfig.codes_path`, `data/errorcodes.csv`).

    Returns:
        list[ErrorCode]: 적재한 에러코드 표의 행 목록.

    Raises:
        ValidationError: 필수값 누락, 중복 키, 허용 밖 설비, 출처 누락 등 CSV 오류가 있을 때.
        DependencyError: 파일을 읽거나 DB에 쓰는 데 실패했을 때.
    """
    engine = _get_engine()  # connect 전이면 여기서 DependencyError
    columns = error_codes.c.keys()  # CSV 열 = error_codes 컬럼(spec §3.4)
    try:
        # dtype=str·keep_default_na=False: 숫자·빈칸을 pandas가 바꾸지 않게 글자 그대로 읽는다(자동 보정 금지).
        df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        raise DependencyError(f"파일을 읽을 수 없습니다({type(e).__name__}): {path}") from None
    except (pd.errors.ParserError, pd.errors.EmptyDataError) as e:
        raise ValidationError(f"{Path(path).name}: CSV 형식이 올바르지 않습니다: {e}") from None
    if set(df.columns) != set(columns) or df.empty:
        raise ValidationError(f"{Path(path).name}: 머리글이 {list(columns)}와 다르거나 데이터가 없습니다.")
    rows = df.to_dict("records")

    codes, seen = [], set()
    for no, row in enumerate(rows, start=1):
        # 모든 값이 채워져야 한다(공백뿐이면 빈 값, 열이 모자란 행은 NaN). 값은 고치지 않는다.
        if not all(isinstance(row[c], str) and row[c].strip() for c in columns):
            raise ValidationError(f"{no}번째 행: 필수값이 비었거나 열 개수가 다릅니다.")
        # VARCHAR(n) 칸은 n자를 넘으면 MySQL이 거부한다. DB 오류가 되기 전에 CSV 오류로 알린다.
        for c in columns:
            limit = getattr(error_codes.c[c].type, "length", None)  # TEXT·INTEGER는 None
            if limit and len(row[c]) > limit:
                raise ValidationError(f"{no}번째 행: {c}는 {limit}자 이하여야 합니다.")
        try:
            # 설비 4종·심각도·조치 구분·쪽(≥1)은 models가 검증한다. 목록 열 2개는 JSON 배열 문자열이다.
            code = ErrorCode(**{
                **row,
                "customer_procedure_ref_ids": json.loads(row["customer_procedure_ref_ids"]),
                "required_safety_rules": json.loads(row["required_safety_rules"]),
            })
        except ValueError as e:  # JSON 오류, pydantic 오류 모두 ValueError
            raise ValidationError(f"{no}번째 행 값이 올바르지 않습니다: {e}") from None
        if (code.equipment, code.code) in seen:
            raise ValidationError(f"{no}번째 행: (equipment, code) 중복 {code.equipment}/{code.code}")
        seen.add((code.equipment, code.code))
        codes.append(code)

    # 검증을 통과한 값을 저장한다. 목록 열은 공통 도구로 JSON 문자열로 되돌린다.
    values = [
        {
            **c.model_dump(mode="json"),
            "customer_procedure_ref_ids": _to_json_list(c.customer_procedure_ref_ids),
            "required_safety_rules": _to_json_list(c.required_safety_rules),
        }
        for c in codes
    ]
    try:
        with engine.begin() as conn:  # 한 트랜잭션: 실패하면 아무것도 남지 않는다
            conn.execute(insert(error_codes), values)
    except SQLAlchemyError as e:
        raise DependencyError(f"에러코드를 DB에 저장하지 못했습니다({type(e).__name__}).") from None
    return codes


def fetch_error_codes(equipment: Equipment) -> list[ErrorCode]:
    """설비별 에러코드를 조회한다.

    Args:
        equipment: 조회할 설비.

    Returns:
        list[ErrorCode]: 그 설비의 에러코드 표 행 목록. 없으면 빈 목록.

    Raises:
        ValidationError: 허용되지 않은 설비일 때.
        DependencyError: DB 조회에 실패했을 때.
    """
    try:
        equipment = Equipment(equipment)
    except ValueError:
        raise ValidationError(f"허용되지 않은 설비입니다: {equipment}") from None
    try:
        with _get_engine().connect() as conn:
            rows = conn.execute(
                select(error_codes)
                .where(error_codes.c.equipment == equipment.value)
                .order_by(error_codes.c.code)  # 선택지 순서가 매번 같게
            ).all()
    except SQLAlchemyError as e:
        raise DependencyError(f"에러코드를 조회하지 못했습니다({type(e).__name__}).") from None
    return [_row_to_error_code(row) for row in rows]


def load_seed_history_from_csv(path: Path) -> int:
    """시드 이력 CSV를 검증해 적재하고 적재 건수를 반환한다.

    설비당 50건, 총 200건이어야 한다. 시드는 `source=seed`, `route=support`, `guide_failure=NULL`,
    `self_guide_answer=NULL`, `status=completed`로 고정해 `service_cases`와 `resolutions`에 쓴다.
    CSV `memo` 열은 `service_cases.customer_memo`에 넣는다. 시간은 ISO 8601(시간대 포함)을
    UTC로 바꾼 뒤 시간대 없는 DATETIME으로 저장한다. 코드·필수값 오류면 중단한다.

    Args:
        path: 시드 이력 CSV 경로(`AppConfig.history_seed_path`, `data/history_seed.csv`).

    Returns:
        int: 적재한 이력 건수(정상이면 200).

    Raises:
        ValidationError: 에러코드 표에 없는 코드, 필수값 누락, 건수 불일치, 완료 시각이 신고 시각보다 빠를 때.
        DependencyError: 파일을 읽거나 DB에 쓰는 데 실패했을 때.
    """
    engine = _get_engine()  # connect 전이면 여기서 DependencyError
    columns = ["case_id", "equipment", "code", "memo", "cause", "action", "result",
               "created_at", "completed_at"]  # spec §3.4 history_seed.csv
    try:
        # dtype=str·keep_default_na=False: 숫자·빈칸을 pandas가 바꾸지 않게 글자 그대로 읽는다(자동 보정 금지).
        df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        raise DependencyError(f"파일을 읽을 수 없습니다({type(e).__name__}): {path}") from None
    except (pd.errors.ParserError, pd.errors.EmptyDataError) as e:
        raise ValidationError(f"{Path(path).name}: CSV 형식이 올바르지 않습니다: {e}") from None
    if set(df.columns) != set(columns) or df.empty:
        raise ValidationError(f"{Path(path).name}: 머리글이 {columns}와 다르거나 데이터가 없습니다.")
    rows = df.to_dict("records")

    known_codes = {(c.equipment, c.code) for e in Equipment for c in fetch_error_codes(e)}

    cases, records = [], []
    for no, row in enumerate(rows, start=1):
        case_id = row["case_id"]
        # 메모 밖의 값이 모두 채워져야 한다(공백뿐이면 빈 값, 열이 모자란 행은 NaN).
        if not all(isinstance(row[c], str) and (c == "memo" or row[c].strip()) for c in columns):
            raise ValidationError(f"{no}번째 행: 필수값이 비었거나 열 개수가 다릅니다.")
        if ":" in case_id:  # 문서 ID history:<case_id>의 구분자(spec §3.4)
            raise ValidationError(f"{no}번째 행: case_id에 ':'가 있습니다: {case_id}")
        if len(case_id) > service_cases.c.case_id.type.length:  # VARCHAR(64), 넘으면 MySQL이 거부
            raise ValidationError(f"{no}번째 행: case_id가 너무 깁니다: {case_id}")
        if any(c["case_id"] == case_id for c in cases):
            raise ValidationError(f"{no}번째 행: case_id 중복 {case_id}")
        try:
            # 설비 4종·메모 2,000자·원인/조치/결과 1~5,000자는 models 규칙으로 검증한다.
            data = CaseInput(equipment=row["equipment"], code=row["code"],
                             memo=row["memo"] if row["memo"].strip() else None)
            record = Resolution(cause=row["cause"], action=row["action"], result=row["result"])
            # 공통 도구: UTC로 바꾸고 시간대를 뗀다. 시간대가 없으면 짐작하지 않고 ValidationError.
            created_at = _to_db_time(datetime.fromisoformat(row["created_at"]))
            completed_at = _to_db_time(datetime.fromisoformat(row["completed_at"]))
        except (ValueError, ValidationError) as e:
            raise ValidationError(f"{no}번째 행 값이 올바르지 않습니다: {e}") from None
        if (data.equipment, data.code) not in known_codes:
            raise ValidationError(f"{no}번째 행: 에러코드 표에 없는 코드 {data.equipment}/{data.code}")
        if completed_at < created_at:
            raise ValidationError(f"{no}번째 행: 완료 시각이 신고 시각보다 빠릅니다.")

        # 시드 고정값(spec §3.4). 컬럼 이름은 최종 ERD를 따른다(#61).
        cases.append({"case_id": case_id, "equipment": data.equipment.value, "code": data.code,
                      "customer_memo": data.memo,
                      "source": Source.SEED.value, "status": CaseStatus.COMPLETED.value,
                      "route": Route.SUPPORT.value, "guide_failure": None, "self_guide_answer": None,
                      "created_at": created_at, "completed_at": completed_at})
        records.append({"case_id": case_id, **record.model_dump()})

    per_equipment = 50  # 설비당 50건, 총 200건(spec §3.4)
    count = Counter(c["equipment"] for c in cases)
    if any(count[e.value] != per_equipment for e in Equipment):
        raise ValidationError(f"건수 불일치: 설비당 {per_equipment}건이어야 합니다. "
                              f"현재 { {e.value: count[e.value] for e in Equipment} }")

    try:
        with engine.begin() as conn:  # 신고 건 → 조치 기록 순(FK), 한 트랜잭션
            conn.execute(insert(service_cases), cases)
            conn.execute(insert(resolutions), records)
    except SQLAlchemyError as e:
        raise DependencyError(f"시드 이력을 DB에 저장하지 못했습니다({type(e).__name__}).") from None
    return len(cases)


def fetch_cases(
    status: CaseStatus | None = None, source: Source | None = None
) -> list[CaseDetail]:
    """신고 목록을 최신순으로 조회한다.

    Args:
        status: 지정하면 그 상태의 신고만 돌려준다. `None`이면 모든 상태.
        source: 지정하면 시드 이력(`seed`) 또는 앱 접수 건(`reported`)만 돌려준다. `None`이면 모두.

    Returns:
        list[CaseDetail]: `created_at` 내림차순 신고 목록. 조치 기록이 있으면 `resolution`이 채워진다.

    Raises:
        ValidationError: 허용되지 않은 `status`·`source` 값일 때.
        DependencyError: DB 조회에 실패했을 때.
    """
    # 1) 필터 값을 확인한다. 틀린 값을 조용히 무시하면 엉뚱한 목록이 나가므로 오류로 알린다.
    try:
        status = CaseStatus(status) if status is not None else None
        source = Source(source) if source is not None else None
    except ValueError:
        raise ValidationError(f"허용되지 않은 조회 조건입니다: status={status}, source={source}") from None

    # 2) 신고 건에 조치 기록을 외부 조인한다(조치 기록이 없는 건도 나오게).
    query = (
        select(service_cases, resolutions.c.cause, resolutions.c.action, resolutions.c.result)
        .outerjoin(resolutions, resolutions.c.case_id == service_cases.c.case_id)
        # 최신순. 신고 시각이 같으면 접수번호 역순으로 순서를 고정한다.
        .order_by(service_cases.c.created_at.desc(), service_cases.c.case_id.desc())
    )
    if status is not None:
        query = query.where(service_cases.c.status == status.value)
    if source is not None:
        query = query.where(service_cases.c.source == source.value)

    try:
        with _get_engine().connect() as conn:
            rows = conn.execute(query).all()
            code_rows = conn.execute(select(error_codes)).all()
    except SQLAlchemyError as e:
        raise DependencyError(f"신고 목록을 조회하지 못했습니다({type(e).__name__}).") from None

    # 3) 에러코드 행을 (설비, 코드)로 붙인다. 외래키가 있어 반드시 있다.
    codes = {(r.equipment, r.code): _row_to_error_code(r) for r in code_rows}
    return [
        CaseDetail(
            service_case=_row_to_service_case(row),
            error_code=codes[(row.equipment, row.code)],
            # 외부 조인이라 조치 기록이 없으면 세 컬럼이 모두 NULL이다.
            resolution=_row_to_resolution(row) if row.cause is not None else None,
        )
        for row in rows
    ]


def fetch_case_detail(case_id: str) -> CaseDetail:
    """신고 상세를 조회한다.

    Args:
        case_id: 접수번호.

    Returns:
        CaseDetail: 신고 건, 신고한 에러코드 행, 조치 기록(있으면).

    Raises:
        NotFoundError: 그 접수번호의 신고가 없을 때.
        DependencyError: DB 조회에 실패했을 때.
    """
    try:
        with _get_engine().connect() as conn:
            return _load_case_detail(conn, case_id)
    except SQLAlchemyError as e:
        raise DependencyError(f"신고 상세를 조회하지 못했습니다({type(e).__name__}).") from None


def save_self_guide_case(
    case_id: str, data: CaseInput, guidance_answer: Answer
) -> CaseDetail:
    """셀프 가이드 건을 완료 상태로 저장한다.

    `source=reported`, `route=self_guide`, `status=completed`, `guide_failure=NULL`로 저장하고
    가이드의 내용·단계·출처를 `service_cases.self_guide_answer`에 JSON으로 남긴다.
    `resolutions` 행은 만들지 않는다.

    Args:
        case_id: 새 접수번호(`SC-<UUID 앞 8자리>`). PK가 충돌하면 재시도 없이 오류로 알린다.
        data: 고객이 제출한 설비·에러코드·증상 메모.
        guidance_answer: 생성·검증을 통과한 고객 안내. `verdict`는 `answered`여야 한다.

    Returns:
        CaseDetail: 저장된 신고 건(상태 `completed`)과 신고한 에러코드 행.

    Raises:
        ValidationError: 미등록 코드, 메모 길이 초과, `answered`가 아닌 안내일 때.
        DependencyError: DB 저장에 실패했을 때(접수번호 충돌 포함).
    """
    ...


def save_support_case(
    case_id: str, data: CaseInput, guide_failure: GuideFailure | None
) -> CaseDetail:
    """엔지니어 처리 건을 접수 상태로 저장한다.

    `source=reported`, `route=support`, `status=received`로 저장한다.

    Args:
        case_id: 새 접수번호(`SC-<UUID 앞 8자리>`). PK가 충돌하면 재시도 없이 오류로 알린다.
        data: 고객이 제출한 설비·에러코드·증상 메모.
        guide_failure: 셀프 가이드 후보였다가 넘어온 사유(`held`, `generation_failed`).
            처음부터 엔지니어 처리 대상이면 `None`.

    Returns:
        CaseDetail: 저장된 신고 건(상태 `received`)과 신고한 에러코드 행.

    Raises:
        ValidationError: 미등록 코드나 메모 길이 초과일 때.
        DependencyError: DB 저장에 실패했을 때(접수번호 충돌 포함).
    """
    ...


# ---------------------------------------------------------------------------
# 대화·조치 저장(담당: 이재서)
# ---------------------------------------------------------------------------

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
