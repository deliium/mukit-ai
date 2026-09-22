"""Unit tests for composer.profile.v1 schema validation and forbid gates."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.composer_profile_schemas import (
    COMPOSER_PROFILE_SCHEMA,
    ComposerProfileExportV1,
    ComposerProfileV1,
    DerivedPreferenceFields,
    PreferenceFields,
    empty_composer_profile,
    preference_fields_to_dict,
)


def _minimal_profile(**overrides):
    base = {
        "id": "prof_abc123",
        "name": "My Cinematic Style",
        "created_at": "2026-09-22T12:00:00Z",
        "updated_at": "2026-09-22T12:00:00Z",
    }
    base.update(overrides)
    return ComposerProfileV1.model_validate(base)


def test_minimal_profile_validates():
    profile = _minimal_profile()
    assert profile.schema_version == COMPOSER_PROFILE_SCHEMA
    assert profile.explicit.model_dump(exclude_none=True) == {
        "chord_vocabulary": [],
        "preferred_instruments": [],
    } or preference_fields_to_dict(profile.explicit) == {}
    assert profile.derived.stats_meta is None
    assert profile.source_projects == []


def test_preference_fields_accept_bands_and_lists():
    fields = PreferenceFields.model_validate(
        {
            "midi_mean_band": "high",
            "rhythmic_density_band": "moderate",
            "chord_vocabulary": ["I", "V", "vi"],
            "preferred_instruments": ["Strings", "strings", "Piano"],
            "tension_shape": "arch",
            "interval_histogram": {"bins": [0.1, 0.2, 0.0]},
            "preferred_forms": {"counts": {"verse": 2.0, "chorus": 1.5}},
        }
    )
    assert fields.midi_mean_band == "high"
    assert fields.preferred_instruments == ["Strings", "Piano"]
    assert fields.interval_histogram is not None
    assert len(fields.interval_histogram.bins) == 3


def test_forbid_events_and_tracks_at_root():
    with pytest.raises(ValidationError) as exc:
        ComposerProfileV1.model_validate(
            {
                "id": "prof_1",
                "name": "Bad",
                "created_at": "2026-09-22T12:00:00Z",
                "updated_at": "2026-09-22T12:00:00Z",
                "events": [{"pitch": 60}],
                "tracks": [],
            }
        )
    assert "forbidden" in str(exc.value).lower()


def test_forbid_nested_analysis_report_and_embedding_vector():
    with pytest.raises(ValidationError):
        PreferenceFields.model_validate({"analysis_report": {"foo": 1}})
    with pytest.raises(ValidationError):
        DerivedPreferenceFields.model_validate(
            {"stats_meta": {"embedding": {"vector": [0.1, 0.2]}}}
        )
    with pytest.raises(ValidationError):
        PreferenceFields.model_validate({"artist": "Someone"})
    with pytest.raises(ValidationError):
        PreferenceFields.model_validate({"composer_id": "x"})


def test_forbid_extra_unknown_keys():
    with pytest.raises(ValidationError):
        PreferenceFields.model_validate({"secret_sauce": "nope"})


def test_export_envelope_wraps_profile():
    profile = empty_composer_profile(
        profile_id="prof_export",
        name="Exportable",
        created_at="2026-09-22T12:00:00Z",
    )
    envelope = ComposerProfileExportV1.model_validate(
        {
            "exported_at": "2026-09-22T13:00:00Z",
            "profile": profile.model_dump(),
        }
    )
    assert envelope.schema_version == "composer.profile.export.v1"
    assert envelope.profile.id == "prof_export"


def test_invalid_band_rejected():
    with pytest.raises(ValidationError):
        PreferenceFields.model_validate({"rhythmic_density_band": "ludicrous"})


def test_preference_fields_to_dict_omits_empties():
    fields = PreferenceFields(midi_mean_band="low")
    payload = preference_fields_to_dict(fields)
    assert payload == {"midi_mean_band": "low"}
