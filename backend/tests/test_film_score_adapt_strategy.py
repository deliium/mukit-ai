"""Strategy choice prefers the smallest repair and refuses oversized spans."""

from __future__ import annotations

import pytest

from app.film_score_adapt_schemas import FilmAdaptError
from app.services.film_score_adapt import choose_film_score_adaptations, expected_timeline
from tests.film_score_adapt_fixture import (
    base_previous,
    base_score,
    cue,
    delete_span,
    move_hit,
    score_bars,
    snapshot,
)


def _choose(previous, edits, score):
    stored = expected_timeline(previous, edits)
    return choose_film_score_adaptations(previous, edits, stored, score)


def test_vector_a_contracts_the_phrase_instead_of_tempo_or_regeneration() -> None:
    choice = _choose(base_previous(), [delete_span(16, 22)], base_score())
    strategies = [operation.strategy for operation in choice.operations]
    assert strategies == ["phrase_contract"]
    assert "local_tempo" not in strategies
    assert "targeted_regenerate" not in strategies
    assert choice.operations[0].bars_delta == -3
    assert choice.operations[0].start_bar == 9
    assert choice.operations[0].end_bar == 11


def test_vector_c_uses_local_tempo_and_restores_the_next_section() -> None:
    choice = _choose(base_previous(), [delete_span(16, 17)], base_score())
    operation = choice.operations[0]
    assert operation.strategy == "local_tempo"
    assert operation.tempo_bpm == 128
    assert operation.bars_delta == 0
    assert "tempo_restored" in choice.warnings
    later = next(hit for hit in choice.hit_changes if hit.cue_id == "hit_bbbb0002")
    assert later.next_seconds == 35
    assert later.status == "aligned"


def test_vector_b_keeps_the_score_when_the_hit_stays_inside_tolerance() -> None:
    previous = base_previous(
        [cue("hit_aaaa0001", 6), cue("hit_bbbb0002", 36, tolerance_frames=1)]
    )
    edits = [move_hit("hit_bbbb0002", 36, 36 + 1 / 24)]
    choice = _choose(previous, edits, base_score())
    assert choice.operations[0].strategy == "unchanged"
    assert choice.operations[0].bars_delta == 0


def test_vector_d_regenerates_one_bar_without_considering_tempo() -> None:
    previous = base_previous([cue("hit_aaaa0001", 6), cue("hit_bbbb0002", 36)])
    edits = [move_hit("hit_aaaa0001", 6, 6.25, op_id="edit_0000000d")]
    choice = _choose(previous, edits, base_score())
    strategies = [operation.strategy for operation in choice.operations]
    assert strategies == ["targeted_regenerate"]
    assert "local_tempo" not in strategies
    assert choice.operations[0].start_bar == 4


def test_transition_edge_shortens_before_a_phrase_contract() -> None:
    score = score_bars(8, label_at={1: "transition out"})
    previous = snapshot(16, [])
    choice = _choose(previous, [delete_span(12, 16)], score)
    assert choice.operations[0].strategy == "transition_shorten"
    assert choice.operations[0].bars_delta == -2


def test_thirty_three_bars_is_too_large() -> None:
    score = score_bars(40)
    previous = snapshot(80, [])
    with pytest.raises(FilmAdaptError) as caught:
        _choose(previous, [delete_span(0, 66)], score)
    assert caught.value.code == "film_adapt_span_too_large"
    assert caught.value.candidate is None
