"""관계형 DB 구조·연결 설정 정의(SQLAlchemy Core).

역할:
    MySQL 테이블 4개의 구조를 한 곳에 정의한다(spec §3.3). 함수 인터페이스는 spec에 없고,
    `rdb`가 이 정의를 가져다 쓴다.

정의할 테이블(구현 때 `sqlalchemy.Table`로 만든다):
    - `error_codes`: PK `(equipment, code)`. 에러코드 표 한 행과 가이드 검증용 목록 열 2개
      (`customer_procedure_ref_ids`, `required_safety_rules`, JSON 배열 문자열).
    - `service_cases`: PK `case_id`. FK `(equipment, code)` → `error_codes`.
      `customer_memo`(TEXT, NULL), `source`, `status`, `route`, `guide_failure`,
      `self_guide_answer`(TEXT, NULL, JSON 문자열), `created_at`(DATETIME, NOT NULL),
      `completed_at`(DATETIME, NULL).
    - `resolutions`: PK이자 FK `case_id` → `service_cases`. 건당 한 행. `cause`, `action`, `result`.
    - `chat_turns`: PK `turn_id`(자동 증가). FK `case_id` → `service_cases`. `question`,
      `answer`(JSON 문자열), `created_at`(DATETIME, NOT NULL).

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

의존: `models`만 import한다(의존 규칙 §2.5).
소유: DB 파트
"""
