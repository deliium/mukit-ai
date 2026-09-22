"""Resolve explicit-over-derived prefs into a strength-scaled soft fragment."""

from __future__ import annotations

import logging
from typing import Any

from app.composer_profile_schemas import (
    COMPOSER_PROFILE_LIST_DEFAULT_MAX,
    PROMOTABLE_PREFERENCE_KEYS,
    ComposerProfilePreviewResponse,
    ComposerProfileV1,
    PreferenceFields,
    ProfileStrength,
    preference_fields_to_dict,
)
from app.composer_profile_settings import load_composer_profile_settings

logger = logging.getLogger(__name__)

# Keys included at each strength (light ⊂ normal ⊂ strong).
_LIGHT_KEYS: frozenset[str] = frozenset(
    {
        "midi_mean_band",
        "rhythmic_density_band",
        "preferred_instruments",
        "tension_shape",
    }
)
_NORMAL_KEYS: frozenset[str] = _LIGHT_KEYS | frozenset(
    {
        "midi_min_band",
        "midi_max_band",
        "range_semitones_band",
        "chord_change_rate_band",
        "chord_vocabulary",
        "syncopation_band",
        "arrangement_density_band",
        "repetition_amount_band",
        "preferred_forms",
        "dynamics_band",
    }
)
_STRONG_KEYS: frozenset[str] = PROMOTABLE_PREFERENCE_KEYS

_STRENGTH_KEYS: dict[str, frozenset[str]] = {
    "off": frozenset(),
    "light": _LIGHT_KEYS,
    "normal": _NORMAL_KEYS,
    "strong": _STRONG_KEYS,
}

_STRENGTH_PREFACE: dict[str, str] = {
    "light": "SOFT composer-profile preferences (low weight; may guide style only):",
    "normal": "SOFT composer-profile preferences (may guide style only; never override hard constraints):",
    "strong": (
        "SOFT composer-profile preferences (stronger soft guidance; "
        "still must NOT override hard key/meter/tempo/instruments/sections):"
    ),
}


def resolve_preference_view(profile: ComposerProfileV1) -> dict[str, Any]:
    """Merge explicit over derived for the same preference key."""
    derived = preference_fields_to_dict(profile.derived)
    # Strip stats_meta from derived view for preference keys.
    derived.pop("stats_meta", None)
    explicit = preference_fields_to_dict(profile.explicit)
    resolved: dict[str, Any] = dict(derived)
    skipped_derived: list[str] = []
    for key, value in explicit.items():
        if key in resolved and resolved[key] != value:
            skipped_derived.append(key)
        resolved[key] = value
    if skipped_derived:
        logger.debug(
            "Explicit prefs override derived",
            extra={
                "profile_id": profile.id,
                "skipped_derived_keys": skipped_derived[:32],
            },
        )
    return resolved


def _format_value(key: str, value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        if key == "interval_histogram":
            bins = value.get("bins") or []
            if not bins:
                return None
            # Compact: peak bin index only.
            peak = max(range(len(bins)), key=lambda i: bins[i])
            return f"interval_peak_bin={peak - 12}"
        if key == "preferred_forms":
            counts = value.get("counts") or {}
            if not counts:
                return None
            top = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:4]
            return "forms=" + ",".join(f"{k}:{round(v, 2)}" for k, v in top)
        return None
    if isinstance(value, list):
        if not value:
            return None
        return f"{key}=" + ",".join(str(item) for item in value[:COMPOSER_PROFILE_LIST_DEFAULT_MAX])
    return f"{key}={value}"


def build_soft_fragment(
    resolved: dict[str, Any],
    *,
    strength: ProfileStrength,
    max_chars: int | None = None,
) -> tuple[str, list[str], dict[str, str]]:
    """Return (fragment, applied_keys, tokenizer_labels)."""
    if strength == "off":
        return "", [], {}

    settings = load_composer_profile_settings()
    cap = max_chars if max_chars is not None else settings.fragment_max_chars
    allowed = _STRENGTH_KEYS.get(strength, _NORMAL_KEYS)
    applied: list[str] = []
    lines: list[str] = [_STRENGTH_PREFACE[strength]]
    labels: dict[str, str] = {}

    for key in sorted(allowed):
        if key not in resolved:
            continue
        formatted = _format_value(key, resolved[key])
        if not formatted:
            continue
        applied.append(key)
        lines.append(f"- {formatted}")
        # Tokenizer-ish labels for a few bands.
        if key.endswith("_band") or key in {"tension_shape"}:
            labels[key] = str(resolved[key])[:80]
        elif key == "preferred_instruments" and isinstance(resolved[key], list):
            labels["instruments_hint"] = ",".join(str(x) for x in resolved[key][:4])[:80]

    fragment = "\n".join(lines)
    if len(fragment) > cap:
        fragment = fragment[: max(0, cap - 3)] + "..."
        logger.debug(
            "Soft fragment truncated",
            extra={"strength": strength, "cap": cap, "applied_field_count": len(applied)},
        )
    return fragment, applied, labels


def preview_profile_fragment(
    profile: ComposerProfileV1,
    *,
    strength: ProfileStrength = "normal",
) -> ComposerProfilePreviewResponse:
    resolved = resolve_preference_view(profile)
    fragment, applied, labels = build_soft_fragment(resolved, strength=strength)
    logger.info(
        "Composer profile preview resolved",
        extra={
            "profile_id": profile.id,
            "strength": strength,
            "applied_field_count": len(applied),
            "fragment_chars": len(fragment),
        },
    )
    return ComposerProfilePreviewResponse(
        profile_id=profile.id,
        strength=strength,
        applied_field_count=len(applied),
        fragment_chars=len(fragment),
        soft_fragment=fragment,
        tokenizer_labels=labels,
        resolved_keys=sorted(resolved.keys())[:64],
    )


def promote_derived_to_explicit(
    profile: ComposerProfileV1,
    *,
    fields: list[str] | None = None,
) -> PreferenceFields:
    """Copy selected (or all set) derived keys into an explicit PreferenceFields."""
    derived_payload = preference_fields_to_dict(profile.derived)
    derived_payload.pop("stats_meta", None)
    explicit_payload = preference_fields_to_dict(profile.explicit)

    if fields:
        wanted = [f for f in fields if f in PROMOTABLE_PREFERENCE_KEYS]
    else:
        wanted = [k for k in PROMOTABLE_PREFERENCE_KEYS if k in derived_payload]

    for key in wanted:
        if key in derived_payload:
            explicit_payload[key] = derived_payload[key]

    return PreferenceFields.model_validate(explicit_payload)
