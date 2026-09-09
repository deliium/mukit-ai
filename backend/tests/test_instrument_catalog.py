"""Tests for the versioned arrangement instrument catalog."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.composition_schemas import Composition, CompositionV2, SUPPORTED_TRACK_ROLES
from app.services.instrument_catalog import (
    CATALOG_VERSION,
    RANGE_POLICY_VERSION,
    InstrumentCatalogError,
    canonical_track_roles,
    clear_catalog_cache,
    find_baseline_mismatches,
    get_catalog_meta,
    list_profiles,
    load_catalog,
    reload_catalog,
    resolve_profile,
    resolve_profile_for_track,
)
from app.services.instrument_identity import (
    family_for_identity,
    normalize_instrument_family,
    normalize_instrument_identity,
)


@pytest.fixture(autouse=True)
def _reset_catalog_cache():
    clear_catalog_cache()
    yield
    clear_catalog_cache()


@dataclass
class FakeTrack:
    id: str
    instrument: str
    midi_program: int
    channel: int
    is_drum: bool = False
    role: str = "other"


def test_packaged_catalog_schema_version_and_fingerprints():
    catalog = load_catalog()
    assert catalog.catalog_version == CATALOG_VERSION
    assert catalog.range_policy_version == RANGE_POLICY_VERSION
    assert catalog.source_path_category == "packaged"
    assert len(catalog.fingerprint) == 64
    assert all(len(profile.fingerprint) == 64 for profile in catalog.profiles)
    assert len({profile.fingerprint for profile in catalog.profiles}) == len(catalog.profiles)
    meta = get_catalog_meta()
    assert meta.fingerprint == catalog.fingerprint
    assert meta.profile_count == len(catalog.profiles)
    assert meta.source_path_category == "packaged"


def test_stable_common_program_vectors():
    expected = {
        "acoustic_grand_piano": 0,
        "bright_acoustic_piano": 1,
        "electric_piano_1": 4,
        "electric_piano_2": 5,
        "church_organ": 19,
        "acoustic_guitar_nylon": 24,
        "acoustic_guitar_steel": 25,
        "electric_guitar_clean": 27,
        "acoustic_bass": 32,
        "electric_bass_finger": 33,
        "violin": 40,
        "viola": 41,
        "cello": 42,
        "contrabass": 43,
        "harp": 46,
        "string_ensemble_1": 48,
        "choir_aahs": 52,
        "trumpet": 56,
        "trombone": 57,
        "tuba": 58,
        "french_horn": 60,
        "soprano_sax": 64,
        "alto_sax": 65,
        "tenor_sax": 66,
        "baritone_sax": 67,
        "oboe": 68,
        "english_horn": 69,
        "bassoon": 70,
        "clarinet": 71,
        "piccolo": 72,
        "flute": 73,
        "synth_lead_square": 80,
        "synth_pad_new_age": 88,
        "standard_drum_kit": 0,
    }
    profiles = {profile.instrument_id: profile for profile in list_profiles()}
    assert set(expected) <= set(profiles)
    for instrument_id, program in expected.items():
        assert profiles[instrument_id].midi_program == program
    assert profiles["standard_drum_kit"].is_drum is True
    assert resolve_profile(instrument_id="cello").midi_program == 42
    assert resolve_profile(midi_program=0).instrument_id == "acoustic_grand_piano"
    assert resolve_profile(midi_program=0, is_drum=True).instrument_id == "standard_drum_kit"


def test_override_load_and_invalid_override_failure(tmp_path: Path):
    packaged = load_catalog()
    override = tmp_path / "custom_catalog.json"
    payload = {
        "catalog_version": CATALOG_VERSION,
        "range_policy_version": RANGE_POLICY_VERSION,
        "instruments": [
            {
                "instrument_id": "acoustic_grand_piano",
                "display_name": "Acoustic Grand Piano",
                "aliases": ["piano"],
                "midi_program": 0,
                "gm_family": "piano",
                "compatibility_identity": "piano",
                "compatibility_family": "piano",
                "is_drum": False,
                "range_policy": "absolute",
                "playable_low": 21,
                "playable_high": 108,
                "preferred_low": 24,
                "preferred_high": 96,
                "suggested_roles": ["melody"],
            }
        ],
    }
    override.write_text(json.dumps(payload), encoding="utf-8")
    custom = load_catalog(env={"ARRANGEMENT_INSTRUMENT_CATALOG_PATH": str(override)})
    assert custom.source_path_category == "override"
    assert len(custom.profiles) == 1
    assert custom.fingerprint != packaged.fingerprint

    bad = tmp_path / "bad.json"
    bad.write_text("{not-json", encoding="utf-8")
    with pytest.raises(InstrumentCatalogError) as exc_info:
        load_catalog(env={"ARRANGEMENT_INSTRUMENT_CATALOG_PATH": str(bad)})
    assert exc_info.value.code == "catalog_invalid_json"
    assert exc_info.value.path_category == "override"


def test_container_visible_absolute_path_required(tmp_path: Path):
    relative = "relative_catalog.json"
    (tmp_path / relative).write_text("{}", encoding="utf-8")
    with pytest.raises(InstrumentCatalogError) as exc_info:
        load_catalog(env={"ARRANGEMENT_INSTRUMENT_CATALOG_PATH": relative})
    assert exc_info.value.code == "catalog_invalid_path"
    assert exc_info.value.path_category == "relative_rejected"

    missing = tmp_path / "missing.json"
    with pytest.raises(InstrumentCatalogError) as missing_info:
        load_catalog(env={"ARRANGEMENT_INSTRUMENT_CATALOG_PATH": str(missing)})
    assert missing_info.value.code == "catalog_missing_file"
    assert missing_info.value.path_category == "missing"


def test_alias_collision_rejected(tmp_path: Path):
    payload = {
        "catalog_version": CATALOG_VERSION,
        "range_policy_version": RANGE_POLICY_VERSION,
        "instruments": [
            {
                "instrument_id": "a",
                "display_name": "A",
                "aliases": ["shared"],
                "midi_program": 0,
                "gm_family": "piano",
                "compatibility_identity": "piano",
                "compatibility_family": "piano",
                "is_drum": False,
                "range_policy": "unbounded",
                "playable_low": None,
                "playable_high": None,
                "preferred_low": None,
                "preferred_high": None,
                "suggested_roles": ["other"],
            },
            {
                "instrument_id": "b",
                "display_name": "B",
                "aliases": ["Shared"],
                "midi_program": 1,
                "gm_family": "piano",
                "compatibility_identity": "piano",
                "compatibility_family": "piano",
                "is_drum": False,
                "range_policy": "unbounded",
                "playable_low": None,
                "playable_high": None,
                "preferred_low": None,
                "preferred_high": None,
                "suggested_roles": ["other"],
            },
        ],
    }
    path = tmp_path / "collision.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(InstrumentCatalogError) as exc_info:
        load_catalog(env={"ARRANGEMENT_INSTRUMENT_CATALOG_PATH": str(path)})
    assert exc_info.value.code == "catalog_duplicate_alias"


def test_all_absolute_range_boundaries():
    for profile in list_profiles():
        if profile.range_policy != "absolute":
            continue
        assert profile.playable_low is not None
        assert profile.preferred_low is not None
        assert profile.preferred_high is not None
        assert profile.playable_high is not None
        assert (
            0
            <= profile.playable_low
            <= profile.preferred_low
            <= profile.preferred_high
            <= profile.playable_high
            <= 127
        )
    piano = resolve_profile(instrument_id="acoustic_grand_piano")
    assert piano is not None
    assert piano.playable_low == 21
    assert piano.playable_high == 108
    cello = resolve_profile(instrument_id="cello")
    assert cello is not None
    assert cello.playable_low == 36
    assert cello.playable_high == 72


def test_bassoon_and_english_horn_regressions():
    assert normalize_instrument_identity("bassoon") == "bassoon"
    assert normalize_instrument_identity("Bassoon") == "bassoon"
    assert normalize_instrument_family("bassoon") == "woodwind"
    assert normalize_instrument_identity("bassoon") != "bass"
    assert normalize_instrument_identity("bassoon") != normalize_instrument_identity("bass")

    assert normalize_instrument_identity("english horn") == "english_horn"
    assert normalize_instrument_identity("English Horn") == "english_horn"
    assert normalize_instrument_identity("englishhorn") == "english_horn"
    assert family_for_identity("english_horn") == "woodwind"
    assert normalize_instrument_family("english horn") == "woodwind"
    # Bare horn remains french-horn/brass identity; english horn must not collapse to it.
    assert normalize_instrument_identity("horn") == "horn"
    assert normalize_instrument_family("horn") == "brass"

    bassoon = resolve_profile(instrument_label="bassoon")
    assert bassoon is not None
    assert bassoon.instrument_id == "bassoon"
    assert bassoon.midi_program == 70
    assert bassoon.compatibility_identity == "bassoon"
    assert bassoon.compatibility_family == "woodwind"

    english = resolve_profile(instrument_label="English Horn")
    assert english is not None
    assert english.instrument_id == "english_horn"
    assert english.midi_program == 69
    assert english.compatibility_family == "woodwind"


def test_source_program_precedence_over_coarse_identity():
    # Unknown label + curated program resolves by program before coarse identity.
    profile = resolve_profile(midi_program=42, instrument_label="Mystery Solo Voice")
    assert profile is not None
    assert profile.instrument_id == "cello"

    track = FakeTrack(
        id="t1",
        instrument="Custom Lead Instrument",
        midi_program=80,
        channel=1,
    )
    resolved = resolve_profile_for_track(track)
    assert resolved is not None
    assert resolved.instrument_id == "synth_lead_square"


def test_unknown_and_unbounded_policies():
    lead = resolve_profile(instrument_id="synth_lead_square")
    pad = resolve_profile(instrument_id="synth_pad_new_age")
    drums = resolve_profile(instrument_id="standard_drum_kit")
    assert lead is not None and lead.range_policy == "unbounded"
    assert pad is not None and pad.range_policy == "unbounded"
    assert drums is not None and drums.range_policy == "unknown"
    for profile in (lead, pad, drums):
        assert profile.playable_low is None
        assert profile.playable_high is None
        assert profile.preferred_low is None
        assert profile.preferred_high is None


def test_canonical_role_list_independent_of_suggestions():
    roles = canonical_track_roles()
    assert roles == sorted(SUPPORTED_TRACK_ROLES)
    suggested = {
        role
        for profile in list_profiles()
        for role in profile.suggested_roles
    }
    # Catalog suggestions are a subset; canonical list remains the full vocabulary.
    assert suggested <= SUPPORTED_TRACK_ROLES
    assert set(roles) == SUPPORTED_TRACK_ROLES
    assert "other" in roles


def test_baseline_mismatches_do_not_mutate():
    tracks = [
        FakeTrack(id="ok", instrument="cello", midi_program=42, channel=1),
        FakeTrack(id="prog", instrument="cello", midi_program=0, channel=1),
        FakeTrack(id="drum-ch", instrument="drums", midi_program=0, channel=1, is_drum=True),
    ]
    before = [(t.instrument, t.midi_program, t.channel, t.is_drum) for t in tracks]
    findings = find_baseline_mismatches(tracks)
    after = [(t.instrument, t.midi_program, t.channel, t.is_drum) for t in tracks]
    assert before == after
    codes = {item.track_id: item.code for item in findings}
    assert "ok" not in codes
    assert codes["prog"] == "catalog_program_mismatch"
    assert codes["drum-ch"] == "catalog_channel_mismatch"


def test_composition_schemas_still_load_smoke():
    # Catalog addition must not disturb V1/V2 schema import/construction.
    assert Composition is not None
    assert CompositionV2 is not None
    v2 = CompositionV2.model_validate(
        {
            "schema_version": "composition.v2",
            "tempo": 120,
            "key": "C major",
            "time_signature": "4/4",
            "ticks_per_quarter": 480,
            "duration_ticks": 1920,
            "bar_count": 1,
            "sections": [
                {
                    "id": "s1",
                    "type": "verse",
                    "label": "A",
                    "start_bar": 1,
                    "bar_count": 1,
                    "start_tick": 0,
                    "duration_ticks": 1920,
                }
            ],
            "tracks": [
                {
                    "id": "t1",
                    "name": "Piano",
                    "instrument": "piano",
                    "midi_program": 0,
                    "channel": 1,
                    "role": "melody",
                    "events": [
                        {
                            "type": "note",
                            "pitch": "C4",
                            "start_tick": 0,
                            "duration_ticks": 480,
                            "velocity": 80,
                        }
                    ],
                }
            ],
            "harmony": [],
        }
    )
    assert v2.schema_version == "composition.v2"
    assert v2.tracks[0].midi_program == 0


def test_reload_uses_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    override = tmp_path / "one.json"
    override.write_text(
        json.dumps(
            {
                "catalog_version": CATALOG_VERSION,
                "range_policy_version": RANGE_POLICY_VERSION,
                "instruments": [
                    {
                        "instrument_id": "acoustic_grand_piano",
                        "display_name": "Acoustic Grand Piano",
                        "aliases": ["piano"],
                        "midi_program": 0,
                        "gm_family": "piano",
                        "compatibility_identity": "piano",
                        "compatibility_family": "piano",
                        "is_drum": False,
                        "range_policy": "unbounded",
                        "playable_low": None,
                        "playable_high": None,
                        "preferred_low": None,
                        "preferred_high": None,
                        "suggested_roles": ["melody"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ARRANGEMENT_INSTRUMENT_CATALOG_PATH", str(override))
    catalog = reload_catalog()
    assert len(catalog.profiles) == 1
    assert catalog.source_path_category == "override"
