"""DB 연결·테이블 준비와 초기 자료(에러코드 표, 시드 이력) 적재.

역할:
    앱과 `scripts.init_db`가 DB를 쓰기 전에 연결하고, 테이블을 만들고, 검증한 초기 자료를 올린다.
    에러코드 표 조회(`fetch_error_codes`)도 여기 둔다.

책임:
    - `connect`가 만든 엔진은 `_common._engine`에 보관한다(엔진은 한 곳에만 둔다).
      모듈을 import할 때 DB에 접속하지 않는다.
    - 에러코드 CSV와 시드 이력 CSV를 검증한 뒤 적재한다. 값을 자동으로 보정하지 않고, 오류가 있으면 중단한다.
    - 시드 이력의 시각(ISO 8601, 시간대 포함)은 UTC로 바꾼 뒤 시간대 없는 DATETIME으로 저장한다.

의존: `rdb._common`, `schemas`, `models`만 import한다(의존 규칙 §2.5).
호출: `service`, `app`, `scripts.init_db`(패키지 `rdb`를 거쳐), `rdb.chat`(`fetch_error_codes`).
소유: `connect`·`reset_schema`는 DB 파트 공동(손재호·이재서·김성일),
    `load_error_codes_from_csv`·`fetch_error_codes`·`load_seed_history_from_csv`는 김성일.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, insert, select
from sqlalchemy.exc import ArgumentError, SQLAlchemyError

from mpilot.db.rdb import _common
from mpilot.db.rdb._common import _get_engine, _row_to_error_code, _to_db_time
from mpilot.db.schemas import error_codes, metadata, resolutions, service_cases
from mpilot.models import (
    CaseInput,
    CaseStatus,
    DependencyError,
    ErrorCode,
    Equipment,
    Resolution,
    Route,
    Source,
    ValidationError,
)


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
    #    엔진은 _common 한 곳에 둔다(다른 파일의 _get_engine이 그곳을 읽는다).
    if _common._engine is not None:
        _common._engine.dispose()
    _common._engine = engine


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
    머리글에 DB 컬럼이 모두 있어야 하며, 그 밖의 열(사람 검수 참고용 `required_safety_rules` 등)은 읽지 않는다.
    틀린 값은 고치지 않고 적재를 멈춘다.

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
    if not set(columns) <= set(df.columns) or df.empty:
        raise ValidationError(f"{Path(path).name}: 머리글에 {list(columns)}가 모두 있어야 하며 데이터도 있어야 합니다.")
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
            # 설비 4종·심각도·조치 구분·쪽(≥1)은 models가 검증한다. DB 컬럼 밖의 열은 읽지 않는다.
            code = ErrorCode(**{c: row[c] for c in columns})
        except ValueError as e:  # pydantic 오류는 ValueError다
            raise ValidationError(f"{no}번째 행 값이 올바르지 않습니다: {e}") from None
        if (code.equipment, code.code) in seen:
            raise ValidationError(f"{no}번째 행: (equipment, code) 중복 {code.equipment}/{code.code}")
        seen.add((code.equipment, code.code))
        codes.append(code)

    values = [c.model_dump(mode="json") for c in codes]  # 검증을 통과한 값을 저장한다.
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
