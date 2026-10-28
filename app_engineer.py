"""엔지니어 앱 진입점(CHG-01). `python app_engineer.py`로 7861 포트에 띄운다.

역할:
    엔지니어 역할로 고정한 앱을 실행한다. 첫 화면은 엔지니어 대시보드다.
    `python app_engineer.py --fake`는 DB·RAG 연결 없이 가짜 데이터로 화면만 띄운다.

의존: `app`.
소유: UI·총괄 파트
"""

from __future__ import annotations

from app import main
from mpilot.models import Role

PORT = 7861

if __name__ == "__main__":
    main(Role.ENGINEER, PORT)
