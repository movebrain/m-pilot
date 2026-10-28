"""테스트 공통 준비물(fixture).

`rdb_db`: 실제 MySQL 테스트 DB(`MYSQL_TEST_DATABASE`)에 연결하고 빈 테이블 4개를 만든 뒤 `rdb` 모듈을 넘겨준다.
    - CI(GitHub Actions)는 `.github/workflows/ci.yml`의 MySQL 서비스 컨테이너를 쓴다.
      CI에서 테스트 DB에 붙지 못하면 건너뛰지 않고 실패시킨다(초록불로 문제를 가리지 않으려는 것).
    - 로컬은 시연용 `mpilot` DB와 별개로 테스트 DB를 한 번 만들고 `.env`에 이름을 적는다.
          CREATE DATABASE IF NOT EXISTS mpilot_test;
          GRANT ALL ON mpilot_test.* TO '<MYSQL_USER>'@'%';
          (.env) MYSQL_TEST_DATABASE=mpilot_test
      로컬에서 접속 정보가 없거나 DB에 붙지 못하면 DB 테스트만 건너뛴다(`python -m pytest -rs`로 사유 확인).
    - 테스트마다 모든 테이블을 지우고 다시 만든다. 그래서 로컬에서 테스트 DB 이름이 시연 DB
      (`MYSQL_DATABASE`)와 같으면 시연 자료를 지우지 않도록 건너뛴다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import dotenv_values
from sqlalchemy.engine import URL

ROOT = Path(__file__).resolve().parent.parent


def _read_env() -> dict:
    """`.env` 값 위에 환경 변수를 덮어쓴 설정을 돌려준다. 빈 값은 없는 것으로 본다.

    Returns:
        dict: 설정 이름 → 값.
    """
    values = {}
    if (ROOT / ".env").exists():
        values.update(dotenv_values(ROOT / ".env"))
    values.update(os.environ)
    env = {}
    for key, value in values.items():
        if value:
            env[key] = value
    return env


def _in_ci() -> bool:
    """GitHub Actions처럼 `CI` 환경 변수가 켜진 곳에서 도는지 알려준다.

    Returns:
        bool: CI이면 True.
    """
    return os.environ.get("CI", "").lower() in ("1", "true", "yes")


def _skip_or_fail(reason: str) -> None:
    """CI에서는 실패로, 로컬에서는 건너뛰기로 알린다.

    Args:
        reason: 사유 문구.
    """
    if _in_ci():
        pytest.fail(f"CI인데 테스트 DB를 쓸 수 없습니다: {reason}", pytrace=False)
    pytest.skip(reason)


def _make_test_db_url() -> str | None:
    """테스트 DB 접속 URL을 만든다. 필요한 값이 없으면 None.

    Returns:
        str | None: `mysql+pymysql://...` 접속 URL(비밀번호 포함).
    """
    env = _read_env()
    for key in ["MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_HOST", "MYSQL_TEST_DATABASE"]:
        if key not in env:
            return None
    url = URL.create(
        "mysql+pymysql",
        username=env["MYSQL_USER"],
        password=env["MYSQL_PASSWORD"],
        host=env["MYSQL_HOST"],
        port=int(env.get("MYSQL_PORT", "3306")),
        database=env["MYSQL_TEST_DATABASE"],
    )
    return url.render_as_string(hide_password=False)


@pytest.fixture
def rdb_db():
    """테스트 DB에 연결하고 빈 테이블 4개를 만든 뒤 `rdb` 모듈을 넘겨준다."""
    from mpilot.db import rdb
    from mpilot.models import DependencyError

    env = _read_env()
    url = _make_test_db_url()
    if url is None:
        _skip_or_fail("MYSQL_TEST_DATABASE 등 테스트 DB 접속 정보가 없습니다.")
    if not _in_ci() and env.get("MYSQL_TEST_DATABASE") == env.get("MYSQL_DATABASE"):
        pytest.skip("테스트 DB가 시연 DB와 같아 건너뜁니다(테스트는 모든 테이블을 지웁니다).")
    try:
        rdb.connect(url)
    except DependencyError:
        _skip_or_fail("테스트 DB에 연결할 수 없습니다(docker compose up -d, mpilot_test 생성 확인).")

    rdb.reset_schema()
    yield rdb

    # 다음 테스트가 연결 전 상태에서 시작하도록 연결을 닫는다.
    rdb._engine.dispose()
    rdb._engine = None


MANUALS_DIR = ROOT / "data" / "manuals"


@pytest.fixture(scope="session")
def real_page_texts():
    """실제 매뉴얼 PDF의 쪽별 본문(표 영역을 빼지 않은 것)을 PDF 이름으로 돌려주는 함수. PDF마다 처음 한 번만 읽는다.

    합본 PDF는 한 번 읽는 데 AGV만 30초 가까이 걸려서, 테스트마다 다시 읽으면 CI 제한 시간(10분)을 넘는다.
    돌려주는 목록은 여러 테스트가 나눠 쓰므로 고치지 않는다.
    """
    import logging

    from mpilot.db import parser

    logging.getLogger("pdfminer").setLevel(logging.ERROR)  # 글꼴 경고가 수천 줄 나온다
    extract = parser._extract_page_texts  # 모듈 함수를 바꿔 끼우는 테스트가 있어도 진짜 함수를 부른다
    cache: dict[str, list] = {}

    def get(pdf_name: str) -> list:
        if pdf_name not in cache:
            cache[pdf_name] = extract(MANUALS_DIR / pdf_name, tables=[])
        return cache[pdf_name]

    return get


@pytest.fixture(scope="session")
def real_tables():
    """실제 매뉴얼 PDF의 표를 PDF 이름으로 돌려주는 함수. PDF마다 처음 한 번만 읽는다(`real_page_texts`와 같은 이유)."""
    import logging

    from mpilot.db import parser

    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    extract = parser._extract_tables  # 다른 테스트가 모듈 함수를 이 캐시로 바꿔 끼워도(재귀) 진짜 함수를 부르도록 미리 잡아 둔다
    cache: dict[str, list] = {}

    def get(pdf_name: str) -> list:
        if pdf_name not in cache:
            cache[pdf_name] = extract(MANUALS_DIR / pdf_name)
        return cache[pdf_name]

    return get
