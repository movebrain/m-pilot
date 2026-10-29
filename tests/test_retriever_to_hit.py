"""검색 문서 → `SearchHit` 변환 테스트(`_to_hit`)."""

import pytest
from langchain_core.documents import Document

from mpilot.models import CitationKind, DependencyError, Equipment
from mpilot.rag.retriever import _to_hit


def make_doc(ref_id: str, text: str = "원문", **meta) -> Document:
    metadata = {"equipment": "AGV", "manual_id": "agv", "page": 3, "section": "2.1"}
    metadata.update(meta)
    return Document(id=ref_id, page_content=text, metadata=metadata)


def test_to_hit_manual_body():
    hit = _to_hit(make_doc("manual:agv:3:1", "본문"), None)

    assert hit.ref_id == "manual:agv:3:1"
    assert hit.kind is CitationKind.MANUAL
    assert hit.equipment is Equipment.AGV
    assert hit.code is None
    assert hit.text == "본문"
    assert hit.locator == "agv 3쪽 2.1"
    assert hit.distance is None


def test_to_hit_manual_body_keeps_code_of_error_section():
    assert _to_hit(make_doc("manual:agv:3:1", code="A120"), 0.5).code == "A120"


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
