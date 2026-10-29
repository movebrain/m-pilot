"""fake_db(DB 파트 대체)의 rdb·vectordb·parser 동작 테스트."""

from __future__ import annotations

import math
from datetime import timedelta
from pathlib import Path

import pytest

from fake import fake_db
from mpilot import service
from mpilot.config import AppConfig
from mpilot.db import parser, rdb, vectordb
from mpilot.models import (
    ActionOwner,
    Answer,
    AnswerStep,
    CaseInput,
    CaseStatus,
    Citation,
    CitationKind,
    DependencyError,
    Equipment,
    InvalidStateError,
    NotFoundError,
    Resolution,
    SearchSettings,
    Source,
    ValidationError,
    Verdict,
)
from mpilot.rag import retriever


def _input(code: str = "M401", memo: str | None = "증상") -> CaseInput:
    return CaseInput(equipment=Equipment.MINI_LOADER, code=code, memo=memo)


def _answered() -> Answer:
    step = AnswerStep(
        text="점검한다",
        citations=[Citation(kind=CitationKind.MANUAL, ref_id="manual:x:1:1", locator="x 1쪽")],
    )
    return Answer(verdict=Verdict.ANSWERED, steps=[step], notice=None)


def _hold() -> Answer:
    return Answer(verdict=Verdict.INSUFFICIENT_EVIDENCE, steps=[], notice="근거 부족")


def _resolution(cause: str = "원인") -> Resolution:
    return Resolution(cause=cause, action="조치", result="결과")


def _config() -> AppConfig:
    return AppConfig(
        db_url="mysql+pymysql://u:p@localhost/db",
        openai_api_key="key",
        chat_model_name="chat",
        embedding_model_name="emb",
        manuals_dir=Path("data/manuals"),
        codes_path=Path("data/errorcodes.csv"),
        history_seed_path=Path("data/history_seed.csv"),
        search=SearchSettings(manual_k=3, history_k=3, evidence_threshold=0.5),
        demo_accounts={},
    )


@pytest.fixture
def db(monkeypatch, tmp_path):
    """빈 매뉴얼 폴더를 가리키는 새 가짜 DB. PDF를 읽지 않아 빠르다."""
    monkeypatch.setattr(fake_db, "_MANUALS_DIR", tmp_path)
    fake_db.connect("fake://")


@pytest.fixture
def installed():
    """`install()`로 모듈 함수를 바꿔 끼우고, 끝나면 원래 함수로 되돌린다."""
    saved = [(module, name, getattr(module, name)) for module, fakes in fake_db._TARGETS for name in fakes]
    fake_db.install()
    yield
    for module, name, original in saved:
        setattr(module, name, original)


# ---------------------------------------------------------------------------
# rdb: 시드·조회
# ---------------------------------------------------------------------------


def test_connect_seeds_200_resolved_cases(db):
    resolved = fake_db.fetch_resolved_cases()
    assert len(resolved) == 200
    assert all(c.resolution is not None and c.service_case.source is Source.SEED for c in resolved)


def test_seed_times_are_utc_aware(db):
    case = fake_db.fetch_resolved_cases()[0].service_case
    assert case.created_at.utcoffset() == timedelta(0)
    assert case.completed_at is not None and case.completed_at >= case.created_at


def test_connect_again_resets_reported_cases(db):
    fake_db.save_support_case("SC-aaaa0001", _input(), None)
    fake_db.connect("fake://")
    assert fake_db.fetch_cases(source=Source.REPORTED) == []


def test_fetch_error_codes_filters_by_equipment(db):
    codes = fake_db.fetch_error_codes(Equipment.AGV)
    assert codes and {c.equipment for c in codes} == {Equipment.AGV}


def test_fetch_error_codes_rejects_unknown_equipment(db):
    with pytest.raises(ValidationError):
        fake_db.fetch_error_codes("Forklift")


def test_fetch_cases_is_newest_first_and_filters(db):
    fake_db.save_support_case("SC-aaaa0001", _input(), None)
    fake_db.save_support_case("SC-aaaa0002", _input(), None)
    reported = fake_db.fetch_cases(source=Source.REPORTED)
    assert [c.service_case.case_id for c in reported] == ["SC-aaaa0002", "SC-aaaa0001"]
    assert len(fake_db.fetch_cases(status=CaseStatus.RECEIVED)) == 2


def test_missing_case_raises_not_found(db):
    for call in (
        lambda: fake_db.fetch_case_detail("SC-none"),
        lambda: fake_db.save_chat_turn("SC-none", "질문", _hold()),
        lambda: fake_db.save_confirmed_resolution("SC-none", _resolution()),
    ):
        with pytest.raises(NotFoundError):
            call()


# ---------------------------------------------------------------------------
# rdb: 접수
# ---------------------------------------------------------------------------


def test_save_support_case_then_fetch_detail(db):
    saved = fake_db.save_support_case("SC-aaaa0001", _input(), None)
    assert saved.service_case.status is CaseStatus.RECEIVED
    assert saved.service_case.source is Source.REPORTED
    assert fake_db.fetch_case_detail("SC-aaaa0001") == saved


def test_save_case_rejects_unregistered_code(db):
    with pytest.raises(ValidationError):
        fake_db.save_support_case("SC-aaaa0001", _input(code="ZZZ999"), None)


def test_save_case_with_duplicate_id_raises_dependency_error(db):
    fake_db.save_support_case("SC-aaaa0001", _input(), None)
    with pytest.raises(DependencyError):
        fake_db.save_support_case("SC-aaaa0001", _input(), None)


def test_save_self_guide_case_is_completed_without_resolution(db):
    saved = fake_db.save_self_guide_case("SC-aaaa0001", _input(), _answered())
    case = saved.service_case
    assert case.status is CaseStatus.COMPLETED and case.guidance_answer == _answered()
    assert saved.resolution is None
    assert case.completed_at is not None and case.completed_at >= case.created_at


def test_save_self_guide_case_requires_answered_guidance(db):
    with pytest.raises(ValidationError):
        fake_db.save_self_guide_case("SC-aaaa0001", _input(), _hold())


# ---------------------------------------------------------------------------
# rdb: 대화·조치
# ---------------------------------------------------------------------------


def test_first_chat_turn_moves_case_to_in_progress(db):
    fake_db.save_support_case("SC-aaaa0001", _input(), None)
    first = fake_db.save_chat_turn("SC-aaaa0001", " 질문1 ", _answered())
    second = fake_db.save_chat_turn("SC-aaaa0001", "질문2", _answered())
    assert (first.question, second.turn_id > first.turn_id) == ("질문1", True)
    assert fake_db.fetch_case_detail("SC-aaaa0001").service_case.status is CaseStatus.IN_PROGRESS
    assert [t.question for t in fake_db.fetch_chat_turns("SC-aaaa0001")] == ["질문1", "질문2"]


def test_chat_turn_keeps_hold_answer(db):
    fake_db.save_support_case("SC-aaaa0001", _input(), None)
    fake_db.save_chat_turn("SC-aaaa0001", "질문", _hold())
    assert fake_db.fetch_chat_turns("SC-aaaa0001")[0].answer == _hold()


@pytest.mark.parametrize("question", ["", "   ", "가" * 2001])
def test_chat_turn_rejects_blank_and_too_long_question(db, question):
    fake_db.save_support_case("SC-aaaa0001", _input(), None)
    with pytest.raises(ValidationError):
        fake_db.save_chat_turn("SC-aaaa0001", question, _hold())


def test_chat_turn_on_self_guide_case_raises_invalid_state(db):
    fake_db.save_self_guide_case("SC-aaaa0001", _input(), _answered())
    with pytest.raises(InvalidStateError):
        fake_db.save_chat_turn("SC-aaaa0001", "질문", _hold())


def test_confirm_resolution_completes_case_and_lists_it(db):
    fake_db.save_support_case("SC-aaaa0001", _input(), None)
    done = fake_db.save_confirmed_resolution("SC-aaaa0001", _resolution())
    assert done.service_case.status is CaseStatus.COMPLETED and done.resolution == _resolution()
    assert done.service_case.completed_at >= done.service_case.created_at
    assert "SC-aaaa0001" in {c.service_case.case_id for c in fake_db.fetch_resolved_cases()}


def test_confirm_resolution_twice_raises_invalid_state(db):
    fake_db.save_support_case("SC-aaaa0001", _input(), None)
    fake_db.save_confirmed_resolution("SC-aaaa0001", _resolution())
    with pytest.raises(InvalidStateError):
        fake_db.save_confirmed_resolution("SC-aaaa0001", _resolution("다른 원인"))
    with pytest.raises(InvalidStateError):
        fake_db.save_chat_turn("SC-aaaa0001", "질문", _hold())


# ---------------------------------------------------------------------------
# 임베딩·설치
# ---------------------------------------------------------------------------


def test_hash_embeddings_are_deterministic_and_unit_length():
    emb = fake_db.HashEmbeddings()
    vec = emb.embed_query("주행 인버터 과전류")
    assert vec == emb.embed_documents(["주행 인버터 과전류"])[0]
    assert math.isclose(sum(x * x for x in vec), 1.0, rel_tol=1e-9)


def test_install_swaps_db_functions(installed):
    assert rdb.fetch_cases is fake_db.fetch_cases
    assert parser.parse_manuals is fake_db.parse_manuals
    assert vectordb.get_store.__module__ == "mpilot.db.vectordb"  # 벡터 저장소는 바꾸지 않는다


# ---------------------------------------------------------------------------
# 실제 매뉴얼 PDF + 가짜 저장소 + 진짜 retriever/vectordb 색인 코드
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def indexed():
    """실제 PDF를 파싱해 색인까지 만든 가짜 DB(20초 안팎이라 모듈에서 한 번만)."""
    saved = [(module, name, getattr(module, name)) for module, fakes in fake_db._TARGETS for name in fakes]
    fake_db.install()
    try:
        rdb.connect("fake://")
        vectordb.connect(fake_db.HashEmbeddings())
        service.set_config(_config())
        service.prepare_search_index()
        yield
    finally:
        for module, name, original in saved:
            setattr(module, name, original)


def test_manual_search_is_filtered_by_equipment_and_sorted(indexed):
    for equipment in Equipment:
        hits = retriever.search_manual(equipment, "주행 인버터 과전류 점검", 5)
        assert hits and {h.equipment for h in hits} == {equipment}
        assert [h.distance for h in hits] == sorted(h.distance for h in hits)


def test_history_search_returns_seed_history_of_same_equipment(indexed):
    hits = retriever.search_history(Equipment.RTV, "주행 휠 스트레치 필름", 3)
    assert hits and all(h.kind is CitationKind.HISTORY and h.equipment is Equipment.RTV for h in hits)


def test_confirmed_case_becomes_searchable_once(indexed):
    store = vectordb.get_store(CitationKind.HISTORY)
    before = len(store.get()["ids"])
    fake_db.save_support_case("SC-index001", CaseInput(equipment=Equipment.RTV, code="R201", memo="증상"), None)
    done = fake_db.save_confirmed_resolution("SC-index001", _resolution("유니크원인XYZ"))
    vectordb.index_history(done)
    vectordb.index_history(done)  # 같은 case_id는 덮어쓴다
    assert len(store.get()["ids"]) == before + 1
    assert retriever.search_history(Equipment.RTV, "유니크원인XYZ", 1)[0].ref_id == "history:SC-index001"
