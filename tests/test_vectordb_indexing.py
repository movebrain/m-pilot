"""vectordb 색인 쓰기 테스트: rebuild_search_index, index_history(spec §3.4).

OpenAI 없이 돈다. 가짜 임베딩(DeterministicFakeEmbedding)과 메모리 Chroma를 쓰고,
`get_store`(다른 담당)는 테스트 안에서 그 저장소를 돌려주도록 바꿔 끼운다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from langchain_chroma import Chroma
from langchain_core.embeddings import DeterministicFakeEmbedding

from mpilot.db import vectordb
from mpilot.models import (
    ActionOwner,
    CaseDetail,
    CaseStatus,
    Chunk,
    CitationKind,
    DependencyError,
    Equipment,
    ErrorCode,
    Resolution,
    Route,
    ServiceCase,
    Severity,
    Source,
    ValidationError,
)


@pytest.fixture
def stores(monkeypatch):
    """테스트마다 새 메모리 컬렉션 2개를 만들고 get_store가 그것을 돌려주게 한다."""
    emb = DeterministicFakeEmbedding(size=16)
    suffix = uuid.uuid4().hex[:8]
    made = {
        kind: Chroma(
            collection_name=f"{kind.value}-{suffix}",
            embedding_function=emb,
            collection_metadata={"hnsw:space": "cosine"},
        )
        for kind in CitationKind
    }
    monkeypatch.setattr(vectordb, "get_store", lambda kind: made[kind])
    yield made
    for store in made.values():
        store.delete_collection()


def chunk(manual_id="srm_spec", page=12, no=1, text="11.6 스테이션 인계 이상 조치 절차", section="11.6") -> Chunk:
    return Chunk(
        manual_id=manual_id,
        equipment=Equipment.SRM,
        page=page,
        section=section,
        chunk_no=no,
        version="rev1.2",
        text=text,
    )


def code(equipment=Equipment.SRM, code_="S135") -> ErrorCode:
    return ErrorCode(
        equipment=equipment,
        code=code_,
        name="스테이션 수령 확인 시간 초과",
        severity=Severity.MEDIUM,
        action_owner=ActionOwner.CUSTOMER,
        cause="원인",
        action="조치",
        manual_id="srm_spec",
        page=12,
        section="11.6",
    )


def case(case_id="SRM-H-0010", route=Route.SUPPORT, status=CaseStatus.COMPLETED, resolved=True):
    return CaseDetail(
        service_case=ServiceCase(
            case_id=case_id,
            equipment=Equipment.SRM,
            code="S135",
            memo="입고 스테이션 집기 대기 후 정지",
            source=Source.SEED,
            status=status,
            route=route,
            guide_failure=None,
            guidance_answer=None,
            created_at=datetime(2026, 7, 7, 14, 20, tzinfo=UTC),
            completed_at=datetime(2026, 7, 7, 14, 29, tzinfo=UTC)
            if status == CaseStatus.COMPLETED
            else None,
        ),
        error_code=code(),
        resolution=Resolution(cause="M12 커넥터 접촉 불량", action="커넥터 교체", result="30회 시험 정상")
        if resolved
        else None,
    )


def ids_of(store) -> set[str]:
    return set(store.get()["ids"])


# --- rebuild_search_index ------------------------------------------------


def test_rebuild_fills_both_collections_with_spec_ids(stores):
    vectordb.rebuild_search_index(
        chunks=[chunk(no=1), chunk(no=2)],
        codes=[code()],
        histories=[case()],
    )
    assert ids_of(stores[CitationKind.MANUAL]) == {
        "error:SRM:S135",
        "manual:srm_spec:12:1",
        "manual:srm_spec:12:2",
    }
    assert ids_of(stores[CitationKind.HISTORY]) == {"history:SRM-H-0010"}


def test_rebuild_metadata_supports_equipment_filter_and_citation(stores):
    vectordb.rebuild_search_index([chunk()], [code()], [case()])
    manual = stores[CitationKind.MANUAL].get(ids=["manual:srm_spec:12:1"])
    assert manual["documents"][0] == "11.6 스테이션 인계 이상 조치 절차"  # 원문 그대로
    assert manual["metadatas"][0] == {
        "doc_type": "manual",
        "equipment": "SRM",
        "manual_id": "srm_spec",
        "page": 12,
        "section": "11.6",
        "chunk_no": 1,
        "version": "rev1.2",
        "code": vectordb.COMMON_CODE,
    }
    history = stores[CitationKind.HISTORY].get(ids=["history:SRM-H-0010"])
    assert history["metadatas"][0]["completed_at"] == "2026-07-07T14:29:00+00:00"
    assert "커넥터 교체" in history["documents"][0]
    # 설비 필터 검색이 동작한다.
    hits = stores[CitationKind.MANUAL].similarity_search("인계", k=5, filter={"equipment": "AGV"})
    assert hits == []


def test_rebuild_replaces_previous_index(stores):
    vectordb.rebuild_search_index([chunk(no=1), chunk(no=2)], [code()], [case("A-1"), case("A-2")])
    vectordb.rebuild_search_index([chunk(no=1)], [code()], [case("A-1")])
    assert ids_of(stores[CitationKind.MANUAL]) == {"error:SRM:S135", "manual:srm_spec:12:1"}
    assert ids_of(stores[CitationKind.HISTORY]) == {"history:A-1"}


def test_rebuild_rejects_invalid_ids_without_touching_index(stores):
    vectordb.rebuild_search_index([chunk()], [code()], [case()])
    with pytest.raises(ValidationError):
        vectordb.rebuild_search_index([chunk(manual_id="srm:spec")], [code()], [case()])
    # 검증 실패는 기존 색인을 지우기 전에 멈춘다.
    assert "manual:srm_spec:12:1" in ids_of(stores[CitationKind.MANUAL])


@pytest.mark.parametrize("bad", [chunk(manual_id="srm:spec"), chunk(manual_id="")])
def test_rebuild_rejects_colon_or_empty_id_parts(stores, bad):
    with pytest.raises(ValidationError):
        vectordb.rebuild_search_index([bad], [code()], [])


def test_rebuild_rejects_duplicate_manual_ids(stores):
    with pytest.raises(ValidationError, match="겹칩니다"):
        vectordb.rebuild_search_index([chunk(no=1), chunk(no=1, text="다른 원문")], [code()], [])


def test_rebuild_dedupes_history_by_case_id(stores):
    vectordb.rebuild_search_index([chunk()], [code()], [case("A-1"), case("A-1")])
    assert ids_of(stores[CitationKind.HISTORY]) == {"history:A-1"}


def test_rebuild_rejects_case_without_resolution(stores):
    with pytest.raises(ValidationError):
        vectordb.rebuild_search_index([chunk()], [code()], [case(resolved=False)])


def test_rebuild_wraps_store_failure_as_dependency_error(stores, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("embedding api down")

    # 매뉴얼 청크는 벡터를 직접 만들어 넣으므로 임베딩 생성에서 실패시킨다.
    monkeypatch.setattr(type(stores[CitationKind.MANUAL].embeddings), "embed_documents", boom)
    with pytest.raises(DependencyError):
        vectordb.rebuild_search_index([chunk()], [code()], [])


# --- 코드 절 메타데이터와 임베딩 텍스트 ------------------------------------


CODE_SECTION = "13.1 S135 스테이션 인계 이상"


def stored_vector(store, doc_id):
    return store._collection.get(ids=[doc_id], include=["embeddings"])["embeddings"][0]


def test_chunk_in_error_code_section_gets_code_metadata(stores):
    vectordb.rebuild_search_index([chunk(section=CODE_SECTION), chunk(no=2)], [], [])
    manual = stores[CitationKind.MANUAL].get(ids=["manual:srm_spec:12:1", "manual:srm_spec:12:2"])
    by_id = dict(zip(manual["ids"], manual["metadatas"], strict=True))
    assert by_id["manual:srm_spec:12:1"]["code"] == "S135"
    assert by_id["manual:srm_spec:12:2"]["code"] == vectordb.COMMON_CODE  # `11.6` 같은 공통 절


def test_manual_chunk_is_embedded_with_section_but_stored_as_is(stores):
    vectordb.rebuild_search_index([chunk(section=CODE_SECTION, text="원인 후보 표")], [], [])
    store = stores[CitationKind.MANUAL]
    doc_id = "manual:srm_spec:12:1"

    assert store.get(ids=[doc_id])["documents"][0] == "원인 후보 표"  # 인용 원문은 그대로
    vector = stored_vector(store, doc_id)
    assert list(vector) == pytest.approx(store.embeddings.embed_documents([f"{CODE_SECTION}\n원인 후보 표"])[0])
    assert list(vector) != pytest.approx(store.embeddings.embed_documents(["원인 후보 표"])[0])


def test_error_rows_and_history_are_embedded_as_stored(stores):
    vectordb.rebuild_search_index([chunk()], [code()], [case()])
    manual, history = stores[CitationKind.MANUAL], stores[CitationKind.HISTORY]
    row = manual.get(ids=["error:SRM:S135"])["documents"][0]
    note = history.get(ids=["history:SRM-H-0010"])["documents"][0]

    assert list(stored_vector(manual, "error:SRM:S135")) == pytest.approx(manual.embeddings.embed_documents([row])[0])
    assert list(stored_vector(history, "history:SRM-H-0010")) == pytest.approx(history.embeddings.embed_documents([note])[0])


# --- index_history -------------------------------------------------------


def test_index_history_adds_one_case(stores):
    vectordb.index_history(case("SC-1234ABCD"))
    assert ids_of(stores[CitationKind.HISTORY]) == {"history:SC-1234ABCD"}


def test_index_history_upserts_same_case_id(stores):
    vectordb.index_history(case("SC-1234ABCD"))
    vectordb.index_history(case("SC-1234ABCD"))
    assert ids_of(stores[CitationKind.HISTORY]) == {"history:SC-1234ABCD"}


@pytest.mark.parametrize(
    "bad",
    [
        case(resolved=False),  # 조치 기록 없음
        case(route=Route.SELF_GUIDE),  # 셀프 가이드 건(조치 기록이 있어도 막는다)
        case(status=CaseStatus.IN_PROGRESS),  # 아직 완료되지 않음
    ],
)
def test_index_history_rejects_non_confirmed_cases(stores, bad):
    with pytest.raises(ValidationError):
        vectordb.index_history(bad)
    assert ids_of(stores[CitationKind.HISTORY]) == set()


def test_index_history_wraps_store_failure(stores, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("chroma down")

    monkeypatch.setattr(stores[CitationKind.HISTORY], "add_documents", boom)
    with pytest.raises(DependencyError):
        vectordb.index_history(case())


# --- 리뷰 반영: 컬렉션 비우기 실패 -------------------------------------------


def test_rebuild_reset_failure_is_dependency_error_and_writes_nothing(stores, monkeypatch):
    def boom():
        raise RuntimeError("chroma down")

    monkeypatch.setattr(stores[CitationKind.MANUAL], "reset_collection", boom)
    with pytest.raises(DependencyError):
        vectordb.rebuild_search_index([chunk()], [code()], [case()])
    assert ids_of(stores[CitationKind.HISTORY]) == set()
