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


def chunk(manual_id="srm_spec", page=12, no=1, text="11.6 스테이션 인계 이상 조치 절차") -> Chunk:
    return Chunk(
        manual_id=manual_id,
        equipment=Equipment.SRM,
        page=page,
        section="11.6",
        chunk_no=no,
        version="rev1.2",
        text=text,
    )


def code(refs=(), equipment=Equipment.SRM, code_="S135") -> ErrorCode:
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
        customer_procedure_ref_ids=list(refs),
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
        codes=[code(refs=["manual:srm_spec:12:1"])],
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


def test_rebuild_rejects_missing_customer_procedure_without_touching_index(stores):
    vectordb.rebuild_search_index([chunk()], [code()], [case()])
    with pytest.raises(ValidationError, match="manual:srm_spec:99:1"):
        vectordb.rebuild_search_index([chunk()], [code(refs=["manual:srm_spec:99:1"])], [case()])
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

    monkeypatch.setattr(stores[CitationKind.MANUAL], "add_documents", boom)
    with pytest.raises(DependencyError):
        vectordb.rebuild_search_index([chunk()], [code()], [])


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


# --- 리뷰 반영: 허용 절차 ID 엄격 검증, 컬렉션 비우기 실패 -------------------------


def test_rebuild_rejects_error_row_as_customer_procedure(stores):
    # 허용 절차는 매뉴얼 본문(manual:)만 된다. 코드 표 행(error:)은 절차가 아니다.
    with pytest.raises(ValidationError, match="매뉴얼 본문"):
        vectordb.rebuild_search_index([chunk()], [code(refs=["error:SRM:S135"])], [])


def test_rebuild_rejects_procedure_of_other_equipment(stores):
    agv_chunk = Chunk(
        manual_id="agv_spec",
        equipment=Equipment.AGV,
        page=3,
        section="8.4",
        chunk_no=1,
        version="rev1.1",
        text="AGV 절차",
    )
    with pytest.raises(ValidationError, match="다른 설비"):
        vectordb.rebuild_search_index(
            [chunk(), agv_chunk], [code(refs=["manual:agv_spec:3:1"])], []
        )


def test_rebuild_reset_failure_is_dependency_error_and_writes_nothing(stores, monkeypatch):
    def boom():
        raise RuntimeError("chroma down")

    monkeypatch.setattr(stores[CitationKind.MANUAL], "reset_collection", boom)
    with pytest.raises(DependencyError):
        vectordb.rebuild_search_index([chunk()], [code()], [case()])
    assert ids_of(stores[CitationKind.HISTORY]) == set()
