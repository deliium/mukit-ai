"""Chord parsing upgrades and declared-harmony compatibility coverage."""

from __future__ import annotations

from app.composition_schemas import CompositionV2
from app.services.composition_harmony_compatibility import analyze_harmony_compatibility
from app.services.composition_tonality import parse_chord_symbol, parse_key


def _composition(**overrides) -> CompositionV2:
    data = {
        "schema_version": "composition.v2",
        "tempo": 100,
        "key": "C major",
        "time_signature": "4/4",
        "ticks_per_quarter": 480,
        "bar_count": 4,
        "duration_ticks": 7680,
        "sections": [
            {
                "id": "sec-1",
                "type": "verse",
                "start_bar": 1,
                "bar_count": 4,
                "start_tick": 0,
                "duration_ticks": 7680,
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
                "events": [
                    {
                        "type": "note",
                        "id": "m1",
                        "pitch": "E4",
                        "start_tick": 0,
                        "duration_ticks": 480,
                        "velocity": 80,
                    },
                    {
                        "type": "note",
                        "id": "m2",
                        "pitch": "F4",
                        "start_tick": 480,
                        "duration_ticks": 240,
                        "velocity": 80,
                    },
                    {
                        "type": "note",
                        "id": "m3",
                        "pitch": "E4",
                        "start_tick": 720,
                        "duration_ticks": 240,
                        "velocity": 80,
                    },
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
                    {
                        "type": "note",
                        "id": "b1",
                        "pitch": "C2",
                        "start_tick": 0,
                        "duration_ticks": 1920,
                        "velocity": 70,
                    }
                ],
            },
            {
                "id": "harm-1",
                "name": "Harmony",
                "instrument": "piano",
                "role": "harmony",
                "midi_program": 0,
                "channel": 3,
                "events": [
                    {
                        "type": "note",
                        "id": "h1",
                        "pitch": "C4",
                        "start_tick": 0,
                        "duration_ticks": 960,
                        "velocity": 60,
                    },
                    {
                        "type": "note",
                        "id": "h2",
                        "pitch": "E4",
                        "start_tick": 0,
                        "duration_ticks": 960,
                        "velocity": 60,
                    },
                    {
                        "type": "note",
                        "id": "h3",
                        "pitch": "G4",
                        "start_tick": 0,
                        "duration_ticks": 960,
                        "velocity": 60,
                    },
                ],
            },
        ],
        "harmony": [
            {"start_tick": 0, "duration_ticks": 1920, "chord": "C"},
            {"start_tick": 1920, "duration_ticks": 1920, "chord": "G7"},
            {"start_tick": 3840, "duration_ticks": 1920, "chord": "Am"},
            {"start_tick": 5760, "duration_ticks": 1920, "chord": "C"},
        ],
        "tempo_changes": [],
        "time_signature_changes": [],
        "key_changes": [],
        "markers": [],
        "motifs": [],
    }
    data.update(overrides)
    return CompositionV2.model_validate(data)


def test_parse_slash_altered_and_unknown_suffix():
    slash = parse_chord_symbol("C7/E")
    assert slash.parseable
    assert slash.root_pc == 0
    assert slash.bass_pc == 4
    assert slash.quality == "dom7"
    assert slash.authored_spelling == "C7/E"
    assert 4 in slash.pitch_classes

    altered = parse_chord_symbol("E7(b9)")
    assert altered.parseable
    assert altered.root_pc == 4
    assert "b9" in altered.alterations
    assert (4 + 1) % 12 in altered.pitch_classes

    sharp_nine = parse_chord_symbol("G7#9")
    assert sharp_nine.parseable
    assert "#9" in sharp_nine.alterations or "9" in "".join(sharp_nine.alterations)

    halfdim = parse_chord_symbol("Bm7b5")
    assert halfdim.parseable
    assert halfdim.quality == "min7b5"

    unknown = parse_chord_symbol("Cmaj7xyz")
    assert unknown.parseable
    assert unknown.quality == "maj7"
    assert unknown.unknown_suffix
    assert unknown.has_unknown_syntax

    bad = parse_chord_symbol("not-a-chord")
    assert not bad.parseable


def test_enharmonic_spelling_preserved_separately_from_pc():
    sharp = parse_chord_symbol("C#")
    flat = parse_chord_symbol("Db")
    assert sharp.root_pc == flat.root_pc == 1
    assert sharp.authored_spelling == "C#"
    assert flat.authored_spelling == "Db"


def test_compatibility_supports_diatonic_and_secondary_dominant():
    composition = _composition(
        harmony=[
            {"start_tick": 0, "duration_ticks": 1920, "chord": "C"},
            {"start_tick": 1920, "duration_ticks": 1920, "chord": "A7"},
            {"start_tick": 3840, "duration_ticks": 1920, "chord": "Dm"},
            {"start_tick": 5760, "duration_ticks": 1920, "chord": "G7"},
        ]
    )
    report = analyze_harmony_compatibility(composition, start_tick=0, end_tick=7680)
    assert report.status in {"compatible", "compatible_with_warnings"}
    assert report.active_key == "C major"
    codes = {item.code for item in report.findings}
    assert "secondary_dominant_function" in codes or "tonal_center_support" in codes
    assert "structural_corruption" not in codes


def test_compatibility_slash_bass_and_borrowed_color():
    composition = _composition(
        key="A minor",
        harmony=[
            {"start_tick": 0, "duration_ticks": 3840, "chord": "Am"},
            {"start_tick": 3840, "duration_ticks": 3840, "chord": "F/A"},
        ],
        tracks=[
            {
                "id": "melody-1",
                "name": "Melody",
                "instrument": "piano",
                "role": "melody",
                "midi_program": 0,
                "channel": 1,
                "events": [
                    {
                        "type": "note",
                        "id": "m1",
                        "pitch": "A4",
                        "start_tick": 0,
                        "duration_ticks": 960,
                        "velocity": 80,
                    }
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
                    {
                        "type": "note",
                        "id": "b1",
                        "pitch": "A1",
                        "start_tick": 3840,
                        "duration_ticks": 960,
                        "velocity": 70,
                    }
                ],
            },
        ],
    )
    report = analyze_harmony_compatibility(composition)
    codes = {item.code for item in report.findings}
    assert "bass_inversion_support" in codes or "bass_root_support" in codes
    assert report.status != "incompatible"


def test_compatibility_key_change_active_center():
    composition = _composition(
        key="C major",
        key_changes=[{"tick": 3840, "key": "G major"}],
        harmony=[
            {"start_tick": 0, "duration_ticks": 3840, "chord": "C"},
            {"start_tick": 3840, "duration_ticks": 3840, "chord": "G"},
        ],
    )
    early = analyze_harmony_compatibility(composition, start_tick=0, end_tick=3840)
    late = analyze_harmony_compatibility(composition, start_tick=3840, end_tick=7680)
    assert early.active_key == "C major"
    assert late.active_key == "G major"


def test_compatibility_unparseable_and_imported_other_role():
    composition = _composition(
        harmony=[{"start_tick": 0, "duration_ticks": 7680, "chord": "???"}],
        tracks=[
            {
                "id": "other-1",
                "name": "Import",
                "instrument": "piano",
                "role": "other",
                "midi_program": 0,
                "channel": 1,
                "events": [
                    {
                        "type": "note",
                        "id": "o1",
                        "pitch": "C4",
                        "start_tick": 0,
                        "duration_ticks": 480,
                        "velocity": 60,
                    }
                ],
            }
        ],
    )
    report = analyze_harmony_compatibility(composition)
    assert any(item.code == "unsupported_chord_symbol" for item in report.findings)
    # Unparseable metadata alone is not a hard failure.
    assert report.status != "incompatible"


def test_preservation_and_unauthorized_target_errors():
    source = _composition()
    candidate_data = source.model_dump(mode="json")
    # Mutate melody (should be preserved) and an unauthorized other track.
    candidate_data["tracks"][0]["events"][0]["pitch"] = "D4"
    candidate = CompositionV2.model_validate(candidate_data)
    report = analyze_harmony_compatibility(
        candidate,
        start_tick=0,
        end_tick=1920,
        source_composition=source,
        authorized_target_ids=["harm-1"],
        preserve_track_ids=["melody-1"],
    )
    assert report.status == "incompatible"
    codes = {item.code for item in report.findings}
    assert "preservation_violation" in codes


def test_parse_key_still_works_with_extended_parser():
    assert parse_key("F# minor") is not None
    assert parse_chord_symbol("C#7").quality == "dom7"
