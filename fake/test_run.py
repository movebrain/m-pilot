"""fake.run의 더미 환경 변수 채우기 테스트."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from fake import run
from mpilot.config import load_config


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, str]:
    """빈 환경과 빈 작업 폴더. `os.environ` 자체는 건드리지 않고 복사본으로 바꾼다."""
    fake_environ: dict[str, str] = {}
    monkeypatch.setattr(os, "environ", fake_environ)
    monkeypatch.chdir(tmp_path)
    return fake_environ


def test_offline_dummies_satisfy_load_config(env: dict[str, str], tmp_path: Path) -> None:
    empty_env_file = tmp_path / "empty.env"  # 저장소 루트의 진짜 .env가 섞이지 않게 한다
    empty_env_file.write_text("", encoding="utf-8")

    run._set_dummy_env(offline=True)

    load_config(env_path=empty_env_file)  # 필수 값이 모두 채워졌으면 예외가 없다


def test_real_value_in_environment_is_kept(env: dict[str, str]) -> None:
    env["OPENAI_API_KEY"] = "sk-real"
    env["MYSQL_USER"] = "real"

    run._set_dummy_env(offline=True)

    assert env["OPENAI_API_KEY"] == "sk-real"
    assert env["MYSQL_USER"] == "real"


def test_real_mode_does_not_fill_openai_values(env: dict[str, str]) -> None:
    run._set_dummy_env(offline=False)

    assert "OPENAI_API_KEY" not in env
    assert env["MYSQL_USER"] == "fake"


def test_empty_value_in_env_file_counts_as_missing(env: dict[str, str], tmp_path: Path) -> None:
    # .env.example을 그대로 복사하면 DEMO_*가 비어 있다. load_config는 빈 값을 없는 값으로 본다.
    (tmp_path / ".env").write_text("DEMO_CUSTOMER_ID=\nMYSQL_USER=from-file\n", encoding="utf-8")

    run._set_dummy_env(offline=False)

    assert env["DEMO_CUSTOMER_ID"] == "customer"
    assert "MYSQL_USER" not in env  # 파일에 값이 있으면 더미로 가리지 않는다
