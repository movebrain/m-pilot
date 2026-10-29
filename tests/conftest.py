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
PARSE_CACHE_DIR = ROOT / ".pytest_parse_cache"


class _DiskParse:
    """실제 매뉴얼 PDF를 읽는 두 함수(표 추출, 쪽별 본문 추출)에 디스크 캐시를 씌운다.

    합본 PDF는 4종을 읽는 데 3분 가까이 걸려, 캐시 없이는 CI(GitHub Actions)가 10분 넘게 걸린다.
    캐시 파일 이름의 해시에 PDF·`parser.py`·`pdfplumber` 버전이 들어 있어 어느 하나라도 바뀌면 새로 읽는다.
    캐시 파일이 없거나 깨졌으면(모델 필드가 바뀐 경우 포함) 조용히 새로 읽는다. 캐시가 테스트를 막지는 않는다.
    """

    def __init__(self) -> None:
        import logging

        import pdfplumber
        from pydantic import TypeAdapter

        from mpilot.db import parser

        logging.getLogger("pdfminer").setLevel(logging.ERROR)  # 글꼴 경고가 수천 줄 나온다
        # 다른 테스트가 모듈 함수를 이 캐시로 바꿔 끼워도(재귀) 진짜 함수를 부르도록 미리 잡아 둔다
        self._extract_tables = parser._extract_tables
        self._extract_page_texts = parser._extract_page_texts
        self._tables = TypeAdapter(list[parser.RawTable])
        self._page_texts = TypeAdapter(list[parser.PageText])
        self._salt = Path(parser.__file__).read_bytes() + pdfplumber.__version__.encode()

    def tables(self, path: Path) -> list:
        return self._cached("tables", path, b"", self._tables, lambda: self._extract_tables(path))

    def page_texts(self, path: Path, tables: list) -> list:
        extra = self._tables.dump_json(tables)  # 본문에서 뺄 표 영역이 달라지면 결과도 다르다
        return self._cached("texts", path, extra, self._page_texts, lambda: self._extract_page_texts(path, tables))

    def _cached(self, kind: str, path: Path, extra: bytes, adapter, compute):
        import hashlib

        key = hashlib.sha256(path.read_bytes() + self._salt + extra).hexdigest()[:16]
        variant = hashlib.sha256(extra).hexdigest()[:8]  # 본문 추출은 표 목록마다 따로 남긴다(서로의 캐시를 지우지 않도록)
        stem = f"{kind}-{path.stem}-{variant}"
        file = PARSE_CACHE_DIR / f"{stem}-{key}.json"
        try:
            return adapter.validate_json(file.read_bytes())
        except (OSError, ValueError):  # 파일이 없거나 깨짐. pydantic 검증 오류도 ValueError다
            pass
        result = compute()
        try:
            PARSE_CACHE_DIR.mkdir(exist_ok=True)
            for old in PARSE_CACHE_DIR.glob(f"{stem}-*.json"):  # 입력이 바뀌어 못 쓰게 된 것
                if old != file:
                    old.unlink()
            file.write_bytes(adapter.dump_json(result))
        except OSError:  # 읽기 전용 디스크 등. 캐시만 못 남기고 테스트는 계속한다
            pass
        return result


@pytest.fixture(scope="session")
def disk_parse() -> _DiskParse:
    """PDF를 읽는 두 함수의 디스크 캐시판. 이 세션에서 PDF 하나당 한 번만 읽고, CI는 이전 실행의 캐시를 이어 쓴다."""
    return _DiskParse()


@pytest.fixture(scope="session")
def real_page_texts(disk_parse):
    """실제 매뉴얼 PDF의 쪽별 본문(표 영역을 빼지 않은 것)을 PDF 이름으로 돌려주는 함수. PDF마다 처음 한 번만 읽는다.

    돌려주는 목록은 여러 테스트가 나눠 쓰므로 고치지 않는다.
    """
    memo: dict[str, list] = {}

    def get(pdf_name: str) -> list:
        if pdf_name not in memo:
            memo[pdf_name] = disk_parse.page_texts(MANUALS_DIR / pdf_name, [])
        return memo[pdf_name]

    return get


@pytest.fixture(scope="session")
def real_tables(disk_parse):
    """실제 매뉴얼 PDF의 표를 PDF 이름으로 돌려주는 함수. PDF마다 처음 한 번만 읽는다(`real_page_texts`와 같은 이유)."""
    memo: dict[str, list] = {}

    def get(pdf_name: str) -> list:
        if pdf_name not in memo:
            memo[pdf_name] = disk_parse.tables(MANUALS_DIR / pdf_name)
        return memo[pdf_name]

    return get
