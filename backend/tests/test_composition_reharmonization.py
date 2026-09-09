"""Deterministic reharmonization preview coverage (bars 9-12 acceptance)."""

from __future__ import annotations

import pytest

from app.composition_schemas import CompositionV2
from app.harmony_schemas import (
    ReharmonizeError,
    ReharmonizePreviewRequest,
    ReharmonizeTonalContext,
)
from app.services.composition_reharmonization import (
    preview_reharmonization,
    recommend_target_track_ids,
)


TPQ = 480
BAR = 4 * TPQ  # 1920


def _note(event_id: str, pitch: str, start_tick: int, duration_ticks: int = 480, velocity: int = 80):
    return {
        "type": "note",
        "id": event_id,
        "pitch": pitch,
        "start_tick": start_tick,
        "duration_ticks": duration_ticks,
        "velocity": velocity,
    }


def _sixteen_bar_composition() -> CompositionV2:
    melody_events = []
    bass_events = []
    harm_events = []
    for bar in range(16):
        start = bar * BAR
        degree = ["C4", "D4", "E4", "G4"][bar % 4]
        melody_events.append(_note(f"m{bar}", degree, start, 960))
        bass_events.append(_note(f"b{bar}", ["C2", "G2", "A1", "F2"][bar % 4], start, BAR, 70))
        harm_events.extend(
            [
                _note(f"h{bar}a", ["C3", "G3", "A3", "F3"][bar % 4], start, BAR, 55),
                _note(f"h{bar}b", ["E3", "B3", "C4", "A3"][bar % 4], start, BAR, 55),
            ]
        )
    harmony = []
    chords = ["C", "G", "Am", "F"]
    for bar in range(16):
        harmony.append(
            {
                "start_tick": bar * BAR,
                "duration_ticks": BAR,
                "chord": chords[bar % 4],
            }
        )
    return CompositionV2.model_validate(
        {
            "schema_version": "composition.v2",
            "tempo": 100,
            "key": "C major",
            "time_signature": "4/4",
            "ticks_per_quarter": TPQ,
            "bar_count": 16,
            "duration_ticks": 16 * BAR,
            "sections": [
                {
                    "id": "sec-1",
                    "type": "verse",
                    "start_bar": 1,
                    "bar_count": 16,
                    "start_tick": 0,
                    "duration_ticks": 16 * BAR,
                }
            ],
            "tracks": [
                {
                    "id": "melody",
                    "name": "Melody",
                    "instrument": "piano",
                    "role": "melody",
                    "midi_program": 0,
                    "channel": 1,
                    "events": melody_events,
                },
                {
                    "id": "bass",
                    "name": "Bass",
                    "instrument": "bass",
                    "role": "bass",
                    "midi_program": 32,
                    "channel": 2,
                    "events": bass_events,
                },
                {
                    "id": "accomp",
                    "name": "Accomp",
                    "instrument": "piano",
                    "role": "harmony",
                    "midi_program": 0,
                    "channel": 3,
                    "events": harm_events,
                },
                {
                    "id": "drums",
                    "name": "Drums",
                    "instrument": "drums",
                    "role": "drums",
                    "midi_program": 0,
                    "channel": 10,
                    "events": [_note("d0", "C2", 0, 480)],
                },
            ],
            "harmony": harmony,
            "tempo_changes": [],
            "time_signature_changes": [],
            "key_changes": [],
            "markers": [],
            "motifs": [],
        }
    )


def test_recommend_targets_do_not_include_drums():
    composition = _sixteen_bar_composition()
    recommended = recommend_target_track_ids(composition, "preserve_melody_adapt_harmony")
    assert "bass" in recommended and "accomp" in recommended
    assert "melody" not in recommended
    assert "drums" not in recommended


def test_increase_tension_bars_9_12_preserves_melody():
    composition = _sixteen_bar_composition()
    source_melody = [
        event.model_dump(mode="json")
        for event in next(track for track in composition.tracks if track.id == "melody").events
    ]
    request = ReharmonizePreviewRequest(
        composition=composition,
        selection={"start_bar": 9, "end_bar": 12},
        operation="increase_tension",
        content_policy="preserve_melody_adapt_harmony",
        target_track_ids=["bass", "accomp"],
        engine="deterministic",
        instruction="make the harmony more tense while keeping the melody",
    )
    response = preview_reharmonization(request)

    # Source composition object identity / content unchanged (deep copy path).
    assert [
        event.model_dump(mode="json")
        for event in next(track for track in composition.tracks if track.id == "melody").events
    ] == source_melody

    candidate_melody = next(track for track in response.composition.tracks if track.id == "melody")
    assert [event.model_dump(mode="json") for event in candidate_melody.events] == source_melody

    assert response.harmony_changes
    assert any(item.before_chord != item.after_chord for item in response.harmony_changes)
    assert any(item.track_id in {"bass", "accomp"} and item.events_changed > 0 for item in response.track_changes)
    assert response.compatibility.status in {"compatible", "compatible_with_warnings"}
    assert all(item.satisfied for item in response.preservation)
    assert response.provider == "deterministic"
    assert response.start_tick == 8 * BAR
    assert response.end_tick == 12 * BAR

    # Outside bars 9-12 harmony unchanged for non-overlapping spans.
    source_outside = [
        (item.start_tick, item.duration_ticks, item.chord)
        for item in composition.harmony
        if item.start_tick + item.duration_ticks <= response.start_tick or item.start_tick >= response.end_tick
    ]
    cand_outside = [
        (item.start_tick, item.duration_ticks, item.chord)
        for item in response.composition.harmony
        if item.start_tick + item.duration_ticks <= response.start_tick or item.start_tick >= response.end_tick
    ]
    assert source_outside == cand_outside


def test_rejects_drums_and_requires_explicit_targets():
    composition = _sixteen_bar_composition()
    with pytest.raises(ReharmonizeError) as missing:
        preview_reharmonization(
            ReharmonizePreviewRequest(
                composition=composition,
                selection={"start_bar": 9, "end_bar": 12},
                operation="increase_tension",
                content_policy="preserve_melody_adapt_harmony",
                target_track_ids=[],
            )
        )
    assert missing.value.code == "reharmonize_invalid_targets"

    with pytest.raises(ReharmonizeError) as drums:
        preview_reharmonization(
            ReharmonizePreviewRequest(
                composition=composition,
                selection={"start_bar": 9, "end_bar": 12},
                operation="increase_tension",
                content_policy="preserve_melody_adapt_harmony",
                target_track_ids=["drums"],
            )
        )
    assert drums.value.code == "reharmonize_invalid_targets"


def test_adapt_accompaniment_only_preserves_harmony_and_melody():
    composition = _sixteen_bar_composition()
    harmony_before = [(i.start_tick, i.duration_ticks, i.chord) for i in composition.harmony]
    response = preview_reharmonization(
        ReharmonizePreviewRequest(
            composition=composition,
            selection={"start_bar": 9, "end_bar": 12},
            operation="increase_tension",
            content_policy="adapt_accompaniment_only",
            target_track_ids=["bass", "accomp"],
        )
    )
    harmony_after = [
        (i.start_tick, i.duration_ticks, i.chord) for i in response.composition.harmony
    ]
    assert harmony_before == harmony_after
    melody = next(track for track in response.composition.tracks if track.id == "melody")
    source_melody = next(track for track in composition.tracks if track.id == "melody")
    assert [e.model_dump(mode="json") for e in melody.events] == [
        e.model_dump(mode="json") for e in source_melody.events
    ]
    assert any(item.events_changed > 0 for item in response.track_changes)


def test_tonicize_requires_target_chord():
    composition = _sixteen_bar_composition()
    with pytest.raises(ReharmonizeError) as exc:
        preview_reharmonization(
            ReharmonizePreviewRequest(
                composition=composition,
                selection={"start_bar": 1, "end_bar": 4},
                operation="tonicize_target",
                content_policy="preserve_melody_adapt_harmony",
                target_track_ids=["bass"],
                tonal_context=ReharmonizeTonalContext(target_chord=None),
            )
        )
    assert exc.value.code == "reharmonize_tonicize_required"


@pytest.mark.parametrize(
    "operation",
    [
        "suggest_progression",
        "reharmonize",
        "decrease_tension",
        "strengthen_cadence",
        "use_secondary_dominants",
        "use_modal_interchange",
        "simplify_harmony",
    ],
)
def test_deterministic_operations_matrix(operation: str):
    composition = _sixteen_bar_composition()
    response = preview_reharmonization(
        ReharmonizePreviewRequest(
            composition=composition,
            selection={"start_bar": 9, "end_bar": 12},
            operation=operation,  # type: ignore[arg-type]
            content_policy="preserve_melody_adapt_harmony",
            target_track_ids=["bass", "accomp"],
        )
    )
    assert response.base_fingerprint
    assert response.proposal_fingerprint
    assert response.proposal_fingerprint != response.base_fingerprint or response.track_changes
