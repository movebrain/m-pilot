"""가짜 DB 위에서 고객(7860)·엔지니어(7861) 앱을 한 프로세스로 띄우는 수동 테스트용 진입점.

역할:
    `fake_db.install()`로 `rdb`·`parser`만 메모리 구현으로 바꾸고, 나머지(`service`·`rag`·`ui`)는
    진짜 코드를 그대로 띄운다. MySQL과 docker 없이 UI에서 RAG까지 눈으로 확인하기 위한 도구다.

책임:
    - 기본(실제 모드): 루트 `.env`의 OpenAI 값으로 임베딩·LLM을 쓴다. 키가 없으면 앱이 시작되지 않는다.
    - `--offline`: 키 없이 배선만 확인한다. 해시 임베딩을 쓰고 LLM은 연결하지 않아, 고객 안내 생성은
      `DependencyError`가 되어 엔지니어 접수(`generation_failed`)로 떨어진다. 오류가 아니라 정해진 동작이다.
    - `--both`: 엔지니어 앱(7861)도 같은 프로세스에 띄운다. 없으면 고객 앱(7860)만 띄운다.
    - `--evidence-threshold T`: 엔지니어 검색 근거 충분성 임계값(`EVIDENCE_THRESHOLD`)을 T로 정한다. 없으면 프로세스
      환경변수 값, 그것도 없으면 1.0이다. 환경변수가 `.env`보다 우선하므로 `.env`에 적은 값은 쓰이지 않는다.
      1.0은 가짜 DB 청크 기준으로 잰 수동 테스트용 임시 값이며 평가(#49)로 정한 값이 아니다.
    - 앱이 쓰지 않는 MySQL·데모 계정 값은 `load_config`가 요구하므로, `.env`나 환경에 없을 때만 더미를 둔다.

실행: 저장소 루트에서 `python -m fake.run [--offline] [--both] [--evidence-threshold T]`.
의존: `app`, `fake_db`, `mpilot.db.vectordb`, `mpilot.rag.generator`.
소유: UI·총괄 파트
"""

from __future__ import annotations

import argparse
import math
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
# 오프라인에서는 키·모델명도 더미다(실제 호출을 하지 않는다)
_OFFLINE_ENV = {
    "OPENAI_API_KEY": "offline",
    "OPENAI_CHAT_MODEL": "offline",
    "OPENAI_EMBEDDING_MODEL": "offline",
}
# 실제 임베딩 기준 검색 거리는 관련 질문이 0.75~1.22, 무관·교차 설비 질문이 1.00 이상이었다(가짜 DB 청크, 질문 19개).
# 1.0은 그 표본에서 무관 질문을 통과시키지 않는 가장 높은 값이다. 평가(#49)로 정한 값이 아니다.
_DEFAULT_EVIDENCE_THRESHOLD = 1.0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """실행 인자를 읽는다."""
    parser = argparse.ArgumentParser(description="가짜 DB 위에서 M-Pilot 앱 실행")
    parser.add_argument("--offline", action="store_true", help="OpenAI 키 없이 해시 임베딩으로 배선만 확인한다")
    parser.add_argument("--both", action="store_true", help="고객 앱(7860)에 더해 엔지니어 앱(7861)도 띄운다")
    parser.add_argument(
        "--evidence-threshold", type=float, default=None,
        help=f"엔지니어 검색 근거 충분성 임계값(0 이상). 없으면 환경변수 EVIDENCE_THRESHOLD, 그것도 없으면 {_DEFAULT_EVIDENCE_THRESHOLD}",
    )
    args = parser.parse_args(argv)
    if args.evidence_threshold is not None and not (math.isfinite(args.evidence_threshold) and args.evidence_threshold >= 0):
        parser.error("--evidence-threshold는 0 이상의 유한한 실수여야 한다")
    return args


def _apply_evidence_threshold(threshold: float | None) -> str:
    """`EVIDENCE_THRESHOLD` 환경변수를 정한다. 옵션 > 이미 있는 환경변수 > 기본값 순이다.

    환경변수가 `.env`보다 우선하므로 이 값을 정하면 `.env`에 적힌 값은 쓰이지 않는다(`load_config` 규칙).

    Args:
        threshold: `--evidence-threshold` 값. 없으면 None.

    Returns:
        str: 환경변수에 들어간 값(시작 로그에 보여 주기 위함).
    """
    if threshold is not None:
        value = str(threshold)
    else:
        value = os.environ.get("EVIDENCE_THRESHOLD") or str(_DEFAULT_EVIDENCE_THRESHOLD)
    os.environ["EVIDENCE_THRESHOLD"] = value
    return value


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
    evidence_threshold = _apply_evidence_threshold(args.evidence_threshold)

    # 환경 값을 채운 뒤에 불러온다(import 시점에 설정을 읽지는 않지만 순서를 고정해 둔다).
    import app as app_mod
    from fake import fake_db
    from mpilot.db import vectordb
    from mpilot.models import DependencyError, Role, ValidationError
    from mpilot.rag import generator

    _real_connect = vectordb.connect
    fake_db.install()
    if args.offline:
        vectordb.connect = lambda _emb: _real_connect(fake_db.HashEmbeddings())
        generator.set_chat_model = lambda _model: None

    try:
        app_mod.initialize()
    except (ValidationError, DependencyError) as e:
        # 예외 원문은 비밀 정보를 담을 수 있어 app.main처럼 고정 문구와 예외 이름만 출력한다.
        print(f"{app_mod.STARTUP_FAILED_MESSAGE} ({type(e).__name__})", file=sys.stderr)
        raise SystemExit(1) from None
    mode = "offline" if args.offline else "real OpenAI models"
    print(f"initialized: {mode}, evidence_threshold={evidence_threshold}", flush=True)
    app_mod.build_app(Role.CUSTOMER).launch(server_port=_CUSTOMER_PORT, prevent_thread_lock=args.both)
    if args.both:
        app_mod.build_app(Role.ENGINEER).launch(server_port=_ENGINEER_PORT)


if __name__ == "__main__":
    main()
