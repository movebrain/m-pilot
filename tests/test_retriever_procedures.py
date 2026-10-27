"""고객 허용 절차 직접 조회 테스트(`fetch_customer_procedures`, `_to_hit`)."""

import pytest
from langchain_core.documents import Document

from mpilot.db import vectordb
from mpilot.models import (
    ActionOwner,
    CitationKind,
    DependencyError,
    Equipment,
    ErrorCode,
    NotFoundError,
    Severity,
)
from mpilot.rag import retriever
from mpilot.rag.retriever import _to_hit, fetch_customer_procedures


def make_code(refs=("manual:agv:1:1",)) -> ErrorCode:
    return ErrorCode(
        equipment=Equipment.AGV,
        code="R001",
        name="테스트",
        severity=Severity.LOW,
        action_owner=ActionOwner.CUSTOMER,
        cause="원인",
        action="조치",
        manual_id="agv",
        page=1,
        section="1",
        customer_procedure_ref_ids=list(refs),
    )


def make_doc(ref_id: str, text: str = "원문", **meta) -> Document:
    metadata = {"equipment": "AGV", "manual_id": "agv", "page": 3, "section": "2.1"}
    metadata.update(meta)
    return Document(id=ref_id, page_content=text, metadata=metadata)


@pytest.fixture
def store(monkeypatch):
    """딕셔너리 기반 가짜 저장소. 검색·`get_store`가 불리면 실패한다."""
    docs: dict[str, Document] = {}

    def fake_get_document(kind, ref_id):
        assert kind is CitationKind.MANUAL
        if ref_id not in docs:
            raise NotFoundError(ref_id)
        return docs[ref_id]

    def must_not_call(*args, **kwargs):
        raise AssertionError("직접 조회에서 검색 저장소를 부르면 안 된다")

    monkeypatch.setattr(vectordb, "get_document", fake_get_document)
    monkeypatch.setattr(vectordb, "get_store", must_not_call)
    monkeypatch.setattr(retriever, "search_manual", must_not_call)
    monkeypatch.setattr(retriever, "search_history", must_not_call)
    return docs


def test_returns_only_allowed_documents_without_distance(store):
    store["manual:agv:3:1"] = make_doc("manual:agv:3:1", "첫째 절차")
    store["manual:agv:3:2"] = make_doc("manual:agv:3:2", "둘째 절차")
    store["manual:agv:3:3"] = make_doc("manual:agv:3:3", "허용 목록에 없는 문서")

    hits = fetch_customer_procedures(make_code(refs=("manual:agv:3:2", "manual:agv:3:1")))

    assert [h.ref_id for h in hits] == ["manual:agv:3:2", "manual:agv:3:1"]
    assert [h.text for h in hits] == ["둘째 절차", "첫째 절차"]
    assert all(h.kind is CitationKind.MANUAL for h in hits)
    assert all(h.distance is None for h in hits)


def test_returns_empty_list_when_no_allowed_ids(store):
    assert fetch_customer_procedures(make_code(refs=())) == []


def test_missing_document_raises_dependency_error(store):
    store["manual:agv:3:1"] = make_doc("manual:agv:3:1")

    with pytest.raises(DependencyError) as info:
        fetch_customer_procedures(make_code(refs=("manual:agv:3:1", "manual:agv:9:9")))

    assert isinstance(info.value.__cause__, NotFoundError)
    assert "manual:agv:9:9" in str(info.value)


def test_store_dependency_error_propagates(monkeypatch):
    error = DependencyError("저장소 장애")

    def failing_get_document(kind, ref_id):
        raise error

    monkeypatch.setattr(vectordb, "get_document", failing_get_document)

    with pytest.raises(DependencyError) as info:
        fetch_customer_procedures(make_code())

    assert info.value is error


def test_to_hit_manual_body():
    hit = _to_hit(make_doc("manual:agv:3:1", "본문"), None)

    assert hit.ref_id == "manual:agv:3:1"
    assert hit.kind is CitationKind.MANUAL
    assert hit.equipment is Equipment.AGV
    assert hit.code is None
    assert hit.text == "본문"
    assert hit.locator == "agv 3쪽 2.1"
    assert hit.distance is None


def test_to_hit_history_keeps_locator_and_distance():
    doc = Document(
        id="history:SC-00000001",
        page_content="조치 내용",
        metadata={"equipment": "SRM", "code": "S112", "case_id": "SC-00000001"},
    )

    hit = _to_hit(doc, 0.25)

    assert hit.kind is CitationKind.HISTORY
    assert hit.equipment is Equipment.SRM
    assert hit.code == "S112"
    assert hit.locator == "접수 SC-00000001 S112"
    assert hit.distance == 0.25


@pytest.mark.parametrize("missing", ["equipment", "manual_id", "page", "section"])
def test_to_hit_missing_metadata_raises_dependency_error(missing):
    doc = make_doc("manual:agv:3:1")
    del doc.metadata[missing]

    with pytest.raises(DependencyError):
        _to_hit(doc, None)


def test_to_hit_invalid_equipment_raises_dependency_error():
    with pytest.raises(DependencyError):
        _to_hit(make_doc("manual:agv:3:1", equipment="Forklift"), None)


def test_to_hit_missing_doc_id_raises_dependency_error():
    with pytest.raises(DependencyError):
        _to_hit(Document(page_content="본문", metadata={"equipment": "AGV"}), None)
