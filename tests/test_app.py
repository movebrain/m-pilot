"""앱 시작 순서·시작 실패 처리·화면 조립 테스트(CHG-01)."""

from __future__ import annotations

import pytest

import app
from mpilot.models import DependencyError, Role, ValidationError
from mpilot.ui import common


@pytest.mark.parametrize("exc_type", [ValidationError, DependencyError])
def test_startup_failure_exits_without_launch(monkeypatch, capsys, exc_type):
    def fail():
        raise exc_type("비밀번호=secret 가 포함된 원문")

    launched = []
    monkeypatch.setattr(app, "initialize", fail)
    monkeypatch.setattr(app, "build_app", lambda role: launched.append(role))

    with pytest.raises(SystemExit) as info:
        app.main(Role.CUSTOMER, 7860, argv=[])

    assert info.value.code == 1
    assert launched == []  # 화면을 띄우지 않는다
    err = capsys.readouterr().err
    assert app.STARTUP_FAILED_MESSAGE in err
    assert "secret" not in err  # 예외 원문은 출력하지 않는다


def test_fake_mode_skips_initialize(monkeypatch):
    called = []

    class FakeDemo:
        def launch(self, server_port, css=None):
            called.append(("launch", server_port))

    monkeypatch.setattr(app, "initialize", lambda: called.append("initialize"))
    monkeypatch.setattr(app.fake_service, "install", lambda: called.append("install"))
    monkeypatch.setattr(app, "build_app", lambda role: FakeDemo())

    app.main(Role.ENGINEER, 7861, argv=["--fake"])

    assert called == ["install", ("launch", 7861)]


def test_initialize_runs_in_spec_order(monkeypatch):
    pytest.importorskip("langchain_openai")
    order = []

    class FakeSecret:
        def get_secret_value(self):
            return "x"

    class FakeConfig:
        db_url = FakeSecret()
        openai_api_key = FakeSecret()
        embedding_model_name = "emb"
        chat_model_name = "chat"
        llm_timeout_seconds = 60.0

    config = FakeConfig()
    monkeypatch.setattr(app, "load_config", lambda: order.append("load_config") or config)
    monkeypatch.setattr(app.service, "set_config", lambda c: order.append("set_config"))
    monkeypatch.setattr(app.rdb, "connect", lambda url: order.append("rdb.connect"))
    monkeypatch.setattr(app.vectordb, "connect", lambda emb: order.append("vectordb.connect"))
    monkeypatch.setattr(app.generator, "set_chat_model", lambda m: order.append("set_chat_model"))
    monkeypatch.setattr(app.service, "prepare_search_index", lambda: order.append("prepare_search_index"))

    app.initialize()

    assert order == [
        "load_config", "set_config", "rdb.connect", "vectordb.connect", "set_chat_model", "prepare_search_index",
    ]


@pytest.mark.parametrize("role", [Role.CUSTOMER, Role.ENGINEER])
def test_build_app_assembles_role_screens(monkeypatch, role):
    # --fake와 같은 조건으로 조립한다. 테스트가 끝나면 원래 service 함수로 되돌린다.
    for name in app.fake_service.public_function_names(app.service):
        monkeypatch.setattr(app.service, name, getattr(app.fake_service, name))
    demo = app.build_app(role)
    texts = [getattr(block, "value", None) for block in demo.blocks.values()]
    assert common.VIRTUAL_DATA_BANNER in texts
    states = [block.value for block in demo.blocks.values() if type(block).__name__ == "State"]
    assert role in states
    assert common.first_screen(role) in states
