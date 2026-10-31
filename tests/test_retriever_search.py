"""설비 필터 검색 테스트(retriever.search). 가짜 저장소로 DB 파트 없이 검증한다."""

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


class FakeStore:
    """메타데이터 동등 필터를 실제로 적용하고 미리 정한 거리를 돌려주는 가짜 저장소."""

    def __init__(self, scored: list[tuple[Document, float]]):
        self.scored = scored
        self.calls: list[dict] = []

    def similarity_search_with_score(self, query, k=4, filter=None):
        self.calls.append({"query": query, "k": k, "filter": filter})
        hits = [(doc, dist) for doc, dist in self.scored if matches(doc, filter)]
        return hits[:k]  # 입력 순서를 그대로 둬 정렬은 retriever가 하는지 확인한다.


def matches(doc: Document, where: dict | None) -> bool:
    """Chroma where 중 retriever가 쓰는 동등·`$and`·`$in`만 흉내 낸다."""
    if not where:
        return True
    if "$and" in where:
        return all(matches(doc, cond) for cond in where["$and"])
    key, value = next(iter(where.items()))
    if isinstance(value, dict):
        return doc.metadata.get(key) in value["$in"]
    return doc.metadata.get(key) == value


class BrokenStore:
    def similarity_search_with_score(self, query, k=4, filter=None):
        raise RuntimeError("임베딩 API 장애")


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


def manual_store() -> FakeStore:
    return FakeStore(
        [
            (error_row(Equipment.MINI_LOADER, "M406"), 0.30),
            (error_row(Equipment.SRM, "S115"), 0.10),
            (error_row(Equipment.RTV, "R206"), 0.20),
            (manual_body(Equipment.SRM, 1), 0.15),
            (manual_body(Equipment.MINI_LOADER, 1), 0.25),
        ]
    )


def history_store() -> FakeStore:
    return FakeStore(
        [
            (history(Equipment.MINI_LOADER, "C-1", "M406"), 0.40),
            (history(Equipment.SRM, "C-2", "S115"), 0.05),
            (history(Equipment.RTV, "C-3", "R206"), 0.20),
            (history(Equipment.SRM, "C-4", "S115"), 0.30),
        ]
    )


@pytest.mark.parametrize("equipment", [Equipment.MINI_LOADER, Equipment.SRM, Equipment.RTV])
def test_manual_returns_only_given_equipment(install_store, equipment):
    install_store(manual=manual_store())
    hits = retriever.search(CitationKind.MANUAL, equipment, NAME, 5)
    assert hits
    assert {h.equipment for h in hits} == {equipment}


@pytest.mark.parametrize(
    "equipment, expected",
    [(Equipment.MINI_LOADER, {"C-1"}), (Equipment.SRM, {"C-2", "C-4"}), (Equipment.RTV, {"C-3"})],
)
def test_history_returns_only_given_equipment(install_store, equipment, expected):
    install_store(hist=history_store())
    hits = retriever.search(CitationKind.HISTORY, equipment, NAME, 5)
    assert {h.ref_id.removeprefix("history:") for h in hits} == expected


def test_filter_and_k_are_passed_to_store(install_store):
    store = manual_store()
    install_store(manual=store)
    retriever.search(CitationKind.MANUAL, Equipment.SRM, NAME, 3)
    assert store.calls == [{"query": NAME, "k": 3, "filter": {"equipment": "SRM"}}]


def test_results_sorted_by_distance_and_mix_error_rows_with_body(install_store):
    install_store(manual=manual_store())
    hits = retriever.search(CitationKind.MANUAL, Equipment.SRM, NAME, 5)
    assert [h.distance for h in hits] == [0.10, 0.15]
    assert [h.ref_id.split(":")[0] for h in hits] == ["error", "manual"]


def test_history_sorted_by_distance(install_store):
    install_store(hist=history_store())
    hits = retriever.search(CitationKind.HISTORY, Equipment.SRM, NAME, 5)
    assert [h.distance for h in hits] == [0.05, 0.30]


def test_history_returns_exactly_k(install_store):
    install_store(hist=history_store())
    assert len(retriever.search(CitationKind.HISTORY, Equipment.SRM, NAME, 2)) == 2


def test_no_match_returns_empty_list(install_store):
    install_store(manual=manual_store(), hist=history_store())
    assert retriever.search(CitationKind.MANUAL, Equipment.AGV, NAME, 3) == []
    assert retriever.search(CitationKind.HISTORY, Equipment.AGV, NAME, 3) == []


@pytest.mark.parametrize("query", ["", "   ", "\n\t"])
@pytest.mark.parametrize("kind", [CitationKind.MANUAL, CitationKind.HISTORY])
def test_blank_query_is_validation_error(install_store, kind, query):
    install_store(manual=manual_store(), hist=history_store())
    with pytest.raises(ValidationError):
        retriever.search(kind, Equipment.SRM, query, 3)


@pytest.mark.parametrize("k", [0, -1])
@pytest.mark.parametrize("kind", [CitationKind.MANUAL, CitationKind.HISTORY])
def test_k_below_one_is_validation_error(install_store, kind, k):
    install_store(manual=manual_store(), hist=history_store())
    with pytest.raises(ValidationError):
        retriever.search(kind, Equipment.SRM, NAME, k)


@pytest.mark.parametrize("kind", [CitationKind.MANUAL, CitationKind.HISTORY])
def test_store_failure_becomes_dependency_error(install_store, kind):
    install_store(manual=BrokenStore(), hist=BrokenStore())
    with pytest.raises(DependencyError) as info:
        retriever.search(kind, Equipment.SRM, NAME, 3)
    assert isinstance(info.value.__cause__, RuntimeError)


@pytest.mark.parametrize("kind", [CitationKind.MANUAL, CitationKind.HISTORY])
def test_none_distance_becomes_dependency_error(install_store, kind):
    install_store(
        manual=FakeStore([(manual_body(Equipment.SRM, 1), None), (manual_body(Equipment.SRM, 2), 0.2)]),
        hist=FakeStore([(history(Equipment.SRM, "C-1", "S115"), None), (history(Equipment.SRM, "C-2", "S115"), 0.2)]),
    )
    with pytest.raises(DependencyError) as info:
        retriever.search(kind, Equipment.SRM, NAME, 3)
    assert isinstance(info.value.__cause__, TypeError)


@pytest.mark.parametrize("kind", [CitationKind.MANUAL, CitationKind.HISTORY])
def test_get_store_dependency_error_propagates(monkeypatch, kind):
    error = DependencyError("connect 전에 호출했다")

    def fail(kind):
        raise error

    monkeypatch.setattr(vectordb, "get_store", fail)
    with pytest.raises(DependencyError) as info:
        retriever.search(kind, Equipment.SRM, NAME, 3)
    assert info.value is error


@pytest.mark.parametrize("kind", [CitationKind.MANUAL, CitationKind.HISTORY])
def test_mpilot_error_from_store_is_not_converted(monkeypatch, kind):
    error = ValidationError("지원하지 않는 종류")

    def fail(kind):
        raise error

    monkeypatch.setattr(vectordb, "get_store", fail)
    with pytest.raises(ValidationError) as info:
        retriever.search(kind, Equipment.SRM, NAME, 3)
    assert info.value is error


def test_malformed_metadata_becomes_dependency_error(install_store):
    broken = Document(id="manual:x:1:1", page_content="본문", metadata={"equipment": "SRM"})
    install_store(manual=FakeStore([(broken, 0.1)]))
    with pytest.raises(DependencyError):
        retriever.search(CitationKind.MANUAL, Equipment.SRM, NAME, 3)


def test_hit_fields_for_error_row_body_and_history(install_store):
    install_store(manual=manual_store(), hist=history_store())
    row, body = retriever.search(CitationKind.MANUAL, Equipment.SRM, NAME, 5)
    assert (row.kind, row.code, row.locator) == (CitationKind.MANUAL, "S115", "spec 12쪽 4.2")
    assert row.text == f"S115 {NAME}"
    assert (body.kind, body.code, body.locator) == (CitationKind.MANUAL, None, "SRM-spec 7쪽 5.1")

    first = retriever.search(CitationKind.HISTORY, Equipment.SRM, NAME, 5)[0]
    assert (first.kind, first.code, first.locator) == (CitationKind.HISTORY, "S115", "접수 C-2 S115")
    assert first.distance == 0.05


def test_equipment가_None이면_필터_없이_4종_전체에서_찾는다(install_store):
    manual, hist = manual_store(), history_store()
    install_store(manual=manual, hist=hist)
    manual_hits = retriever.search(CitationKind.MANUAL, None, NAME, 5)
    history_hits = retriever.search(CitationKind.HISTORY, None, NAME, 5)
    assert manual.calls == [{"query": NAME, "k": 5, "filter": None}]
    assert hist.calls == [{"query": NAME, "k": 5, "filter": None}]
    assert {h.equipment for h in manual_hits} == {Equipment.MINI_LOADER, Equipment.SRM, Equipment.RTV}
    assert {h.equipment for h in history_hits} == {Equipment.MINI_LOADER, Equipment.SRM, Equipment.RTV}


def code_chunk(chunk_no: int, code: str | None) -> Document:
    """`code`가 없으면 색인처럼 공통 절 표시값을 단다."""
    doc = manual_body(Equipment.SRM, chunk_no)
    doc.metadata["code"] = code or vectordb.COMMON_CODE
    return doc


def test_code를_주면_다른_코드의_문서를_검색_전에_뺀다(install_store):
    other, same, common = code_chunk(1, "S-999"), code_chunk(2, "S-104"), code_chunk(3, None)
    other_row, same_row = error_row(Equipment.SRM, "S-999"), error_row(Equipment.SRM, "S-104")
    same_late = code_chunk(4, "S-104")
    docs = [other, same, other, common, other_row, same_row, same_late]
    store = FakeStore([(doc, i / 10) for i, doc in enumerate(docs)])
    install_store(manual=store)

    hits = retriever.search(CitationKind.MANUAL, Equipment.SRM, NAME, 3, code="S-104")

    assert [h.ref_id for h in hits] == [same.id, common.id, same_row.id]
    assert store.calls[0]["k"] == 3
    assert store.calls[0]["filter"] == {"$and": [{"equipment": "SRM"}, {"code": {"$in": ["S-104", "common"]}}]}
    assert hits[1].code is None  # 공통 절 표시값은 SearchHit에서 None으로 돌려준다


def test_code로_거르고_모자라면_있는_만큼만_돌려준다(install_store):
    install_store(manual=FakeStore([(code_chunk(1, "S-999"), 0.1)]))
    assert retriever.search(CitationKind.MANUAL, Equipment.SRM, NAME, 3, code="S-104") == []


class GetStore:
    """`get(where=...)`만 흉내 내는 가짜 저장소. 받은 where를 기록한다."""

    def __init__(self, docs: list[Document]):
        self.docs = docs
        self.wheres: list[dict] = []

    def get(self, where=None):
        self.wheres.append(where)
        conds = {k: v for c in where["$and"] for k, v in c.items()}
        picked = [d for d in self.docs if all(d.metadata.get(k) == v for k, v in conds.items())]
        return {"ids": [d.id for d in picked], "documents": [d.page_content for d in picked],
                "metadatas": [d.metadata for d in picked]}


def numbered(page: int, chunk_no: int, code: str) -> Document:
    doc = code_chunk(chunk_no, code)
    doc.id = f"manual:SRM-spec:{page}:{chunk_no}"
    doc.metadata.update(page=page, chunk_no=chunk_no)
    return doc


def test_코드_절_전체를_코드표_행_다음_쪽_청크_순으로_가져온다(monkeypatch):
    late, early, early2 = numbered(42, 1, "S-104"), numbered(41, 3, "S-104"), numbered(41, 1, "S-104")
    row, other = error_row(Equipment.SRM, "S-104"), numbered(41, 2, "S-999")
    store = GetStore([late, early, other, row, early2])
    monkeypatch.setattr(vectordb, "get_store", lambda kind: store)

    hits = retriever.fetch_code_documents(Equipment.SRM, "S-104")

    assert [h.ref_id for h in hits] == [row.id, early2.id, early.id, late.id]
    assert all(h.distance is None for h in hits)
    assert store.wheres == [{"$and": [{"equipment": "SRM"}, {"code": "S-104"}]}]


def test_코드_절_조회_실패는_DependencyError(monkeypatch):
    monkeypatch.setattr(vectordb, "get_store", lambda kind: BrokenStore())
    with pytest.raises(DependencyError):
        retriever.fetch_code_documents(Equipment.SRM, "S-104")


def test_코드_절은_엔지니어_조치_절차_제목_앞까지만_가져온다(monkeypatch):
    cond, mixed, eng = numbered(41, 1, "S-104"), numbered(41, 2, "S-104"), numbered(41, 3, "S-104")
    cond.page_content = "1. 발생 조건\n주행 중 발생"
    mixed.page_content = "2. 고객사 조치 절차\n1. 정지한다.\n\n3. 엔지니어 조치 절차\n\n1. LOTO 후 교체한다."
    eng.page_content = "2. 엔코더를 교체한다."
    table = numbered(41, 4, "S-104")
    table.page_content = "| 원인 후보 | 확인 방법 | 판별 기준 |\n| --- | --- | --- |\n| 커넥터 풀림 | 육안 | 들뜸 |"
    monkeypatch.setattr(vectordb, "get_store", lambda kind: GetStore([table, eng, mixed, cond]))

    hits = retriever.fetch_code_documents(Equipment.SRM, "S-104")

    assert [h.ref_id for h in hits] == [cond.id, mixed.id, table.id]  # 원인 후보 표는 제목 뒤에 있어도 남긴다
    assert hits[1].text == "2. 고객사 조치 절차\n1. 정지한다."
