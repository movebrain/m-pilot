"""앱 설정: .env·환경 변수 로드와 검증, 설정 객체 정의.

역할:
    DB 접속 정보, OpenAI 키·모델 이름, 데이터 경로, 검색 파라미터, 데모 계정을
    앱 시작 때 한 번 읽어 `AppConfig`로 만든다(spec §2.4.2 설정).

책임:
    - `.env`와 환경 변수를 읽고 필수 값·형식을 검증한다. 틀리면 앱을 시작하지 않는다.
    - 비밀 값(`db_url`, `openai_api_key`, 데모 계정 비밀번호)은 `SecretStr`로 감싸 로그·화면에 새지 않게 한다.
    - 모듈을 import할 때는 파일을 읽거나 네트워크에 접속하지 않는다. `load_config()`를 부를 때만 읽는다.
    - 데모 계정 2개(고객·엔지니어)를 `.env`에서 읽는다. 실제 인증이 아니다.

의존: `models`만 import한다(의존 규칙 §2.5).
소유: 공통
"""

from __future__ import annotations

import io
import os
from pathlib import Path

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, SecretStr
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.engine import URL

from mpilot.models import DependencyError, Role, SearchSettings, ValidationError

_ROOT = Path(__file__).resolve().parent.parent


class DemoAccount(BaseModel):
    """데모 로그인 계정. 역할을 화면에서 나누기 위한 것이며 인증·권한 보안이 아니다.

    Attributes:
        login_id: 로그인 ID.
        password: 로그인 비밀번호.
        role: 이 계정이 여는 역할(`customer` 또는 `engineer`).
    """

    model_config = ConfigDict(frozen=True)

    login_id: str
    password: SecretStr
    role: Role


class AppConfig(BaseModel):
    """앱 설정. DB·모델·경로·검색 파라미터·데모 계정을 담는다.

    Attributes:
        db_url: 관계형 DB 접속 URL(비밀번호 포함).
        openai_api_key: OpenAI API 키.
        chat_model_name: 답변 생성에 쓸 채팅 모델 이름.
        embedding_model_name: 임베딩 모델 이름.
        manuals_dir: 매뉴얼 PDF 폴더.
        codes_path: 에러코드 CSV(`data/errorcodes.csv`) 경로.
        history_seed_path: 서비스 이력 시드 CSV(`data/history_seed.csv`) 경로.
        search: 엔지니어 검색 설정.
        llm_timeout_seconds: 모델 호출 제한 시간(초). 기본 60.
        demo_accounts: 로그인 ID를 키로 하는 데모 계정(고객·엔지니어 2개).
    """

    model_config = ConfigDict(frozen=True)

    db_url: SecretStr
    openai_api_key: SecretStr
    chat_model_name: str
    embedding_model_name: str
    manuals_dir: Path
    codes_path: Path
    history_seed_path: Path
    search: SearchSettings
    llm_timeout_seconds: float = 60
    demo_accounts: dict[str, DemoAccount]


def load_config(env_path: Path | None = None) -> AppConfig:
    """.env·환경 변수를 로드해 설정을 검증한다.

    Args:
        env_path: 읽을 `.env` 파일 경로. `None`이면 저장소 루트의 `.env`를 쓰고,
            환경 변수가 파일보다 우선하는지는 구현 때 정한다.

    Returns:
        AppConfig: 검증을 통과한 앱 설정.

    Raises:
        ValidationError: 필수 값이 없거나 형식이 틀릴 때(예: 데모 계정이 2개가 아님).
        DependencyError: `.env` 파일을 읽을 수 없을 때.

    환경 변수가 `.env` 파일보다 우선한다. `env_path`를 주지 않았는데 `.env`가 없으면
    환경 변수만 쓴다. 키 이름은 `.env.example`을 따른다.
    """
    path = env_path or _ROOT / ".env"
    file_values: dict[str, str | None] = {}
    if env_path is not None or path.exists():
        try:
            file_values = dotenv_values(stream=io.StringIO(path.read_text(encoding="utf-8")))
        except OSError as e:
            raise DependencyError(f".env 파일을 읽을 수 없습니다: {path}") from e
    env = {k: v for k, v in {**file_values, **os.environ}.items() if v}

    def need(key: str) -> str:
        if key not in env:
            raise ValidationError(f"필수 설정 {key}이(가) 없습니다.")
        return env[key]

    try:
        db_url = URL.create(
            "mysql+pymysql",
            username=need("MYSQL_USER"),
            password=need("MYSQL_PASSWORD"),
            host=need("MYSQL_HOST"),
            port=int(env.get("MYSQL_PORT", "3306")),
            database=need("MYSQL_DATABASE"),
        ).render_as_string(hide_password=False)
        accounts = [
            DemoAccount(
                login_id=need(f"DEMO_{name}_ID"),
                password=SecretStr(need(f"DEMO_{name}_PASSWORD")),
                role=role,
            )
            for name, role in (("CUSTOMER", Role.CUSTOMER), ("ENGINEER", Role.ENGINEER))
        ]
        if accounts[0].login_id == accounts[1].login_id:
            raise ValidationError("데모 계정은 서로 다른 로그인 ID 2개여야 합니다.")
        return AppConfig(
            db_url=SecretStr(db_url),
            openai_api_key=SecretStr(need("OPENAI_API_KEY")),
            chat_model_name=need("OPENAI_CHAT_MODEL"),
            embedding_model_name=need("OPENAI_EMBEDDING_MODEL"),
            manuals_dir=_ROOT / "data" / "manuals",
            codes_path=_ROOT / "data" / "errorcodes.csv",
            history_seed_path=_ROOT / "data" / "history_seed.csv",
            search=SearchSettings(
                manual_k=int(env.get("MANUAL_K", "3")),
                history_k=int(env.get("HISTORY_K", "3")),
                evidence_threshold=float(need("EVIDENCE_THRESHOLD")),
            ),
            llm_timeout_seconds=float(env.get("LLM_TIMEOUT_SECONDS", "60")),
            demo_accounts={a.login_id: a for a in accounts},
        )
    except ValueError as e:
        # pydantic 오류 문구는 입력값(비밀번호 등)을 담을 수 있어 필드 위치만 알린다.
        if isinstance(e, PydanticValidationError):
            fields = ", ".join(".".join(map(str, err["loc"])) for err in e.errors())
            raise ValidationError(f"설정 값이 올바르지 않습니다: {fields}") from None
        raise ValidationError(f"설정 값의 형식이 올바르지 않습니다: {e}") from None
