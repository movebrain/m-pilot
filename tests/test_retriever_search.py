"""설비 필터 검색 테스트(retriever.search_manual, search_history). 가짜 저장소로 DB 파트 없이 검증한다."""

import pytest
from langchain_core.documents import Document

from mpilot.db import vectordb
from mpilot.models import (
    CitationKind,
    DependencyError,
    Equipment,
    ValidationError,
)
from mpilot.rag import retriever

NAME = "주행 브레이크 이상"


NAME_VECTOR = [float(len(NAME))]  # FakeEmbeddings가 NAME으로 만드는 벡터


class FakeEmbeddings:
    """`embed_query` 호출을 기록하고, 글자 수로 정한 벡터를 돌려주는 가짜 임베딩."""

    def __init__(self):
        self.queries: list[str] = []

    def embed_query(self, text):
        self.queries.append(text)
        return [float(len(text))]


class FlakyEmbeddings(FakeEmbeddings):
    """첫 호출만 실패하는 가짜 임베딩."""

    def embed_query(self, text):
        first = not self.queries
        vector = super().embed_query(text)
        if first:
            raise RuntimeError("임베딩 API 장애")
        return vector


class FakeStore:
    """메타데이터 동등 필터를 실제로 적용하고 미리 정한 거리를 돌려주는 가짜 저장소."""

    def __init__(self, scored: list[tuple[Document, float]], embeddings: FakeEmbeddings | None = None):
        self.scored = scored
        self.embeddings = embeddings or FakeEmbeddings()
        self.calls: list[dict] = []

    def similarity_search_by_vector_with_relevance_scores(self, embedding, k=4, filter=None):
        self.calls.append({"embedding": embedding, "k": k, "filter": filter})
        hits = [
            (doc, dist)
            for doc, dist in self.scored
            if all(doc.metadata.get(key) == value for key, value in (filter or {}).items())
        ]
        return hits[:k]  # 입력 순서를 그대로 둬 정렬은 retriever가 하는지 확인한다.


class BrokenStore:
    def __init__(self):
        self.embeddings = FakeEmbeddings()

    def similarity_search_by_vector_with_relevance_scores(self, embedding, k=4, filter=None):
        raise RuntimeError("검색 API 장애")


def error_row(equipment: Equipment, code: str) -> Document:
    return Document(
        id=f"error:{equipment.value}:{code}",
        page_content=f"{code} {NAME}",
        metadata={
            "equipment": equipment.value,
            "code": code,
            "manual_id": "spec",
            "page": 12,
            "section": "4.2",
        },
    )


def manual_body(equipment: Equipment, chunk_no: int) -> Document:
    return Document(
        id=f"manual:{equipment.value}-spec:7:{chunk_no}",
        page_content=f"{NAME} 점검 절차 {chunk_no}",
        metadata={
            "equipment": equipment.value,
            "manual_id": f"{equipment.value}-spec",
            "page": 7,
            "section": "5.1",
        },
    )


def history(equipment: Equipment, case_id: str, code: str) -> Document:
    return Document(
        id=f"history:{case_id}",
        page_content=f"{NAME} 조치 이력 {case_id}",
        metadata={"equipment": equipment.value, "code": code, "case_id": case_id},
    )


@pytest.fixture
def install_store(monkeypatch):
    """종류별 가짜 저장소를 `vectordb.get_store`에 꽂는다."""

    def install(manual=None, hist=None):
        stores = {CitationKind.MANUAL: manual, CitationKind.HISTORY: hist}
        monkeypatch.setattr(vectordb, "get_store", lambda kind: stores[kind])
        return stores

    return install


@pytest.fixture(autouse=True)
def reset_query_vector(monkeypatch):
    """직전 질의 벡터는 모듈 상태라 테스트마다 비운다."""
    monkeypatch.setattr(retriever, "_last_query_vector", None)


def manual_store(embeddings: FakeEmbeddings | None = None) -> FakeStore:
    return FakeStore(
        [
            (error_row(Equipment.MINI_LOADER, "M406"), 0.30),
            (error_row(Equipment.SRM, "S115"), 0.10),
            (error_row(Equipment.RTV, "R206"), 0.20),
            (manual_body(Equipment.SRM, 1), 0.15),
            (manual_body(Equipment.MINI_LOADER, 1), 0.25),
        ],
        embeddings,
    )


def history_store(embeddings: FakeEmbeddings | None = None) -> FakeStore:
    return FakeStore(
        [
            (history(Equipment.MINI_LOADER, "C-1", "M406"), 0.40),
            (history(Equipment.SRM, "C-2", "S115"), 0.05),
            (history(Equipment.RTV, "C-3", "R206"), 0.20),
            (history(Equipment.SRM, "C-4", "S115"), 0.30),
        ],
        embeddings,
    )


@pytest.mark.parametrize("equipment", [Equipment.MINI_LOADER, Equipment.SRM, Equipment.RTV])
def test_manual_returns_only_given_equipment(install_store, equipment):
    install_store(manual=manual_store())
    hits = retriever.search_manual(equipment, NAME, 5)
    assert hits
    assert {h.equipment for h in hits} == {equipment}


@pytest.mark.parametrize(
    "equipment, expected",
    [(Equipment.MINI_LOADER, {"C-1"}), (Equipment.SRM, {"C-2", "C-4"}), (Equipment.RTV, {"C-3"})],
)
def test_history_returns_only_given_equipment(install_store, equipment, expected):
    install_store(hist=history_store())
    hits = retriever.search_history(equipment, NAME, 5)
    assert {h.ref_id.removeprefix("history:") for h in hits} == expected


def test_filter_and_k_are_passed_to_store(install_store):
    store = manual_store()
    install_store(manual=store)
    retriever.search_manual(Equipment.SRM, NAME, 3)
    assert store.calls == [{"embedding": NAME_VECTOR, "k": 3, "filter": {"equipment": "SRM"}}]


def test_results_sorted_by_distance_and_mix_error_rows_with_body(install_store):
    install_store(manual=manual_store())
    hits = retriever.search_manual(Equipment.SRM, NAME, 5)
    assert [h.distance for h in hits] == [0.10, 0.15]
    assert [h.ref_id.split(":")[0] for h in hits] == ["error", "manual"]


def test_history_sorted_by_distance(install_store):
    install_store(hist=history_store())
    hits = retriever.search_history(Equipment.SRM, NAME, 5)
    assert [h.distance for h in hits] == [0.05, 0.30]


def test_history_returns_exactly_k(install_store):
    install_store(hist=history_store())
    assert len(retriever.search_history(Equipment.SRM, NAME, 2)) == 2


def test_no_match_returns_empty_list(install_store):
    install_store(manual=manual_store(), hist=history_store())
    assert retriever.search_manual(Equipment.AGV, NAME, 3) == []
    assert retriever.search_history(Equipment.AGV, NAME, 3) == []


@pytest.mark.parametrize("query", ["", "   ", "\n\t"])
@pytest.mark.parametrize("search", [retriever.search_manual, retriever.search_history])
def test_blank_query_is_validation_error(install_store, search, query):
    install_store(manual=manual_store(), hist=history_store())
    with pytest.raises(ValidationError):
        search(Equipment.SRM, query, 3)


@pytest.mark.parametrize("k", [0, -1])
@pytest.mark.parametrize("search", [retriever.search_manual, retriever.search_history])
def test_k_below_one_is_validation_error(install_store, search, k):
    install_store(manual=manual_store(), hist=history_store())
    with pytest.raises(ValidationError):
        search(Equipment.SRM, NAME, k)


@pytest.mark.parametrize("search", [retriever.search_manual, retriever.search_history])
def test_store_failure_becomes_dependency_error(install_store, search):
    install_store(manual=BrokenStore(), hist=BrokenStore())
    with pytest.raises(DependencyError) as info:
        search(Equipment.SRM, NAME, 3)
    assert isinstance(info.value.__cause__, RuntimeError)


@pytest.mark.parametrize("search", [retriever.search_manual, retriever.search_history])
def test_none_distance_becomes_dependency_error(install_store, search):
    install_store(
        manual=FakeStore([(manual_body(Equipment.SRM, 1), None), (manual_body(Equipment.SRM, 2), 0.2)]),
        hist=FakeStore([(history(Equipment.SRM, "C-1", "S115"), None), (history(Equipment.SRM, "C-2", "S115"), 0.2)]),
    )
    with pytest.raises(DependencyError) as info:
        search(Equipment.SRM, NAME, 3)
    assert isinstance(info.value.__cause__, TypeError)


@pytest.mark.parametrize("search", [retriever.search_manual, retriever.search_history])
def test_get_store_dependency_error_propagates(monkeypatch, search):
    error = DependencyError("connect 전에 호출했다")

    def fail(kind):
        raise error

    monkeypatch.setattr(vectordb, "get_store", fail)
    with pytest.raises(DependencyError) as info:
        search(Equipment.SRM, NAME, 3)
    assert info.value is error


@pytest.mark.parametrize("search", [retriever.search_manual, retriever.search_history])
def test_mpilot_error_from_store_is_not_converted(monkeypatch, search):
    error = ValidationError("지원하지 않는 종류")

    def fail(kind):
        raise error

    monkeypatch.setattr(vectordb, "get_store", fail)
    with pytest.raises(ValidationError) as info:
        search(Equipment.SRM, NAME, 3)
    assert info.value is error


def test_malformed_metadata_becomes_dependency_error(install_store):
    broken = Document(id="manual:x:1:1", page_content="본문", metadata={"equipment": "SRM"})
    install_store(manual=FakeStore([(broken, 0.1)]))
    with pytest.raises(DependencyError):
        retriever.search_manual(Equipment.SRM, NAME, 3)


def test_hit_fields_for_error_row_body_and_history(install_store):
    install_store(manual=manual_store(), hist=history_store())
    row, body = retriever.search_manual(Equipment.SRM, NAME, 5)
    assert (row.kind, row.code, row.locator) == (CitationKind.MANUAL, "S115", "spec 12쪽 4.2")
    assert row.text == f"S115 {NAME}"
    assert (body.kind, body.code, body.locator) == (CitationKind.MANUAL, None, "SRM-spec 7쪽 5.1")

    first = retriever.search_history(Equipment.SRM, NAME, 5)[0]
    assert (first.kind, first.code, first.locator) == (CitationKind.HISTORY, "S115", "접수 C-2 S115")
    assert first.distance == 0.05


def test_equipment가_None이면_필터_없이_4종_전체에서_찾는다(install_store):
    manual, hist = manual_store(), history_store()
    install_store(manual=manual, hist=hist)
    manual_hits = retriever.search_manual(None, NAME, 5)
    history_hits = retriever.search_history(None, NAME, 5)
    assert manual.calls == [{"embedding": NAME_VECTOR, "k": 5, "filter": None}]
    assert hist.calls == [{"embedding": NAME_VECTOR, "k": 5, "filter": None}]
    assert {h.equipment for h in manual_hits} == {Equipment.MINI_LOADER, Equipment.SRM, Equipment.RTV}
    assert {h.equipment for h in history_hits} == {Equipment.MINI_LOADER, Equipment.SRM, Equipment.RTV}


def test_같은_질의를_연달아_검색하면_임베딩은_한_번이다(install_store):
    emb = FakeEmbeddings()
    manual, hist = manual_store(emb), history_store(emb)  # 실제 앱처럼 두 저장소가 임베딩 객체를 공유한다
    install_store(manual=manual, hist=hist)
    retriever.search_manual(Equipment.SRM, NAME, 3)
    retriever.search_history(Equipment.SRM, NAME, 3)
    assert emb.queries == [NAME]
    assert manual.calls[0]["embedding"] == hist.calls[0]["embedding"] == NAME_VECTOR


def test_질의가_바뀌면_다시_임베딩한다(install_store):
    emb = FakeEmbeddings()
    install_store(manual=manual_store(emb), hist=history_store(emb))
    retriever.search_manual(Equipment.SRM, NAME, 3)
    retriever.search_history(Equipment.SRM, NAME, 3)
    retriever.search_manual(None, "다른 질문", 3)
    retriever.search_history(None, "다른 질문", 3)
    assert emb.queries == [NAME, "다른 질문"]


def test_임베딩_객체가_바뀌면_같은_질의도_다시_임베딩한다(install_store):
    old, new = FakeEmbeddings(), FakeEmbeddings()
    install_store(manual=manual_store(old), hist=history_store(new))  # connect로 객체가 바뀐 경우
    retriever.search_manual(Equipment.SRM, NAME, 3)
    retriever.search_history(Equipment.SRM, NAME, 3)
    assert (old.queries, new.queries) == ([NAME], [NAME])


def test_임베딩_실패는_DependencyError이고_벡터를_저장하지_않는다(install_store):
    emb = FlakyEmbeddings()
    install_store(manual=manual_store(emb))
    with pytest.raises(DependencyError) as info:
        retriever.search_manual(Equipment.SRM, NAME, 3)
    assert isinstance(info.value.__cause__, RuntimeError)
    assert retriever.search_manual(Equipment.SRM, NAME, 3)  # 실패가 저장되지 않아 다시 임베딩하고 성공한다
    assert emb.queries == [NAME, NAME]
