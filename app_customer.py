"""고객 앱 진입점(CHG-01). `python app_customer.py`로 7860 포트에 띄운다.

역할:
    고객 역할로 고정한 앱을 실행한다. 첫 화면은 고객 신고 목록이다.
    `python app_customer.py --fake`는 DB·RAG 연결 없이 가짜 데이터로 화면만 띄운다.

의존: `app`.
소유: UI·총괄 파트
"""

from __future__ import annotations

from app import main
from mpilot.models import Role

PORT = 7860

if __name__ == "__main__":
    main(Role.CUSTOMER, PORT)
