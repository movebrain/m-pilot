"""service의 설정 보관·검색 색인 준비 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest

from mpilot import service
from mpilot.config import AppConfig
from mpilot.models import (
    ActionOwner,
    Chunk,
    DependencyError,
    Equipment,
    ErrorCode,
    SearchSettings,
    Severity,
)


def _config() -> AppConfig:
    return AppConfig(
        db_url="mysql+pymysql://u:p@localhost/db",
        openai_api_key="key",
        chat_model_name="chat",
        embedding_model_name="emb",
        manuals_dir=Path("data/manuals"),
        codes_path=Path("data/errorcodes.csv"),
        history_seed_path=Path("data/history_seed.csv"),
        search=SearchSettings(manual_k=3, history_k=3),
        demo_accounts={},
    )


def _chunk(page: int, chunk_no: int) -> Chunk:
    return Chunk(
        manual_id="agv_rev1_1", equipment=Equipment.AGV, page=page, section="2.4 배터리",
        chunk_no=chunk_no, version="1.1", text="본문",
    )


def _code() -> ErrorCode:
    return ErrorCode(
        equipment=Equipment.AGV, code="A-120", name="배터리 저전압", severity=Severity.LOW,
        action_owner=ActionOwner.CUSTOMER, cause="충전 부족", action="충전",
        manual_id="agv_rev1_1", page=8, section="2.4 배터리",
    )


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(service, "_config", None)
    service.set_config(_config())


@pytest.fixture
def backend(monkeypatch):
    """DB·파서·저장소를 가짜로 바꾸고 호출 기록을 돌려준다."""
    calls: dict = {}
    codes_by_equipment = {Equipment.AGV: [_code()]}

    def parse_manuals(directory):
        calls["dir"] = directory
        return [_chunk(8, 2)]

    def rebuild(chunks, codes, histories):
        calls["rebuild"] = (chunks, codes, histories)

    monkeypatch.setattr(service.parser, "parse_manuals", parse_manuals)
    monkeypatch.setattr(service.rdb, "fetch_error_codes", lambda e: codes_by_equipment.get(e, []))
    monkeypatch.setattr(service.rdb, "fetch_resolved_cases", lambda: [])
    monkeypatch.setattr(service.vectordb, "rebuild_search_index", rebuild)
    calls["codes"] = codes_by_equipment
    return calls


def test_prepare_search_index_requires_config(monkeypatch):
    monkeypatch.setattr(service, "_config", None)
    with pytest.raises(DependencyError):
        service.prepare_search_index()


def test_prepare_search_index_rebuilds_with_all_sources(configured, backend):
    service.prepare_search_index()

    assert backend["dir"] == Path("data/manuals")  # 설정의 매뉴얼 폴더를 쓴다
    chunks, codes, histories = backend["rebuild"]
    assert [c.chunk_no for c in chunks] == [2]
    assert [c.code for c in codes] == ["A-120"]
    assert histories == []


def test_login_removed():
    assert not hasattr(service, "login")  # CHG-01
