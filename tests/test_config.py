"""load_config 테스트: 환경 변수 우선순위·필수 값·비밀 값 노출 방지."""

from pathlib import Path

import pytest

from mpilot.config import load_config
from mpilot.models import DependencyError, Role, ValidationError

ENV = {
    "MYSQL_HOST": "127.0.0.1",
    "MYSQL_PORT": "3307",
    "MYSQL_USER": "u",
    "MYSQL_PASSWORD": "p@ss/word",
    "MYSQL_DATABASE": "db",
    "OPENAI_API_KEY": "sk-test",
    "OPENAI_CHAT_MODEL": "chat",
    "OPENAI_EMBEDDING_MODEL": "embed",
    "DEMO_CUSTOMER_ID": "customer",
    "DEMO_CUSTOMER_PASSWORD": "c-pw",
    "DEMO_ENGINEER_ID": "engineer",
    "DEMO_ENGINEER_PASSWORD": "e-pw",
}


@pytest.fixture
def clean_env(monkeypatch):
    for key in [*ENV, "MANUAL_K", "HISTORY_K", "LLM_TIMEOUT_SECONDS"]:
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def write_env(tmp_path: Path, **overrides) -> Path:
    values = {**ENV, **overrides}
    path = tmp_path / ".env"
    path.write_text("\n".join(f"{k}={v}" for k, v in values.items()), encoding="utf-8")
    return path


def test_loads_values_and_defaults(clean_env, tmp_path):
    config = load_config(write_env(tmp_path))

    assert config.db_url.get_secret_value() == "mysql+pymysql://u:p%40ss%2Fword@127.0.0.1:3307/db"
    assert config.search.manual_k == 3
    assert config.search.history_k == 3
    assert config.llm_timeout_seconds == 60
    assert config.demo_accounts["customer"].role is Role.CUSTOMER
    assert config.demo_accounts["engineer"].role is Role.ENGINEER


def test_environment_variable_overrides_file(clean_env, tmp_path):
    clean_env.setenv("OPENAI_CHAT_MODEL", "from-env")

    assert load_config(write_env(tmp_path)).chat_model_name == "from-env"


def test_secrets_are_masked(clean_env, tmp_path):
    text = repr(load_config(write_env(tmp_path)))

    for secret in ("sk-test", "c-pw", "e-pw", "p@ss"):
        assert secret not in text


def test_missing_required_value(clean_env, tmp_path):
    path = write_env(tmp_path, OPENAI_API_KEY="")

    with pytest.raises(ValidationError, match="OPENAI_API_KEY"):
        load_config(path)


def test_demo_accounts_must_be_two_distinct_ids(clean_env, tmp_path):
    path = write_env(tmp_path, DEMO_ENGINEER_ID="customer")

    with pytest.raises(ValidationError, match="2개"):
        load_config(path)


def test_bad_number_format(clean_env, tmp_path):
    path = write_env(tmp_path, MANUAL_K="abc")

    with pytest.raises(ValidationError, match="형식"):
        load_config(path)


def test_unreadable_env_file(clean_env, tmp_path):
    with pytest.raises(DependencyError):
        load_config(tmp_path / "missing.env")
