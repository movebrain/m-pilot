"""vectordb 연결·저장소·원문 조회(connect, get_store, get_document) 테스트.

OpenAI 대신 결정적 가짜 임베딩을 쓰고, Chroma는 메모리 클라이언트로 띄운다.
메모리 Chroma는 같은 이름의 컬렉션을 한 프로세스 안에서 공유하므로, 테스트마다 컬렉션을 지운다.
"""

from __future__ import annotations

import importlib
import sys
from datetime import UTC, datetime

import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding
from langchain_core.vectorstores import VectorStore

from mpilot.db import vectordb
from mpilot.models import (
    CaseDetail,
    CaseStatus,
    Chunk,
    CitationKind,
    DependencyError,
    Equipment,
    ErrorCode,
    NotFoundError,
    Resolution,
    Route,
    ServiceCase,
    Source,
    ValidationError,
)

MANUAL_ID = "manual:srm_manual:12:1"


@pytest.fixture
def connected(monkeypatch):
    """가짜 임베딩으로 연결하고, 끝나면 컬렉션을 지워 다음 테스트와 섞이지 않게 한다."""
    monkeypatch.setattr(vectordb, "_stores", {})
    vectordb.connect(DeterministicFakeEmbedding(size=16))
    yield
    for store in vectordb._stores.values():
        store.delete_collection()


def test_get_store_before_connect_is_dependency_error(monkeypatch):
    monkeypatch.setattr(vectordb, "_stores", {})

    with pytest.raises(DependencyError):
        vectordb.get_store(CitationKind.MANUAL)


def test_get_store_returns_separate_collections(connected):
    manual = vectordb.get_store(CitationKind.MANUAL)
    history = vectordb.get_store("history")  # 문자열 값도 받는다

    assert isinstance(manual, VectorStore)
    assert manual._collection.name == "manual"
    assert history._collection.name == "history"


def test_get_store_unknown_kind(connected):
    with pytest.raises(ValidationError):
        vectordb.get_store("photos")


def test_get_document_returns_text_and_metadata(connected):
    vectordb.get_store(CitationKind.MANUAL).add_texts(
        ["반사판을 마른 천으로 닦는다."],
        metadatas=[{"equipment": "SRM", "page": 12, "section": "11.1"}],
        ids=[MANUAL_ID],
    )

    doc = vectordb.get_document(CitationKind.MANUAL, MANUAL_ID)

    assert doc.id == MANUAL_ID
    assert doc.page_content == "반사판을 마른 천으로 닦는다."
    assert doc.metadata == {"equipment": "SRM", "page": 12, "section": "11.1"}


def test_get_document_missing_id(connected):
    with pytest.raises(NotFoundError):
        vectordb.get_document(CitationKind.MANUAL, "manual:없음:1:1")


def test_get_document_does_not_cross_collections(connected):
    vectordb.get_store(CitationKind.MANUAL).add_texts(["본문"], ids=[MANUAL_ID])

    with pytest.raises(NotFoundError):
        vectordb.get_document(CitationKind.HISTORY, MANUAL_ID)


def test_get_document_unknown_kind(connected):
    with pytest.raises(NotFoundError):
        vectordb.get_document("photos", MANUAL_ID)


def test_store_supports_equipment_filter(connected):
    store = vectordb.get_store(CitationKind.HISTORY)
    store.add_texts(
        ["SRM 이력", "AGV 이력"],
        metadatas=[{"equipment": "SRM"}, {"equipment": "AGV"}],
        ids=["history:SRM-H-0001", "history:AGV-H-0001"],
    )

    hits = store.similarity_search("이력", k=5, filter={"equipment": "AGV"})

    assert [h.id for h in hits] == ["history:AGV-H-0001"]


def test_connect_failure_is_dependency_error(monkeypatch):
    def boom(**_):
        raise RuntimeError("초기화 실패")

    monkeypatch.setattr("langchain_chroma.Chroma", boom)
    with pytest.raises(DependencyError):
        vectordb.connect(DeterministicFakeEmbedding(size=16))


def test_chroma_import_failure_is_dependency_error_not_import_error(monkeypatch):
    # sqlite3가 3.35 미만이면 langchain_chroma(chromadb)를 import하는 순간 실패한다.
    # 그래도 vectordb 모듈 import(= 앱 시작, --fake 포함)는 되고, connect에서만 DependencyError가 나야 한다.
    monkeypatch.setitem(sys.modules, "langchain_chroma", None)  # 이 상태에서 import하면 실패한다
    try:
        importlib.reload(vectordb)  # 모듈 맨 위에서 Chroma를 불러오면 여기서 실패한다
        with pytest.raises(DependencyError):
            vectordb.connect(DeterministicFakeEmbedding(size=16))
    finally:
        monkeypatch.undo()
        importlib.reload(vectordb)  # 다음 테스트를 위해 원래 상태로 되돌린다


def test_get_document_after_rebuild_search_index(connected):
    # 색인 재생성(#55) 뒤 세 종류 문서의 원문을 ID로 꺼내고, 다른 설비 필터로는 검색되지 않는다.
    code = ErrorCode(
        equipment=Equipment.SRM, code="S101", name="X축 위치 편차 초과", severity="중",
        action_owner="고객사 조치", cause="원인", action="조치", manual_id="srm_manual", page=12,
        section="11.1", customer_procedure_ref_ids=[MANUAL_ID], required_safety_rules=[],
    )
    chunk = Chunk(manual_id="srm_manual", equipment=Equipment.SRM, page=12, section="11.1",
                  chunk_no=1, version="1.2", text="반사판을 마른 천으로 닦는다.")
    history = CaseDetail(
        service_case=ServiceCase(
            case_id="SRM-H-0001", equipment=Equipment.SRM, code="S101", memo=None,
            source=Source.SEED, status=CaseStatus.COMPLETED, route=Route.SUPPORT,
            guide_failure=None, guidance_answer=None,
            created_at=datetime(2026, 7, 1, 9, tzinfo=UTC),
            completed_at=datetime(2026, 7, 1, 10, tzinfo=UTC),
        ),
        error_code=code,
        resolution=Resolution(cause="센서 오염", action="센서 청소", result="정상 동작 확인"),
    )

    vectordb.rebuild_search_index([chunk], [code], [history])

    assert vectordb.get_document(CitationKind.MANUAL, MANUAL_ID).page_content == "반사판을 마른 천으로 닦는다."
    assert vectordb.get_document(CitationKind.MANUAL, "error:SRM:S101").id == "error:SRM:S101"
    assert vectordb.get_document(CitationKind.HISTORY, "history:SRM-H-0001").id == "history:SRM-H-0001"
    store = vectordb.get_store(CitationKind.MANUAL)
    assert store.similarity_search("반사판", k=5, filter={"equipment": "AGV"}) == []
