"""Structural repairs keep prefix identity and shift only the suffix."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.composition_schemas import CompositionV2
from app.film_score_adapt_schemas import FilmAdaptError
from app.services.film_score_adapt import apply_film_score_adaptation, expected_timeline
from tests.film_score_adapt_fixture import (
    base_previous,
    base_score,
    cue,
    delete_span,
    insert_span,
    move_hit,
    pitch_for_bar,
    snapshot,
)


def _apply(previous, edits, score):
    stored = expected_timeline(previous, edits)
    return apply_film_score_adaptation(previous, edits, stored, score)


def _event(score: CompositionV2, event_id: str):
    for track in score.tracks:
        for event in track.events:
            if event.id == event_id:
                return event
    raise AssertionError(event_id)


def test_vector_a_removes_three_bars_and_shifts_the_suffix() -> None:
    source = base_score()
    applied = _apply(base_previous(), [delete_span(16, 22)], source)
    candidate = applied.composition
    assert candidate.bar_count == 29
    assert applied.operations[0].strategy == "phrase_contract"
    assert applied.operations[0].start_bar == 9
    assert applied.operations[0].end_bar == 11
    assert "targeted_regenerate" not in [op.strategy for op in applied.operations]
    assert "note_truncated" not in applied.warnings
    assert applied.counts.events_added == 0
    assert _event(candidate, "note_bar_01").pitch == "C4"
    assert _event(candidate, "note_bar_01").pitch == pitch_for_bar(1)
    for index in range(1, 9):
        before = _event(source, f"note_bar_{index:02d}")
        after = _event(candidate, f"note_bar_{index:02d}")
        assert after.model_dump() == before.model_dump()
    for index in range(12, 33):
        before = _event(source, f"note_bar_{index:02d}")
        after = _event(candidate, f"note_bar_{index:02d}")
        assert after.pitch == before.pitch
        assert after.duration_ticks == before.duration_ticks
        assert after.velocity == before.velocity
        assert after.id == before.id
        assert after.start_tick == before.start_tick - 5760
    assert {event.id for event in candidate.tracks[0].events}.isdisjoint(
        {"note_bar_09", "note_bar_10", "note_bar_11"}
    )
    marker = next(item for item in candidate.markers if item.label == "later")
    assert marker.tick == 30720
    assert marker.kind == "rehearsal"
    assert any(section.label == "climax" for section in candidate.sections)
    original = next(
        occurrence
        for motif in candidate.motifs
        for occurrence in motif.occurrences
        if occurrence.relationship == "original"
    )
    assert "note_bar_01" in original.event_ids
    assert "note_bar_02" in original.event_ids
    assert candidate.tracks[0].instrument == source.tracks[0].instrument
    assert candidate.tracks[0].id == source.tracks[0].id
    assert any(item.chord == "F" for item in candidate.harmony)
    assert applied.counts.events_unchanged + applied.counts.events_shifted + applied.counts.events_removed == 32
    CompositionV2.model_validate(candidate.model_dump(mode="json"))


def test_vector_c_changes_only_section_tempo() -> None:
    source = base_score()
    applied = _apply(base_previous(), [delete_span(16, 17)], source)
    candidate = applied.composition
    assert candidate.tempo == 120
    assert [(change.tick, change.bpm) for change in candidate.tempo_changes] == [
        (15360, 128),
        (30720, 120),
    ]
    assert "tempo_restored" in applied.warnings
    for event_id, event in (
        (event.id, event) for track in source.tracks for event in track.events
    ):
        assert _event(candidate, event_id).model_dump() == event.model_dump()
    later = next(hit for hit in applied.hit_changes if hit.cue_id == "hit_bbbb0002")
    assert later.next_seconds == 35
    assert later.status == "aligned"


def test_vector_e_inserts_two_silent_bars() -> None:
    source = base_score()
    previous = base_previous(
        [
            cue("hit_aaaa0001", 6),
            cue("hit_a0aa0001", 16, kind="music_stop", importance="medium"),
            cue("hit_b0bb0001", 16, kind="music_start", importance="medium"),
            cue("hit_bbbb0002", 36),
        ]
    )
    applied = _apply(previous, [insert_span(16, 4)], source)
    candidate = applied.composition
    assert applied.operations[0].strategy == "silence_insert"
    assert applied.operations[0].bars_delta == 2
    for index in range(1, 9):
        assert _event(candidate, f"note_bar_{index:02d}").model_dump() == _event(
            source, f"note_bar_{index:02d}"
        ).model_dump()
    for index in range(9, 33):
        before = _event(source, f"note_bar_{index:02d}")
        after = _event(candidate, f"note_bar_{index:02d}")
        assert after.start_tick == before.start_tick + 3840
        assert after.pitch == before.pitch
    bar_ticks = 1920
    silent_start = 8 * bar_ticks
    silent_end = silent_start + 2 * bar_ticks
    assert not any(silent_start <= event.start_tick < silent_end for event in candidate.tracks[0].events)
    CompositionV2.model_validate(candidate.model_dump(mode="json"))


def test_vector_d_moves_only_the_bar_four_attack() -> None:
    calls = {"registry": 0}

    def _boom(*_args, **_kwargs):
        calls["registry"] += 1
        raise AssertionError("agent path")

    import app.ai_agents.registry as registry

    original = registry.ensure_registry
    registry.ensure_registry = _boom
    try:
        source = base_score()
        previous = base_previous([cue("hit_aaaa0001", 6), cue("hit_bbbb0002", 36)])
        applied = _apply(previous, [move_hit("hit_aaaa0001", 6, 6.25, op_id="edit_0000000d")], source)
    finally:
        registry.ensure_registry = original
    candidate = applied.composition
    moved = _event(candidate, "note_bar_04")
    assert moved.start_tick == 6000
    assert moved.pitch == _event(source, "note_bar_04").pitch
    assert moved.velocity == _event(source, "note_bar_04").velocity
    for index in list(range(1, 4)) + list(range(5, 33)):
        assert _event(candidate, f"note_bar_{index:02d}").model_dump() == _event(
            source, f"note_bar_{index:02d}"
        ).model_dump()
    assert calls["registry"] == 0
    source_text = Path(__file__).resolve().parents[1].joinpath("app/services/film_score_adapt.py").read_text(
        encoding="utf-8"
    )
    assert "apply_film_score_accents" not in source_text


def test_motif_linked_attack_is_not_moved() -> None:
    source = base_score()
    data = source.model_dump(mode="json")
    data["motifs"][0]["occurrences"][0]["event_ids"] = ["note_bar_02", "note_bar_03", "note_bar_04"]
    linked = CompositionV2.model_validate(data)
    previous = base_previous([cue("hit_aaaa0001", 6), cue("hit_bbbb0002", 36)])
    applied = _apply(previous, [move_hit("hit_aaaa0001", 6, 6.25, op_id="edit_0000000d")], linked)
    assert _event(applied.composition, "note_bar_04").start_tick == _event(linked, "note_bar_04").start_tick
    assert "melody_protected" in applied.warnings
    assert applied.counts.events_added == 1
    added = [event for event in applied.composition.tracks[0].events if event.id and event.id.startswith("adapt_")]
    assert len(added) == 1
    assert added[0].pitch == "C4"
    assert added[0].velocity == 96


def test_vector_f_refuses_a_full_window_without_a_candidate() -> None:
    with pytest.raises(FilmAdaptError) as caught:
        _apply(base_previous(), [delete_span(0, 64)], base_score())
    assert caught.value.code == "film_adapt_span_too_large"
    assert caught.value.candidate is None


def test_empty_score_is_refused() -> None:
    source = base_score()
    data = source.model_dump(mode="json")
    data["tracks"][0]["events"] = []
    data["motifs"] = []
    empty = CompositionV2.model_validate(data)
    with pytest.raises(FilmAdaptError) as caught:
        _apply(snapshot(64, []), [delete_span(16, 22)], empty)
    assert caught.value.code == "film_adapt_score_empty"
    assert caught.value.candidate is None
