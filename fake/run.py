"""가짜 DB 위에서 고객(7860)·엔지니어(7861) 앱을 한 프로세스로 띄우는 수동 테스트용 진입점.

역할:
    `fake_db.install()`로 `rdb`·`vectordb`·`parser`만 메모리 구현으로 바꾸고, 나머지(`service`·`rag`·`ui`)는
    진짜 코드를 그대로 띄운다. MySQL과 docker 없이 UI에서 RAG까지 눈으로 확인하기 위한 도구다.

책임:
    - 기본(실제 모드): 루트 `.env`의 OpenAI 값으로 임베딩·LLM을 쓴다. 키가 없으면 앱이 시작되지 않는다.
    - `--offline`: 키 없이 배선만 확인한다. 해시 임베딩을 쓰고 LLM은 연결하지 않아, 고객 안내 생성은
      `DependencyError`가 되어 엔지니어 접수(`generation_failed`)로 떨어진다. 오류가 아니라 정해진 동작이다.
    - `--both`: 엔지니어 앱(7861)도 같은 프로세스에 띄운다. 없으면 고객 앱(7860)만 띄운다.
    - 앱이 쓰지 않는 MySQL·데모 계정 값은 `load_config`가 요구하므로, `.env`나 환경에 없을 때만 더미를 둔다.

실행: 저장소 루트에서 `python -m fake.run [--offline] [--both]`.
의존: `app`, `fake_db`, `mpilot.db.vectordb`, `mpilot.rag.generator`.
소유: UI·총괄 파트
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from dotenv import dotenv_values

_CUSTOMER_PORT = 7860  # app_customer.py와 같은 포트
_ENGINEER_PORT = 7861  # app_engineer.py와 같은 포트

# 앱이 쓰지 않지만 load_config가 요구하는 값
_DUMMY_ENV = {
    "MYSQL_USER": "fake",
    "MYSQL_PASSWORD": "fake",
    "MYSQL_HOST": "localhost",
    "MYSQL_DATABASE": "fake",
    "DEMO_CUSTOMER_ID": "customer",
    "DEMO_CUSTOMER_PASSWORD": "fake",
    "DEMO_ENGINEER_ID": "engineer",
    "DEMO_ENGINEER_PASSWORD": "fake",
}
# 오프라인에서는 키·모델명·임계값도 더미다(실제 호출을 하지 않는다)
_OFFLINE_ENV = {
    "OPENAI_API_KEY": "offline",
    "OPENAI_CHAT_MODEL": "offline",
    "OPENAI_EMBEDDING_MODEL": "offline",
    "EVIDENCE_THRESHOLD": "0.5",
}


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """실행 인자를 읽는다."""
    parser = argparse.ArgumentParser(description="가짜 DB 위에서 M-Pilot 앱 실행")
    parser.add_argument("--offline", action="store_true", help="OpenAI 키 없이 해시 임베딩으로 배선만 확인한다")
    parser.add_argument("--both", action="store_true", help="고객 앱(7860)에 더해 엔지니어 앱(7861)도 띄운다")
    return parser.parse_args(argv)


def _set_dummy_env(offline: bool) -> None:
    """`.env`나 환경에 없는 값에만 더미를 채운다. 이미 있는 값(실제 키 포함)은 건드리지 않는다.

    `load_config`처럼 빈 값은 없는 값으로 본다(`.env.example`을 그대로 복사하면 `DEMO_*`가 비어 있다).

    Args:
        offline: True면 OpenAI 관련 값도 더미로 채운다.

    Returns:
        None: `os.environ`에 더미가 들어간다.
    """
    env_file = Path(".env")
    file_values = dotenv_values(env_file) if env_file.exists() else {}
    known = {key for key, value in (file_values | dict(os.environ)).items() if value}
    for key, value in (_DUMMY_ENV | (_OFFLINE_ENV if offline else {})).items():
        if key not in known:
            os.environ[key] = value


def main(argv: Sequence[str] | None = None) -> None:
    """가짜 DB를 끼우고 앱을 띄운다.

    Args:
        argv: 실행 인자. None이면 `sys.argv`를 쓴다.

    Returns:
        None: Gradio 서버가 끝날 때까지 돌아오지 않는다.
    """
    args = _parse_args(argv)
    _set_dummy_env(args.offline)

    # 환경 값을 채운 뒤에 불러온다(import 시점에 설정을 읽지는 않지만 순서를 고정해 둔다).
    import app as app_mod
    from fake import fake_db
    from mpilot.db import vectordb
    from mpilot.models import DependencyError, Role, ValidationError
    from mpilot.rag import generator

    fake_db.install()
    if args.offline:
        vectordb.connect = lambda _emb: fake_db.connect_vectordb(fake_db.HashEmbeddings())
        generator.set_chat_model = lambda _model: None

    try:
        app_mod.initialize()
    except (ValidationError, DependencyError) as e:
        # 예외 원문은 비밀 정보를 담을 수 있어 app.main처럼 고정 문구와 예외 이름만 출력한다.
        print(f"{app_mod.STARTUP_FAILED_MESSAGE} ({type(e).__name__})", file=sys.stderr)
        raise SystemExit(1) from None
    print("initialized: offline" if args.offline else "initialized: real OpenAI models", flush=True)
    app_mod.build_app(Role.CUSTOMER).launch(server_port=_CUSTOMER_PORT, prevent_thread_lock=args.both)
    if args.both:
        app_mod.build_app(Role.ENGINEER).launch(server_port=_ENGINEER_PORT)


if __name__ == "__main__":
    main()
