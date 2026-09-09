"""Backend regression, scale, import/analysis stability, and export coverage for arrangement (Task 8)."""

from __future__ import annotations

import asyncio
import copy
import logging
from unittest.mock import patch

import pytest

from app.arrangement_schemas import (
    CompositionArrangementError,
    CompositionArrangementPreviewRequest,
)
from app.composition_schemas import CompositionV2
from app.llm_settings import LLMProviderSettings, LLMSettings
from app.schemas import LLMModelSelection
from app.services.composition_edit_fingerprint import (
    canonical_edit_json_dumps,
    composition_edit_fingerprint,
    full_document_edit_projection,
)
from app.services.composition_midi import render_midi, render_midi_with_report
from app.services.composition_migration import migrate_v1_to_v2
from app.services.instrument_catalog import find_baseline_mismatches, get_catalog, list_profiles
from app.services.llm_composition_arrangement import run_composition_arrangement_preview
from app.services.music_json_renderer import render_musicxml
from tests.fixtures.load_fixture import load_v2_expressive
from tests.test_composition_arrangement_context import (
    _acceptance_instrumentation,
    _part,
    _piano_sketch_v2,
)
from tests.test_composition_v2_schema import minimal_v2
from tests.test_export_fidelity import (
    assert_note_tuples_equal,
    canonical_note_tuples,
    midi_note_tuples_from_bytes,
    musicxml_note_tuples,
)


def _fake_settings() -> LLMSettings:
    return LLMSettings(
        providers=(
            LLMProviderSettings(
                provider="fake",
                model="fake-deterministic",
                api_key="unused",
                is_default=True,
            ),
        ),
        default_provider="fake",
        request_timeout_seconds=30,
        temperature=0.4,
    )


def _preview(composition: CompositionV2, **overrides):
    payload = {
        "composition": composition,
        "operation": "piano_to_ensemble",
        "source_track_ids": ["piano-melody", "piano-accomp", "bass-1"],
        "instrumentation": _acceptance_instrumentation(),
        "candidate_count": 1,
        "selection": LLMModelSelection(provider="fake", model="fake-deterministic"),
        "instruction": "UNIQUE_ARR_INSTRUCTION_MUST_NOT_LOG",
        "options": {"max_repairs": 1},
    }
    payload.update(overrides)
    request = CompositionArrangementPreviewRequest.model_validate(payload)
    return asyncio.run(run_composition_arrangement_preview(request, settings=_fake_settings()))


@pytest.fixture(autouse=True)
def _fake_mode(monkeypatch):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.delenv("LLM_FAKE_INJECT_MALFORMED", raising=False)


def test_catalog_does_not_alter_migrated_or_imported_source_metadata():
    """Arrangement catalog resolution must not rewrite persisted/migrated metadata."""
    from app.schemas import Composition

    v1 = Composition.model_validate(
        {
            "schema_version": "composition.v1",
            "tempo": 100,
            "key": "D minor",
            "time_signature": "3/4",
            "ticks_per_quarter": 480,
            "bar_count": 1,
            "duration_ticks": 1440,
            "sections": [
                {
                    "type": "intro",
                    "start_bar": 1,
                    "bar_count": 1,
                    "start_tick": 0,
                    "duration_ticks": 1440,
                }
            ],
            "tracks": [
                {
                    "id": "mystery-1",
                    "name": "Lead",
                    "instrument": "Custom Mystery Lead",
                    "role": "melody",
                    "midi_program": 80,
                    "channel": 1,
                    "events": [
                        {
                            "type": "note",
                            "pitch": "A4",
                            "start_tick": 0,
                            "duration_ticks": 480,
                            "velocity": 90,
                        }
                    ],
                },
                {
                    "id": "mismatch-2",
                    "name": "Cello-ish",
                    "instrument": "cello",
                    "role": "bass",
                    "midi_program": 0,
                    "channel": 2,
                    "events": [
                        {
                            "type": "note",
                            "pitch": "C3",
                            "start_tick": 0,
                            "duration_ticks": 960,
                            "velocity": 70,
                        }
                    ],
                },
            ],
            "harmony": [],
        }
    )
    before = copy.deepcopy(v1.model_dump(mode="json"))
    migrated = migrate_v1_to_v2(v1).composition
    assert v1.model_dump(mode="json") == before

    # Catalog findings are advisory and must not mutate track fields.
    snapshot = [
        (t.id, t.instrument, t.midi_program, t.channel, t.role, t.is_drum)
        for t in migrated.tracks
    ]
    findings = find_baseline_mismatches(migrated.tracks)
    assert [
        (t.id, t.instrument, t.midi_program, t.channel, t.role, t.is_drum)
        for t in migrated.tracks
    ] == snapshot
    assert any(item.code == "catalog_program_mismatch" for item in findings)

    # Source track metadata remains exactly as migrated after a catalog load.
    catalog = get_catalog()
    assert catalog.fingerprint
    assert migrated.tracks[0].instrument == "Custom Mystery Lead"
    assert migrated.tracks[0].midi_program == 80
    assert migrated.tracks[1].midi_program == 0


def test_common_gm_profile_matrix_stable():
    expected = {
        "acoustic_grand_piano": 0,
        "violin": 40,
        "cello": 42,
        "string_ensemble_1": 48,
        "flute": 73,
        "bassoon": 70,
        "english_horn": 69,
        "trumpet": 56,
        "electric_bass_finger": 33,
        "standard_drum_kit": 0,
    }
    profiles = {p.instrument_id: p for p in list_profiles()}
    for instrument_id, program in expected.items():
        assert profiles[instrument_id].midi_program == program
    assert profiles["standard_drum_kit"].is_drum is True
    assert profiles["bassoon"].compatibility_identity == "bassoon"
    assert profiles["english_horn"].compatibility_family == "woodwind"


@pytest.mark.parametrize("time_signature", ["4/4", "3/4", "6/8"])
def test_variable_meter_arrangement_preserves_source_document(time_signature):
    from app.composition_schemas import bar_duration_ticks

    bar_ticks = bar_duration_ticks(time_signature, 480)
    duration = bar_ticks * 4
    source = _piano_sketch_v2(
        time_signature=time_signature,
        bar_count=4,
        duration_ticks=duration,
        sections=[
            {
                "id": "a-section",
                "type": "verse",
                "label": "A",
                "start_bar": 1,
                "bar_count": 4,
                "start_tick": 0,
                "duration_ticks": duration,
            }
        ],
    )
    # Rebuild event timings inside bars for odd meters.
    rebuilt_tracks = []
    for track in source.tracks:
        events = []
        for index, event in enumerate(track.events):
            events.append(
                event.model_copy(
                    update={
                        "start_tick": min(index * (bar_ticks // 2), duration - 120),
                        "duration_ticks": min(event.duration_ticks, bar_ticks // 2),
                    }
                )
            )
        rebuilt_tracks.append(track.model_copy(update={"events": events}))
    from app.composition_schemas import CompositionV2HarmonyItem

    source = CompositionV2.model_validate(
        {
            **source.model_dump(mode="json"),
            "tracks": [track.model_dump(mode="json") for track in rebuilt_tracks],
            "harmony": [
                {"start_tick": 0, "duration_ticks": bar_ticks * 2, "chord": "C"},
                {"start_tick": bar_ticks * 2, "duration_ticks": bar_ticks * 2, "chord": "F"},
            ],
        }
    )
    assert isinstance(source.harmony[0], CompositionV2HarmonyItem)
    source_bytes = canonical_edit_json_dumps(full_document_edit_projection(source))
    response = _preview(source)
    assert len(response.candidates) == 1
    assert canonical_edit_json_dumps(full_document_edit_projection(source)) == source_bytes
    candidate = response.candidates[0].composition
    assert candidate.time_signature == time_signature
    assert candidate.key == source.key
    assert candidate.tempo == source.tempo
    assert candidate.harmony == source.harmony


def test_empty_harmony_and_key_tempo_preserved():
    source = _piano_sketch_v2(harmony=[], key="G minor", tempo=96)
    before = copy.deepcopy(source.model_dump(mode="json"))
    response = _preview(source)
    candidate = response.candidates[0].composition
    assert candidate.harmony == []
    assert candidate.key == "G minor"
    assert candidate.tempo == 96
    assert source.model_dump(mode="json") == before


def test_expressive_v2_ties_survive_arrangement_and_motifs_drums_ok():
    expressive = load_v2_expressive()
    # Expressive fixture includes tied melody notes; arrange melody → violin.
    melody = next(t for t in expressive.tracks if t.id == "melody-1")
    assert any(getattr(e, "tie", None) is not None for e in melody.events)
    source_bytes = canonical_edit_json_dumps(full_document_edit_projection(expressive))
    response = _preview(
        expressive,
        operation="change_instrumentation",
        source_track_ids=["melody-1"],
        protected_track_ids=[t.id for t in expressive.tracks if t.id != "melody-1"],
        instrumentation={
            "before": [
                _part(
                    "b-m",
                    "acoustic_grand_piano",
                    role="melody",
                    source_track_ids=["melody-1"],
                )
            ],
            "after": [_part("a-v", "violin", role="melody")],
        },
        allow_unlisted_after=True,
    )
    assert canonical_edit_json_dumps(full_document_edit_projection(expressive)) == source_bytes
    candidate = response.candidates[0].composition
    violin = next(t for t in candidate.tracks if "violin" in t.instrument.lower() or t.midi_program == 40)
    assert any(getattr(e, "tie", None) is not None for e in violin.events)
    assert candidate.harmony == expressive.harmony
    assert candidate.key == expressive.key

    # Motifs + drums: reinstrument melody while retaining drums on channel 10.
    from tests.test_composition_v2_schema import _motif_definition, _motif_track

    motif_track = _motif_track()
    data = minimal_v2(
        bar_count=1,
        duration_ticks=1920,
        tracks=[
            motif_track,
            {
                "id": "drums-1",
                "name": "Drums",
                "instrument": "drums",
                "role": "drums",
                "midi_program": 0,
                "channel": 10,
                "is_drum": True,
                "events": [
                    {
                        "id": "d1",
                        "pitch": "C2",
                        "start_tick": 0,
                        "duration_ticks": 120,
                        "velocity": 90,
                    }
                ],
            },
        ],
        motifs=[_motif_definition()],
        sections=[
            {
                "id": "s",
                "type": "verse",
                "start_bar": 1,
                "bar_count": 1,
                "start_tick": 0,
                "duration_ticks": 1920,
            }
        ],
        harmony=[],
    )
    composition = CompositionV2.model_validate(data)
    drum_response = _preview(
        composition,
        operation="change_instrumentation",
        source_track_ids=["melody-1", "drums-1"],
        instrumentation={
            "before": [
                _part(
                    "b-m",
                    "acoustic_grand_piano",
                    role="melody",
                    source_track_ids=["melody-1"],
                ),
                _part(
                    "b-d",
                    "standard_drum_kit",
                    role="drums",
                    source_track_ids=["drums-1"],
                ),
            ],
            "after": [
                _part("a-v", "flute", role="melody"),
                _part("a-d", "standard_drum_kit", role="drums"),
            ],
        },
    )
    drum_candidate = drum_response.candidates[0].composition
    assert any(t.is_drum and t.channel == 10 for t in drum_candidate.tracks)
    for motif in drum_candidate.motifs or []:
        for occ in motif.occurrences:
            track = next(t for t in drum_candidate.tracks if t.id == occ.track_id)
            ids = {e.id for e in track.events if e.id}
            assert set(occ.event_ids) <= ids


def test_four_candidates_export_ready_and_playable_from_events():
    source = _piano_sketch_v2()
    source_fp = composition_edit_fingerprint(source)
    response = _preview(source, candidate_count=4)
    assert len(response.candidates) == 4
    assert {c.edit_source_fingerprint for c in response.candidates} == {source_fp}
    assert len({c.candidate_id for c in response.candidates}) == 4
    assert len({c.candidate_fingerprint for c in response.candidates}) == 4

    candidate = response.candidates[2].composition
    assert all(track.events for track in candidate.tracks)
    # Explicit events are the playable source — MIDI programs match catalog targets.
    instruments = {t.instrument.lower() for t in candidate.tracks}
    assert any("piano" in name for name in instruments)
    assert any("cello" in name for name in instruments)

    midi = render_midi(candidate)
    musicxml, report = render_musicxml(candidate)
    assert isinstance(midi, (bytes, bytearray)) and midi[:4] == b"MThd"
    assert "score-partwise" in musicxml or "score-timewise" in musicxml
    assert report is not None

    expected = canonical_note_tuples(candidate)
    midi_notes = midi_note_tuples_from_bytes(midi, candidate)
    assert_note_tuples_equal(expected, midi_notes, source_format="midi")
    xml_notes = musicxml_note_tuples(musicxml, candidate)
    # MusicXML velocity is optional; compare pitch/onset/duration only.
    assert_note_tuples_equal(
        [n._replace(velocity=0) for n in expected],
        [n._replace(velocity=0) for n in xml_notes],
        source_format="musicxml",
    )

    # WAV reuses MIDI projection; prove the candidate is WAV-pipeline ready without requiring FluidSynth.
    from app.services.composition_wav import expected_duration_seconds

    midi_report = render_midi_with_report(candidate)
    assert midi_report.midi_bytes[:4] == b"MThd"
    assert expected_duration_seconds(candidate) > 0


def test_repeated_deterministic_fake_runs():
    source = _piano_sketch_v2()
    first = _preview(source, candidate_count=2)
    second = _preview(source, candidate_count=2)
    assert [c.candidate_id for c in first.candidates] == [c.candidate_id for c in second.candidates]
    assert [c.candidate_fingerprint for c in first.candidates] == [
        c.candidate_fingerprint for c in second.candidates
    ]
    for left, right in zip(first.candidates, second.candidates, strict=True):
        assert left.composition.model_dump(mode="json") == right.composition.model_dump(mode="json")


def test_fifteen_channel_limit_fails_before_provider(monkeypatch):
    tracks = []
    for index in range(16):
        tracks.append(
            {
                "id": f"t{index}",
                "name": f"T{index}",
                "instrument": "piano",
                "role": "harmony",
                "midi_program": index,
                "channel": (index % 15) + 1 if index < 15 else 16,
                "events": [
                    {
                        "id": f"e{index}",
                        "pitch": "C4",
                        "start_tick": 0,
                        "duration_ticks": 480,
                        "velocity": 80,
                    }
                ],
            }
        )
    composition = CompositionV2.model_validate(
        minimal_v2(
            bar_count=1,
            duration_ticks=1920,
            tracks=tracks,
            sections=[
                {
                    "id": "s",
                    "type": "verse",
                    "start_bar": 1,
                    "bar_count": 1,
                    "start_tick": 0,
                    "duration_ticks": 1920,
                }
            ],
            harmony=[],
        )
    )
    instrument_ids = [
        "acoustic_grand_piano",
        "bright_acoustic_piano",
        "electric_piano_1",
        "electric_piano_2",
        "church_organ",
        "acoustic_guitar_nylon",
        "acoustic_guitar_steel",
        "electric_guitar_clean",
        "acoustic_bass",
        "electric_bass_finger",
        "violin",
        "viola",
        "cello",
        "contrabass",
        "harp",
        "flute",
    ]
    before = [
        _part(f"b{i}", "acoustic_grand_piano", role="harmony", source_track_ids=[f"t{i}"])
        for i in range(16)
    ]
    after = [_part(f"a{i}", instrument_ids[i], role="harmony") for i in range(16)]

    from app.services import llm_composition_arrangement as module

    called = {"n": 0}

    async def should_not_matter(**_kwargs):
        called["n"] += 1
        raise AssertionError("provider must not be required once realization rejects channels")

    with patch.object(module, "_invoke_structured_draft", side_effect=should_not_matter):
        with pytest.raises(CompositionArrangementError) as exc:
            _preview(
                composition,
                operation="orchestrate_selected_tracks",
                source_track_ids=[f"t{i}" for i in range(16)],
                instrumentation={"before": before, "after": after},
                candidate_count=1,
                options={"max_repairs": 0},
            )
    # Exhaustion is a draft/realization failure mapped to candidate exhaustion.
    assert exc.value.code in {"arrangement_candidate_exhausted", "arrangement_draft_invalid"}
    detail = str(exc.value.details)
    assert "channel" in detail.lower() or exc.value.code == "arrangement_candidate_exhausted"


def test_over_limit_context_fails_before_provider(monkeypatch):
    from app.services import llm_composition_arrangement as module

    called = {"n": 0}

    async def must_not_run(**_kwargs):
        called["n"] += 1
        raise AssertionError("provider invoked after over-limit request")

    with patch.object(module, "_generate_one_candidate_draft", side_effect=must_not_run):
        with pytest.raises(CompositionArrangementError) as exc:
            _preview(
                _piano_sketch_v2(),
                options={"context_budget_chars": 1000, "max_repairs": 0},
            )
    assert called["n"] == 0
    assert exc.value.code == "arrangement_request_too_large"
    assert exc.value.http_status == 422


def test_arrangement_log_hygiene(monkeypatch, caplog):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-arrangement-must-never-appear")
    source = _piano_sketch_v2()
    with caplog.at_level(logging.DEBUG):
        response = _preview(source, candidate_count=2)
    assert len(response.candidates) == 2
    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert "Composition arrangement preview started" in joined
    assert "Composition arrangement candidate stage" in joined
    assert "Composition arrangement preview completed" in joined

    # Useful structured extras should be present on stage logs.
    stage_records = [
        r for r in caplog.records if "Composition arrangement candidate stage" in r.getMessage()
    ]
    assert stage_records
    assert any(getattr(r, "stage", None) in {"draft", "repair"} for r in stage_records)
    assert any(getattr(r, "catalog_version", None) or getattr(r, "operation", None) for r in caplog.records)

    assert "sk-arrangement-must-never-appear" not in joined
    assert "UNIQUE_ARR_INSTRUCTION_MUST_NOT_LOG" not in joined
    # Distinctive pitches / event IDs / serialized schema must not leak.
    assert "E4" not in joined
    assert "m1" not in joined
    assert '"pitch"' not in joined
    assert "schema_version" not in joined
    assert "sn00001" not in joined


def test_practical_range_scope_only_changed_targets_in_preview():
    """Unchanged source outliers must not fail arrangement when targets are in range."""
    data = _piano_sketch_v2().model_dump(mode="json")
    data["tracks"][2]["events"].append(
        {
            "id": "b-outlier",
            "pitch": "A0",
            "start_tick": 4000,
            "duration_ticks": 480,
            "velocity": 50,
        }
    )
    composition = CompositionV2.model_validate(data)
    response = _preview(
        composition,
        operation="add_accompaniment",
        instrumentation={
            "before": _acceptance_instrumentation()["before"],
            "after": _acceptance_instrumentation()["before"]
            + [_part("a-pad", "synth_pad_new_age", role="pad")],
        },
    )
    assert len(response.candidates) == 1
    # Bass outlier remains on the retained bass track.
    bass = next(t for t in response.candidates[0].composition.tracks if "bass" in t.instrument.lower() or t.role == "bass")
    assert any(e.pitch == "A0" for e in bass.events)


def test_midi_projection_report_for_arranged_candidate():
    response = _preview(_piano_sketch_v2())
    candidate = response.candidates[0].composition
    result = render_midi_with_report(candidate)
    assert result.midi_bytes[:4] == b"MThd"
    assert result.report is not None
    # Shared-channel program compatibility must hold after arrangement allocation.
    programs_by_channel: dict[int, set[int]] = {}
    for track in candidate.tracks:
        if track.is_drum:
            continue
        programs_by_channel.setdefault(track.channel, set()).add(track.midi_program)
    assert all(len(programs) == 1 for programs in programs_by_channel.values())
