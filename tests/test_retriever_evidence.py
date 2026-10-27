"""근거 충분성 판단 테스트(초기안, #49에서 규칙 확정)."""

import pytest

from mpilot.models import CitationKind, Equipment, SearchHit, ValidationError
from mpilot.rag.retriever import has_enough_evidence


def make_hit(distance, kind=CitationKind.MANUAL) -> SearchHit:
    return SearchHit(
        ref_id="manual:agv:1:1" if kind == CitationKind.MANUAL else "history:SC-00000001",
        kind=kind,
        equipment=Equipment.AGV,
        code=None,
        text="본문",
        locator="1쪽",
        distance=distance,
    )


def test_distance_equal_to_threshold_is_enough():
    assert has_enough_evidence([make_hit(0.5)], 0.5) is True


def test_one_close_hit_is_enough():
    assert has_enough_evidence([make_hit(0.9), make_hit(0.4), make_hit(0.8)], 0.5) is True


def test_all_far_hits_are_not_enough():
    assert has_enough_evidence([make_hit(0.6), make_hit(0.9)], 0.5) is False


def test_empty_hits_are_not_enough():
    assert has_enough_evidence([], 0.5) is False


def test_none_only_is_not_enough():
    assert has_enough_evidence([make_hit(None), make_hit(None)], 0.5) is False


def test_none_mixed_with_far_is_not_enough():
    assert has_enough_evidence([make_hit(None), make_hit(0.9)], 0.5) is False


def test_none_mixed_with_close_is_enough():
    assert has_enough_evidence([make_hit(None), make_hit(0.3)], 0.5) is True


def test_manual_and_history_mixed():
    hits = [make_hit(0.9), make_hit(0.4, CitationKind.HISTORY)]
    assert has_enough_evidence(hits, 0.5) is True
    assert has_enough_evidence([make_hit(0.9), make_hit(0.8, CitationKind.HISTORY)], 0.5) is False


def test_zero_threshold_is_allowed():
    assert has_enough_evidence([make_hit(0.0)], 0) is True
    assert has_enough_evidence([make_hit(0.1)], 0) is False


@pytest.mark.parametrize("threshold", [-1, float("nan"), float("inf"), float("-inf")])
def test_invalid_threshold_is_rejected(threshold):
    with pytest.raises(ValidationError):
        has_enough_evidence([make_hit(0.1)], threshold)
