import logging

import pytest

from app.schemas import Composition
from app.services.composition_validator import validate_composition_integrity


def _base_composition(**overrides) -> dict:
    payload = {
        "schema_version": "composition.v1",
        "tempo": 80,
        "key": "A minor",
        "time_signature": "4/4",
        "ticks_per_quarter": 480,
        "bar_count": 4,
        "duration_ticks": 7680,
        "sections": [
            {
                "type": "intro",
                "start_bar": 1,
                "bar_count": 2,
                "start_tick": 0,
                "duration_ticks": 3840,
            },
            {
                "type": "outro",
                "start_bar": 3,
                "bar_count": 2,
                "start_tick": 3840,
                "duration_ticks": 3840,
            },
        ],
        "tracks": [
            {
                "id": "melody-1",
                "name": "Melody",
                "instrument": "piano",
                "role": "melody",
                "midi_program": 0,
                "channel": 1,
                "staff": "treble",
                "events": [
                    {"pitch": "A4", "start_tick": 0, "duration_ticks": 480, "velocity": 80},
                    {"pitch": "C5", "start_tick": 1920, "duration_ticks": 480, "velocity": 80},
                    {"pitch": "E5", "start_tick": 3840, "duration_ticks": 480, "velocity": 80},
                    {"pitch": "A4", "start_tick": 5760, "duration_ticks": 480, "velocity": 80},
                ],
            },
            {
                "id": "bass-1",
                "name": "Bass",
                "instrument": "bass",
                "role": "bass",
                "midi_program": 32,
                "channel": 2,
                "events": [
                    {"pitch": "A2", "start_tick": 0, "duration_ticks": 1920, "velocity": 84},
                    {"pitch": "E2", "start_tick": 1920, "duration_ticks": 1920, "velocity": 84},
                    {"pitch": "A2", "start_tick": 3840, "duration_ticks": 1920, "velocity": 84},
                    {"pitch": "E2", "start_tick": 5760, "duration_ticks": 1920, "velocity": 84},
                ],
            },
            {
                "id": "harmony-1",
                "name": "Piano Accompaniment",
                "instrument": "piano",
                "role": "harmony",
                "midi_program": 0,
                "channel": 3,
                "staff": "grand",
                "events": [
                    {"pitch": "A3", "start_tick": 0, "duration_ticks": 960, "velocity": 70, "staff": "bass"},
                    {"pitch": "C5", "start_tick": 0, "duration_ticks": 960, "velocity": 68, "staff": "treble"},
                    {"pitch": "E3", "start_tick": 1920, "duration_ticks": 960, "velocity": 70, "staff": "bass"},
                    {"pitch": "A4", "start_tick": 3840, "duration_ticks": 960, "velocity": 70, "staff": "treble"},
                ],
            },
        ],
        "harmony": [
            {"bar": 1, "chord": "Am"},
            {"bar": 3, "chord": "E"},
        ],
    }
    payload.update(overrides)
    return payload


def test_validator_accepts_valid_composition(caplog):
    caplog.set_level(logging.INFO)
    result = validate_composition_integrity(
        Composition.model_validate(_base_composition()),
        requested_instruments=["piano", "bass"],
        complexity="simple",
    )
    assert result.ok
    assert result.error_codes() == []
    assert "Composition integrity validation passed" in caplog.text


def test_validator_rejects_missing_melody(caplog):
    caplog.set_level(logging.WARNING)
    payload = _base_composition()
    payload["tracks"] = [track for track in payload["tracks"] if track["role"] != "melody"]
    result = validate_composition_integrity(payload, complexity="simple")
    assert not result.ok
    assert "missing_required_track" in result.error_codes()
    assert "Composition integrity validation failed" in caplog.text


def test_validator_rejects_empty_required_track():
    payload = _base_composition()
    payload["tracks"][0]["events"] = []
    result = validate_composition_integrity(payload, complexity="simple")
    assert not result.ok
    assert "empty_required_track" in result.error_codes()


def test_validator_rejects_pitch_out_of_range():
    payload = _base_composition()
    payload["tracks"][1]["events"][0]["pitch"] = "C5"  # too high for bass
    result = validate_composition_integrity(payload, complexity="simple")
    assert not result.ok
    assert "event_out_of_range" in result.error_codes()


def test_validator_allows_strings_cello_register():
    payload = _base_composition()
    payload["tracks"].append(
        {
            "id": "strings-1",
            "name": "Strings",
            "instrument": "strings",
            "role": "pad",
            "midi_program": 48,
            "channel": 3,
            "events": [
                {"pitch": "E2", "start_tick": 0, "duration_ticks": 1920, "velocity": 60},
                {"pitch": "A2", "start_tick": 1920, "duration_ticks": 1920, "velocity": 60},
                {"pitch": "F2", "start_tick": 3840, "duration_ticks": 1920, "velocity": 60},
                {"pitch": "B2", "start_tick": 5760, "duration_ticks": 1920, "velocity": 60},
            ],
        }
    )
    result = validate_composition_integrity(payload, complexity="simple")
    assert result.ok
    assert "event_out_of_range" not in result.error_codes()


def test_validator_bass_density_softer_than_melody_for_complex():
    payload = _base_composition()
    # 4 bars, complex melody/harmony need 6 events; bass only needs ~2 (0.5/bar).
    payload["tracks"][0]["events"] = [
        {"pitch": "A4", "start_tick": i * 480, "duration_ticks": 480, "velocity": 80} for i in range(6)
    ]
    payload["tracks"][1]["events"] = [
        {"pitch": "A2", "start_tick": 0, "duration_ticks": 3840, "velocity": 84},
        {"pitch": "E2", "start_tick": 3840, "duration_ticks": 3840, "velocity": 84},
    ]
    payload["tracks"][2]["events"] = [
        {"pitch": "A3", "start_tick": i * 480, "duration_ticks": 480, "velocity": 70, "staff": "bass"}
        for i in range(6)
    ]
    result = validate_composition_integrity(payload, complexity="complex")
    assert result.ok
    assert "empty_required_track" not in result.error_codes()


def test_validator_rejects_event_outside_composition():
    payload = _base_composition()
    payload["tracks"][0]["events"][0]["start_tick"] = 7500
    payload["tracks"][0]["events"][0]["duration_ticks"] = 480
    result = validate_composition_integrity(payload, complexity="simple")
    assert not result.ok
    assert "event_out_of_range" in result.error_codes() or "schema_invalid" in result.error_codes()


def test_validator_rejects_harmony_only_substitution():
    payload = _base_composition()
    for track in payload["tracks"]:
        track["events"] = []
    result = validate_composition_integrity(payload, complexity="simple")
    assert not result.ok
    assert "harmony_only" in result.error_codes() or "empty_required_track" in result.error_codes()


def test_validator_does_not_emit_missing_requested_instrument():
    """Requested-instrument conformance is owned by generation constraints."""
    result = validate_composition_integrity(
        _base_composition(),
        requested_instruments=["piano", "bass", "strings"],
        complexity="simple",
    )
    assert result.ok
    assert all(item.code != "missing_requested_instrument" for item in result.diagnostics)


def test_generation_owns_missing_requested_instrument_diagnostic():
    from app.schemas import LLMMusicGenerationRequest, LLMPromptParameters
    from app.services.generation_constraints import (
        build_generation_constraints,
        validate_generation_constraints,
    )

    request = LLMMusicGenerationRequest(
        prompt=LLMPromptParameters(
            key="A minor",
            duration_bars=4,
            time_signature="4/4",
            tempo_min=70,
            tempo_max=90,
            instruments=["piano", "bass", "strings"],
        )
    )
    constraints = build_generation_constraints(request)
    integrity = validate_composition_integrity(
        _base_composition(),
        requested_instruments=request.prompt.instruments,
        complexity="simple",
    )
    report = validate_generation_constraints(
        Composition.model_validate(_base_composition()),
        constraints,
    )
    assert integrity.ok
    assert all(item.code != "missing_requested_instrument" for item in integrity.diagnostics)
    assert any(item.code == "constraint_missing_instrument_family" for item in report.errors)
    # Single ownership: only the generation constraint code is present.
    assert "missing_requested_instrument" not in {item.code for item in report.errors}


def test_validator_warns_drum_channel_semantics():
    payload = _base_composition()
    payload["tracks"].append(
        {
            "id": "drums-1",
            "name": "Drums",
            "instrument": "drums",
            "role": "drums",
            "midi_program": 0,
            "channel": 1,
            "is_drum": True,
            "events": [],
        }
    )
    result = validate_composition_integrity(payload, complexity="simple")
    assert result.ok
    assert any(item.code == "track_range_semantics" for item in result.warnings)


def test_validate_generation_constraints_flags_tempo_meter_and_instruments():
    from app.schemas import LLMMusicGenerationRequest, LLMPromptParameters
    from app.services.generation_constraints import (
        build_generation_constraints,
        validate_generation_constraints,
    )

    request = LLMMusicGenerationRequest(
        prompt=LLMPromptParameters(
            key="A minor",
            duration_bars=4,
            time_signature="4/4",
            tempo_min=80,
            tempo_max=90,
            instruments=["piano", "bass", "strings"],
        )
    )
    constraints = build_generation_constraints(request)
    payload = _base_composition(tempo=100, key="A minor")
    # Missing strings family.
    report = validate_generation_constraints(Composition.model_validate(payload), constraints)
    assert not report.ok
    codes = {item.code for item in report.errors}
    assert "constraint_tempo_out_of_range" in codes
    assert "constraint_missing_instrument_family" in codes
    assert report.instrumentation is not None
    assert [item.key for item in report.instrumentation.missing] == ["strings"]
    assert {item.key for item in report.instrumentation.satisfied} == {"piano", "bass"}


def test_validate_generation_constraints_reports_instrumentation_on_success():
    from app.schemas import LLMMusicGenerationRequest, LLMPromptParameters
    from app.services.generation_constraints import (
        build_generation_constraints,
        validate_generation_constraints,
    )

    request = LLMMusicGenerationRequest(
        prompt=LLMPromptParameters(
            key="A minor",
            duration_bars=4,
            time_signature="4/4",
            tempo_min=70,
            tempo_max=90,
            instruments=["piano", "bass"],
        )
    )
    constraints = build_generation_constraints(request)
    report = validate_generation_constraints(
        Composition.model_validate(_base_composition()),
        constraints,
    )
    assert report.ok
    assert report.instrumentation is not None
    assert report.instrumentation.missing == []
    assert {item.key for item in report.instrumentation.satisfied} == {"piano", "bass"}
    assert report.instrumentation.suspicious_duplicates == []
    assert "duplicate_instruments" in report.constraints_checked


def test_validate_generation_constraints_flags_actionable_duplicates():
    from app.schemas import LLMMusicGenerationRequest, LLMPromptParameters
    from app.services.generation_constraints import (
        DUPLICATE_INSTRUMENT_ROLE_CODE,
        build_generation_constraints,
        diagnostics_from_validation_report,
        validate_generation_constraints,
    )

    request = LLMMusicGenerationRequest(
        prompt=LLMPromptParameters(
            key="A minor",
            duration_bars=4,
            time_signature="4/4",
            tempo_min=70,
            tempo_max=90,
            instruments=["piano", "bass"],
        )
    )
    constraints = build_generation_constraints(request)
    payload = _base_composition()
    # Exact same-purpose bass duplicate.
    payload["tracks"].append(
        {
            "id": "bass-2",
            "name": "Bass Double",
            "instrument": "bass",
            "role": "bass",
            "midi_program": 32,
            "channel": 4,
            "events": [
                {"pitch": "A2", "start_tick": 0, "duration_ticks": 1920, "velocity": 84},
                {"pitch": "E2", "start_tick": 1920, "duration_ticks": 1920, "velocity": 84},
                {"pitch": "A2", "start_tick": 3840, "duration_ticks": 1920, "velocity": 84},
                {"pitch": "E2", "start_tick": 5760, "duration_ticks": 1920, "velocity": 84},
            ],
        }
    )
    report = validate_generation_constraints(Composition.model_validate(payload), constraints)
    assert not report.ok
    assert any(item.code == DUPLICATE_INSTRUMENT_ROLE_CODE for item in report.errors)
    assert report.instrumentation is not None
    assert any(item.actionable for item in report.instrumentation.suspicious_duplicates)
    diagnostics = diagnostics_from_validation_report(report)
    duplicate = next(item for item in diagnostics if item.code == DUPLICATE_INSTRUMENT_ROLE_CODE)
    assert duplicate.context.get("stage") == "compose_accompaniment"
    assert duplicate.context.get("repairable") is True


def test_prompt_sections_must_sum_to_duration_bars():
    from pydantic import ValidationError

    from app.schemas import LLMPromptParameters

    with pytest.raises(ValidationError):
        LLMPromptParameters(
            duration_bars=16,
            sections=[{"type": "verse", "bars": 8}, {"type": "chorus", "bars": 4}],
        )


def test_validator_detects_bar_overflow_density():
    payload = _base_composition()
    # Pack many overlapping full-bar events into bar 1 to trip density overflow.
    payload["tracks"][0]["events"] = [
        {"pitch": "A4", "start_tick": 0, "duration_ticks": 1920, "velocity": 80}
        for _ in range(8)
    ] + [
        {"pitch": "C5", "start_tick": 1920, "duration_ticks": 480, "velocity": 80},
        {"pitch": "E5", "start_tick": 3840, "duration_ticks": 480, "velocity": 80},
        {"pitch": "A4", "start_tick": 5760, "duration_ticks": 480, "velocity": 80},
    ]
    result = validate_composition_integrity(payload, complexity="simple")
    assert not result.ok
    assert "bar_overflow" in result.error_codes()


def test_validator_accepts_native_v2_expressive_fixture():
    from app.services.fixture_compositions import FIXTURE_V2_EXPRESSIVE, load_composition_fixture

    composition = load_composition_fixture(FIXTURE_V2_EXPRESSIVE)
    result = validate_composition_integrity(composition, complexity="simple")
    assert result.ok
    assert composition.tempo_changes
    assert any(event.articulations for track in composition.tracks for event in track.events)


def test_canonical_profile_accepts_imported_solo_other_unsectioned():
    payload = {
        "schema_version": "composition.v2",
        "tempo": 120,
        "key": "C major",
        "time_signature": "4/4",
        "ticks_per_quarter": 480,
        "bar_count": 1,
        "duration_ticks": 1920,
        "sections": [
            {
                "id": "section-1",
                "type": "unsectioned",
                "start_bar": 1,
                "bar_count": 1,
                "start_tick": 0,
                "duration_ticks": 1920,
            }
        ],
        "tracks": [
            {
                "id": "track-1",
                "name": "Imported",
                "instrument": "piano",
                "role": "other",
                "midi_program": 0,
                "channel": 1,
                "events": [
                    {"type": "note", "pitch": "C4", "start_tick": 0, "duration_ticks": 480, "velocity": 80},
                ],
                "dynamic_marks": [],
                "sustain_pedals": [],
                "automation": [],
            }
        ],
        "harmony": [],
        "tempo_changes": [],
        "time_signature_changes": [],
        "key_changes": [],
        "markers": [],
    }
    generation = validate_composition_integrity(payload, profile="generation", complexity="simple")
    assert not generation.ok
    assert "missing_required_track" in generation.error_codes()

    canonical = validate_composition_integrity(payload, profile="canonical")
    assert canonical.ok


def test_canonical_profile_accepts_mixed_meter_timeline():
    payload = {
        "schema_version": "composition.v2",
        "tempo": 100,
        "key": "C major",
        "time_signature": "4/4",
        "ticks_per_quarter": 480,
        "bar_count": 2,
        "duration_ticks": 1920 + 1440,
        "sections": [
            {
                "id": "section-1",
                "type": "unsectioned",
                "start_bar": 1,
                "bar_count": 2,
                "start_tick": 0,
                "duration_ticks": 3360,
            }
        ],
        "tracks": [
            {
                "id": "track-1",
                "name": "Solo",
                "instrument": "violin",
                "role": "other",
                "midi_program": 40,
                "channel": 1,
                "events": [
                    {"type": "note", "pitch": "E5", "start_tick": 0, "duration_ticks": 480, "velocity": 90},
                    {"type": "note", "pitch": "G5", "start_tick": 1920, "duration_ticks": 480, "velocity": 90},
                ],
                "dynamic_marks": [],
                "sustain_pedals": [],
                "automation": [],
            }
        ],
        "harmony": [],
        "tempo_changes": [],
        "time_signature_changes": [{"tick": 1920, "time_signature": "3/4"}],
        "key_changes": [],
        "markers": [],
    }
    # Generation profile wrongly assumes constant meter for duration equality.
    generation = validate_composition_integrity(payload, profile="generation", complexity="simple")
    assert not generation.ok

    canonical = validate_composition_integrity(payload, profile="canonical")
    assert canonical.ok
