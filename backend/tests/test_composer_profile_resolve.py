"""Tests for profile resolve + strength-scaled soft fragments."""

from __future__ import annotations

from app.composer_profile_schemas import (
    DerivedPreferenceFields,
    PreferenceFields,
    empty_composer_profile,
)
from app.services.composer_profile_resolve import (
    build_soft_fragment,
    preview_profile_fragment,
    promote_derived_to_explicit,
    resolve_preference_view,
)


def _sample_profile():
    profile = empty_composer_profile(
        profile_id="prof_resolve",
        name="Resolve Test",
        created_at="2026-09-22T12:00:00Z",
    )
    return profile.model_copy(
        update={
            "derived": DerivedPreferenceFields(
                midi_mean_band="high",
                rhythmic_density_band="moderate",
                preferred_instruments=["Strings", "Piano"],
                tension_shape="arch",
                syncopation_band="low",
                chord_vocabulary=["Am", "F", "C"],
            ),
            "explicit": PreferenceFields(
                midi_mean_band="low",  # overrides derived
                dynamics_band="high",
            ),
        }
    )


def test_explicit_overrides_derived():
    resolved = resolve_preference_view(_sample_profile())
    assert resolved["midi_mean_band"] == "low"
    assert resolved["rhythmic_density_band"] == "moderate"
    assert resolved["dynamics_band"] == "high"


def test_strength_off_empty_fragment():
    fragment, applied, labels = build_soft_fragment(
        resolve_preference_view(_sample_profile()), strength="off"
    )
    assert fragment == ""
    assert applied == []
    assert labels == {}


def test_strength_scales_fragment_size():
    resolved = resolve_preference_view(_sample_profile())
    light, light_keys, _ = build_soft_fragment(resolved, strength="light")
    normal, normal_keys, _ = build_soft_fragment(resolved, strength="normal")
    strong, strong_keys, _ = build_soft_fragment(resolved, strength="strong")
    assert len(light) < len(normal) or len(light_keys) <= len(normal_keys)
    assert len(normal_keys) <= len(strong_keys)
    assert "midi_mean_band=low" in light
    assert "SOFT composer-profile" in light


def test_preview_and_promote():
    profile = _sample_profile()
    preview = preview_profile_fragment(profile, strength="normal")
    assert preview.profile_id == "prof_resolve"
    assert preview.applied_field_count > 0
    assert preview.fragment_chars == len(preview.soft_fragment)

    promoted = promote_derived_to_explicit(profile)
    assert promoted.midi_mean_band == "high"  # from derived; overwrites prior explicit on promote of that key
    # promote copies derived → explicit; explicit midi was low, promote of all derived sets high
    assert promoted.rhythmic_density_band == "moderate"
