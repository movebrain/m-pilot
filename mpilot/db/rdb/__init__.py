"""신고·대화·조치의 저장·조회와 초기 자료 적재(MySQL, SQLAlchemy Core).

역할:
    관계형 DB가 신고 건·조치 기록·대화·에러코드 표의 정본이다. 이 패키지가 그 저장과 조회를 맡는다.
    밖에서는 지금처럼 `from mpilot.db import rdb` 뒤 `rdb.<함수>`로 부른다(spec §2.3.1).

책임:
    - spec §2.3.1의 공개 함수를 하위 모듈에서 가져와 다시 내보낸다. 비공개 이름(`_engine` 등)은 내보내지 않는다.
      테스트가 비공개 도구를 쓸 때는 하위 모듈(`rdb._common` 등)을 직접 import한다.
    - 시각 컬럼(DATETIME)에는 UTC 기준, 시간대 없는 값으로 저장하고, 꺼낼 때 UTC 시간대를 붙여
      datetime으로 돌려준다. `completed_at`은 `created_at`보다 빠를 수 없다.
    - DB 컬럼 `customer_memo`·`self_guide_answer`와 모델 필드 `memo`·`guidance_answer`를
      서로 변환한다(spec §3.3). 컬럼 이름은 이 패키지 밖으로 내보내지 않는다.
    - SQL과 DB 접속은 이 패키지(와 `schemas`)에만 둔다. 패키지를 import할 때 DB에 접속하지 않는다.
    - Chroma 검색 색인은 다루지 않는다. 색인은 `vectordb`가 맡는다.

    _common.py   엔진 보관, 시각·JSON 변환, 행 → 업무 객체, _load_case_detail (DB 파트 공동)
    setup.py     connect, reset_schema, 에러코드·시드 이력 CSV 적재, fetch_error_codes
    cases.py     신고 저장(셀프 가이드·지원 요청), 목록·상세 조회 (손재호)
    chat.py      대화 저장·조회, 조치 확정, 확정 이력 조회 (이재서, fetch_resolved_cases는 김성일)

의존: 패키지 안 파일끼리만 서로 부르고, 밖으로는 `schemas`, `models`만 import한다(의존 규칙 §2.5).
    RAG 모듈은 부르지 않는다.
호출: `service`, `app`, `scripts.init_db`. `fake.fake_db`는 이 패키지의 공개 함수를 바꿔 끼운다.
소유: DB 파트
"""

from mpilot.db.rdb.cases import (
    MEMO_MAX_CHARS,
    fetch_case_detail,
    escalate_self_guide_case,
    fetch_cases,
    resolve_self_guide_case,
    save_self_guide_case,
    save_support_case,
)
from mpilot.db.rdb.chat import (
    QUESTION_MAX_CHARS,
    RESOLUTION_MAX_CHARS,
    fetch_chat_turns,
    fetch_resolved_cases,
    save_chat_turn,
    save_confirmed_resolution,
)
from mpilot.db.rdb.setup import (
    connect,
    fetch_error_codes,
    load_error_codes_from_csv,
    load_seed_history_from_csv,
    reset_schema,
)

__all__ = [
    "MEMO_MAX_CHARS",
    "QUESTION_MAX_CHARS",
    "RESOLUTION_MAX_CHARS",
    "connect",
    "escalate_self_guide_case",
    "fetch_case_detail",
    "fetch_cases",
    "fetch_chat_turns",
    "fetch_error_codes",
    "fetch_resolved_cases",
    "load_error_codes_from_csv",
    "load_seed_history_from_csv",
    "reset_schema",
    "resolve_self_guide_case",
    "save_chat_turn",
    "save_confirmed_resolution",
    "save_self_guide_case",
    "save_support_case",
]
