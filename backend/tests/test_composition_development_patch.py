"""Tests for composition development draft realization and preservation."""

from __future__ import annotations

import copy

import pytest

from app.composition_development_schemas import (
    CompositionDevelopmentDraft,
    CompositionDevelopmentError,
    CompositionDevelopmentPreviewRequest,
)
from app.composition_schemas import CompositionV2
from app.services.composition_development_patch import realize_development_draft
from app.services.composition_edit_fingerprint import composition_edit_fingerprint
from tests.test_composition_development_context import _four_bar_piece, _request
from tests.test_composition_development_identity import _related_draft


def _sixteen_bar_a() -> CompositionV2:
    events_melody = []
    events_bass = []
    for bar in range(16):
        start = bar * 1920
        events_melody.append(
            {
                "id": f"m-{bar}",
                "pitch": "C4" if bar % 2 == 0 else "E4",
                "start_tick": start,
                "duration_ticks": 480,
                "velocity": 80,
            }
        )
        events_bass.append(
            {
                "id": f"b-{bar}",
                "pitch": "C2",
                "start_tick": start,
                "duration_ticks": 1920,
                "velocity": 70,
            }
        )
    return CompositionV2.model_validate(
        {
            "schema_version": "composition.v2",
            "tempo": 100,
            "key": "C major",
            "time_signature": "4/4",
            "ticks_per_quarter": 480,
            "bar_count": 16,
            "duration_ticks": 16 * 1920,
            "sections": [
                {
                    "id": "a",
                    "type": "verse",
                    "label": "A",
                    "start_bar": 1,
                    "bar_count": 16,
                    "start_tick": 0,
                    "duration_ticks": 16 * 1920,
                }
            ],
            "tracks": [
                {
                    "id": "melody-1",
                    "name": "Melody",
                    "instrument": "piano",
                    "role": "melody",
                    "midi_program": 0,
                    "channel": 1,
                    "events": events_melody,
                },
                {
                    "id": "bass-1",
                    "name": "Bass",
                    "instrument": "bass",
                    "role": "bass",
                    "midi_program": 32,
                    "channel": 2,
                    "events": events_bass,
                },
            ],
            "harmony": [
                {"start_tick": bar * 1920, "duration_ticks": 1920, "chord": "C" if bar % 2 == 0 else "G"}
                for bar in range(16)
            ],
            "tempo_changes": [],
            "time_signature_changes": [],
            "key_changes": [],
            "markers": [],
            "motifs": [],
        }
    )


def _eight_bar_continuation_draft() -> CompositionDevelopmentDraft:
    melody = []
    bass = []
    for bar in range(8):
        start = bar * 1920
        melody.append(
            {
                "pitch": "C4" if bar % 2 == 0 else "E4",
                "relative_start_tick": start,
                "duration_ticks": 480,
                "velocity": 80,
            }
        )
        bass.append(
            {
                "pitch": "C2",
                "relative_start_tick": start,
                "duration_ticks": 1920,
                "velocity": 70,
            }
        )
    return CompositionDevelopmentDraft.model_validate(
        {
            "tracks": [
                {"track_id": "melody-1", "events": melody},
                {"track_id": "bass-1", "events": bass},
            ],
            "harmony": [
                {
                    "relative_start_tick": bar * 1920,
                    "duration_ticks": 1920,
                    "chord": "C" if bar % 2 == 0 else "G",
                }
                for bar in range(8)
            ],
        }
    )


def test_sixteen_plus_eight_append_preserves_prefix_exactly():
    source = _sixteen_bar_a()
    before = copy.deepcopy(source.model_dump(mode="json"))
    request = _request(source, output_bars=8, variation_strength="balanced")
    result = realize_development_draft(request, _eight_bar_continuation_draft())

    assert result.composition.bar_count == 24
    assert result.composition.duration_ticks == 24 * 1920
    assert source.model_dump(mode="json") == before

    result_dump = result.composition.model_dump(mode="json")
    for track_src, track_res in zip(before["tracks"], result_dump["tracks"], strict=True):
        assert track_res["events"][: len(track_src["events"])] == track_src["events"]
        assert all(event["start_tick"] >= before["duration_ticks"] for event in track_res["events"][len(track_src["events"]) :])
    assert result_dump["sections"][:1] == before["sections"]
    assert all(item.satisfied for item in result.preservation if item.required)


def test_append_continues_all_tracks_and_inherits_ending_key_tempo():
    source = _four_bar_piece()
    request = _request(source, output_bars=2)
    result = realize_development_draft(request, _related_draft())
    assert [track.id for track in result.composition.tracks] == [track.id for track in source.tracks]
    assert result.composition.key == source.key
    assert result.composition.tempo == source.tempo
    assert result.created_event_count > 0


def test_add_section_appends_named_section():
    source = _four_bar_piece()
    request = CompositionDevelopmentPreviewRequest.model_validate(
        {
            "composition": source,
            "operation": "add_section",
            "output_bars": 4,
            "target_section_type": "chorus",
            "target_section_label": "Chorus 1",
            "variation_strength": "balanced",
            "development_intent": "develop",
        }
    )
    draft = CompositionDevelopmentDraft.model_validate(
        {
            "tracks": [
                {
                    "track_id": "melody-1",
                    "events": [
                        {"pitch": "C4", "relative_start_tick": 0, "duration_ticks": 480},
                        {"pitch": "E4", "relative_start_tick": 480, "duration_ticks": 480},
                    ],
                },
                {
                    "track_id": "bass-1",
                    "events": [{"pitch": "C2", "relative_start_tick": 0, "duration_ticks": 1920}],
                },
            ],
            "harmony": [{"relative_start_tick": 0, "duration_ticks": 1920, "chord": "C"}],
        }
    )
    result = realize_development_draft(request, draft)
    assert result.composition.sections[-1].type == "chorus"
    assert result.composition.sections[-1].label == "Chorus 1"
    assert result.composition.bar_count == source.bar_count + 4


def test_missing_track_rejected():
    source = _four_bar_piece()
    request = _request(source, output_bars=2)
    draft = CompositionDevelopmentDraft.model_validate(
        {
            "tracks": [
                {
                    "track_id": "melody-1",
                    "events": [{"pitch": "C4", "relative_start_tick": 0, "duration_ticks": 480}],
                }
            ]
        }
    )
    with pytest.raises(CompositionDevelopmentError) as exc:
        realize_development_draft(request, draft)
    assert exc.value.code in {"development_identity_failed", "development_track_topology"}


def test_event_overflow_rejected():
    source = _four_bar_piece()
    request = _request(source, output_bars=1)
    draft = CompositionDevelopmentDraft.model_validate(
        {
            "tracks": [
                {
                    "track_id": "melody-1",
                    "events": [{"pitch": "C4", "relative_start_tick": 0, "duration_ticks": 10_000}],
                },
                {"track_id": "bass-1", "events": []},
            ]
        }
    )
    with pytest.raises(CompositionDevelopmentError) as exc:
        realize_development_draft(request, draft)
    assert exc.value.code in {"development_draft_invalid", "development_identity_failed"}


def test_variation_preserves_outside_range():
    source = _four_bar_piece()
    before = copy.deepcopy(source.model_dump(mode="json"))
    request = _request(
        source,
        operation="vary_section",
        output_bars=None,
        source={"start_bar": 3, "end_bar": 4},
        variation_strength="balanced",
    )
    draft = CompositionDevelopmentDraft.model_validate(
        {
            "tracks": [
                {
                    "track_id": "melody-1",
                    "events": [
                        {"pitch": "D4", "relative_start_tick": 0, "duration_ticks": 480},
                        {"pitch": "F4", "relative_start_tick": 480, "duration_ticks": 480},
                    ],
                },
                {
                    "track_id": "bass-1",
                    "events": [{"pitch": "D2", "relative_start_tick": 0, "duration_ticks": 1920}],
                },
            ],
            "harmony": [{"relative_start_tick": 0, "duration_ticks": 1920, "chord": "Dm"}],
        }
    )
    result = realize_development_draft(request, draft)
    assert result.composition.bar_count == source.bar_count
    assert result.composition.duration_ticks == source.duration_ticks
    assert source.model_dump(mode="json") == before
    assert result.composition.sections == source.sections
    # Harmony spans entirely before bar 3 must remain.
    early_harmony = [item for item in before["harmony"] if item["start_tick"] + item["duration_ticks"] <= 3840]
    result_early = [
        item.model_dump(mode="json")
        for item in result.composition.harmony
        if item.start_tick + item.duration_ticks <= 3840
    ]
    assert result_early == early_harmony
    assert all(item.satisfied for item in result.preservation if item.kind == "outside_range")


def test_fingerprint_changes_after_successful_append():
    source = _four_bar_piece()
    before_fp = composition_edit_fingerprint(source)
    result = realize_development_draft(_request(source, output_bars=2), _related_draft())
    after_fp = composition_edit_fingerprint(result.composition)
    assert before_fp != after_fp
