"""관계형 DB 구조·연결 설정 정의(SQLAlchemy Core).

역할:
    MySQL 테이블 4개의 구조를 한 곳에 정의한다(spec §3.3). 함수 인터페이스는 spec에 없고,
    `rdb`가 이 정의를 가져다 쓴다.

정의한 테이블:
    - `error_codes`: PK `(equipment, code)`. 에러코드 표 한 행.
    - `service_cases`: PK `case_id`. FK `(equipment, code)` → `error_codes`.
      `customer_memo`(TEXT, NULL), `source`, `status`, `route`, `guide_failure`,
      `self_guide_answer`(TEXT, NULL, JSON 문자열), `created_at`(DATETIME, NOT NULL),
      `completed_at`(DATETIME, NULL).
    - `resolutions`: PK이자 FK `case_id` → `service_cases`. 건당 한 행. `cause`, `action`, `result`.
    - `chat_turns`: PK `turn_id`(자동 증가). FK `case_id` → `service_cases`. `question`,
      `answer`(JSON 문자열), `created_at`(DATETIME, NOT NULL).

ERD와 다른 점:
    - ERD의 고객·엔지니어 테이블과 `service_cases.customer_id`·`engineer_id` 컬럼은 포함 여부가
      아직 정해지지 않아 만들지 않는다. 정해지면 이 파일에 테이블과 컬럼을 추가한다.

컬럼과 모델 필드:
    - `customer_memo`·`self_guide_answer`는 모델 필드 `memo`·`guidance_answer`에 대응한다.
      이름 변환은 `rdb`가 맡는다.
    - DATETIME에는 UTC 기준, 시간대 없는 값으로 저장하고, 꺼낼 때 UTC 시간대를 붙여
      datetime으로 돌려준다(`rdb`가 맡는다).

책임:
    - 컬럼 이름·타입·NULL 여부·DB 수준 제약(PK·FK)을 spec §3.3 표와 맞춘다.
    - `Equipment`·`Severity`·길이 범위 같은 값 검증은 앱(`models`)이 맡으므로 여기서 중복하지 않는다.
    - 다중 고객사·사용자·사진·반영 상태 등 범위 밖 항목(spec §3.3 '모델에 두지 않는 것')은 컬럼으로 만들지 않는다.
    - 모듈을 import할 때 DB에 접속하지 않는다. 연결은 `rdb.connect`가 맡는다.

의존: `models`만 import한다(의존 규칙 §2.5). 지금은 `models`의 값이 필요 없어 sqlalchemy만 쓴다.
소유: DB 파트
"""

from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)

# 키 컬럼(설비·코드·접수번호)은 대소문자와 끝 공백까지 글자 그대로 비교한다.
# 기본 콜레이션은 대소문자를 무시해서 `sc-abcd1234`로 `SC-ABCD1234` 건을 찾아 버리기 때문이다.
# 외래키 양쪽 컬럼은 콜레이션이 같아야 하므로, 키 컬럼은 모두 아래 세 타입을 쓴다.
EQUIPMENT_TYPE = String(32, collation="utf8mb4_0900_bin")
CODE_TYPE = String(32, collation="utf8mb4_0900_bin")
CASE_ID_TYPE = String(64, collation="utf8mb4_0900_bin")

# 한글을 저장하므로 모든 테이블을 utf8mb4로 만든다.
TABLE_OPTIONS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}

# 모든 테이블을 모아 두는 곳. `rdb.reset_schema`가 이것으로 테이블을 지우고 다시 만든다.
metadata = MetaData()

# 에러코드 사전
error_codes = Table(
    "error_codes",
    metadata,
    Column("equipment", EQUIPMENT_TYPE, primary_key=True),  # 설비
    Column("code", CODE_TYPE, primary_key=True),  # 에러코드. 설비 안에서 유일
    Column("name", String(255), nullable=False),  # 에러명
    Column("severity", String(8), nullable=False),  # 심각도(상·중·하)
    Column("action_owner", String(16), nullable=False),  # 조치 구분
    Column("cause", Text, nullable=False),  # 코드 표의 원인
    Column("action", Text, nullable=False),  # 코드 표의 조치
    Column("manual_id", String(64), nullable=False),  # 출처 PDF 식별자
    Column("page", Integer, nullable=False),  # 출처 쪽(1 이상)
    Column("section", String(255), nullable=False),  # 출처 절
    **TABLE_OPTIONS,
)

# 신고 접수 대장
service_cases = Table(
    "service_cases",
    metadata,
    Column("case_id", CASE_ID_TYPE, primary_key=True),  # 접수번호. 신규 `SC-<UUID 앞 8자리>`, 시드는 원본 ID
    Column("equipment", EQUIPMENT_TYPE, nullable=False),
    Column("code", CODE_TYPE, nullable=False),
    Column("customer_memo", Text, nullable=True),  # 모델 필드 memo. 2,000자 이하
    Column("source", String(16), nullable=False),  # seed / reported
    Column("status", String(16), nullable=False),  # received / in_progress / completed
    Column("route", String(16), nullable=False),  # self_guide / support
    Column("guide_failure", String(32), nullable=True),  # held / generation_failed
    Column("self_guide_answer", Text, nullable=True),  # 모델 필드 guidance_answer(Answer JSON)
    Column("created_at", DateTime, nullable=False),  # 신고 시각(UTC)
    Column("completed_at", DateTime, nullable=True),  # 완료 시각(UTC)
    # 신고 한 건에는 설비·에러코드가 하나다. (설비, 코드) 두 컬럼으로 에러코드 표를 참조한다.
    ForeignKeyConstraint(
        ["equipment", "code"],
        ["error_codes.equipment", "error_codes.code"],
        name="fk_service_cases_error_codes",
    ),
    **TABLE_OPTIONS,
)

# 수리 결과 대장. case_id가 PK라서 신고 한 건에 한 행뿐이다.
resolutions = Table(
    "resolutions",
    metadata,
    Column(
        "case_id",
        CASE_ID_TYPE,
        ForeignKey("service_cases.case_id", name="fk_resolutions_service_cases"),
        primary_key=True,
    ),
    Column("cause", Text, nullable=False),  # 실제 원인(고객 비공개)
    Column("action", Text, nullable=False),  # 실제 조치(고객 공개)
    Column("result", Text, nullable=False),  # 조치 결과(고객 공개)
    **TABLE_OPTIONS,
)

# 대화 기록장. turn_id 순서가 대화 순서다.
chat_turns = Table(
    "chat_turns",
    metadata,
    Column("turn_id", Integer, primary_key=True, autoincrement=True),
    Column(
        "case_id",
        CASE_ID_TYPE,
        ForeignKey("service_cases.case_id", name="fk_chat_turns_service_cases"),
        nullable=False,
    ),
    Column("question", Text, nullable=False),  # 엔지니어 질문(1~2,000자)
    Column("answer", Text, nullable=False),  # 답변(Answer JSON). 보류 판정도 저장
    Column("created_at", DateTime, nullable=False),  # 저장 시각(UTC)
    **TABLE_OPTIONS,
)
