"""DB 초기화·초기 자료 적재 실행 스크립트(`python -m scripts.init_db`).

역할:
    시연 전에 한 번 실행해 MySQL을 비우고 에러코드 표와 서비스 이력 시드 200건을 적재한다.

책임:
    - `config.load_config`로 설정을 읽고 `rdb.connect`로 연결한 뒤, 스키마 재생성 → 에러코드 적재 →
      시드 이력 적재 순서로 실행한다.
    - 검증 오류가 있으면 보정하지 않고 중단한다.
    - 실행할 때마다 모든 테이블을 지우고 다시 만든다. 시연 중에 다시 실행하면 새로 확정한 이력이 사라지므로
      시연 전에 한 번만 실행한다. 검색 색인은 앱을 다시 시작할 때 DB에서 새로 만든다.
    - 앱 예외(`MpilotError` 하위)는 안내 메시지를 출력하고 종료 코드 1로 바꾼다.

의존: `config`, `db.rdb`, `models`(의존 규칙 §2.5).
소유: DB 파트
"""

from __future__ import annotations

import sys

from mpilot.config import load_config
from mpilot.db import rdb
from mpilot.models import MpilotError


def main() -> int:
    """DB 초기화와 초기 자료 적재를 실행하고 성공 0·실패 1을 반환한다.

    Returns:
        int: 모든 적재가 성공하면 `0`, 검증 오류나 DB 장애 등 앱 예외가 났으면 `1`.
            앱 예외는 밖으로 던지지 않고 안내를 출력한 뒤 `1`로 바꿔 돌려준다.
    """
    try:
        config = load_config()
        # 접속 URL에는 비밀번호가 있으므로 출력하지 않는다. 예외 문구에도 rdb가 URL을 넣지 않는다.
        rdb.connect(config.db_url.get_secret_value())
        rdb.reset_schema()
        codes = rdb.load_error_codes_from_csv(config.codes_path)
        seed_count = rdb.load_seed_history_from_csv(config.history_seed_path)
    except MpilotError as e:
        print(f"DB 초기화에 실패했습니다({type(e).__name__}): {e}", file=sys.stderr)
        return 1

    print(f"DB 초기화 완료: 에러코드 {len(codes)}건, 시드 이력 {seed_count}건을 적재했습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
