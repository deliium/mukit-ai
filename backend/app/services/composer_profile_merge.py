"""Additive composer-profile soft-fragment merge for generation.

Never mutates ``LLMPromptParameters`` or hard ``GenerationConstraints``.
Profile soft prefs are injected as an additive prompt fragment only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.composer_profile_schemas import (
    ComposerProfileError,
    ComposerProfileV1,
    ProfileStrength,
)
from app.composer_profile_settings import load_composer_profile_settings
from app.services.composer_profile_resolve import (
    build_soft_fragment,
    resolve_preference_view,
)
from app.services import composer_profile_store

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ComposerProfileMergeResult:
    profile_id: str | None
    strength: ProfileStrength
    soft_fragment: str
    applied_field_count: int
    tokenizer_labels: dict[str, str]
    source_project_count: int
    provenance: dict[str, Any]


def _normalize_strength(raw: str | None) -> ProfileStrength:
    value = (raw or "off").strip().lower()
    if value not in {"off", "light", "normal", "strong"}:
        logger.warning(
            "Invalid profile_strength; treating as off",
            extra={"raw_strength": value[:32]},
        )
        return "off"
    return value  # type: ignore[return-value]


def resolve_profile_merge(
    *,
    profile_id: str | None,
    profile_strength: str | None = "off",
    profile: ComposerProfileV1 | None = None,
    db_path: Path | None = None,
) -> ComposerProfileMergeResult:
    """Load/resolve profile and build additive soft fragment + provenance keys.

    Does not read or mutate any generate prompt DTO fields.
    """
    strength = _normalize_strength(profile_strength)
    empty = ComposerProfileMergeResult(
        profile_id=None,
        strength="off",
        soft_fragment="",
        applied_field_count=0,
        tokenizer_labels={},
        source_project_count=0,
        provenance={},
    )

    if strength == "off" or not (profile_id or profile):
        logger.debug(
            "Composer profile merge skipped",
            extra={"strength": strength, "has_profile_id": bool(profile_id)},
        )
        return empty

    active = profile
    if active is None:
        assert profile_id is not None
        try:
            active = composer_profile_store.get_profile(profile_id, db_path=db_path)
        except ComposerProfileError:
            logger.warning(
                "Composer profile missing at generate time; skipping merge",
                extra={"profile_id": profile_id, "strength": strength},
            )
            return empty

    settings = load_composer_profile_settings()
    resolved = resolve_preference_view(active)
    fragment, applied, labels = build_soft_fragment(
        resolved,
        strength=strength,
        max_chars=settings.fragment_max_chars,
    )
    source_count = len(active.source_projects)
    provenance = {
        "composer_profile_id": active.id,
        "profile_strength": strength,
        "source_project_count": source_count,
    }
    logger.info(
        "Composer profile merge applied",
        extra={
            "profile_id": active.id,
            "strength": strength,
            "applied_field_count": len(applied),
            "fragment_chars": len(fragment),
            "source_project_count": source_count,
        },
    )
    return ComposerProfileMergeResult(
        profile_id=active.id,
        strength=strength,
        soft_fragment=fragment,
        applied_field_count=len(applied),
        tokenizer_labels=labels,
        source_project_count=source_count,
        provenance=provenance,
    )


def append_profile_fragment(prompt: str, fragment: str) -> str:
    """Append profile soft fragment after existing prompt soft lines (additive)."""
    cleaned = (fragment or "").strip()
    if not cleaned:
        return prompt
    base = prompt.rstrip()
    return f"{base}\n\n{cleaned}"


def merge_provenance_keys(
    provenance: dict[str, Any],
    merge: ComposerProfileMergeResult,
) -> dict[str, Any]:
    """Nest profile id/strength under generation_parameters without preference bodies."""
    if not merge.provenance:
        return provenance
    out = dict(provenance)
    generation_parameters = dict(out.get("generation_parameters") or {})
    generation_parameters.update(merge.provenance)
    out["generation_parameters"] = generation_parameters
    # Also expose top-level for older readers that flatten keys.
    out["composer_profile_id"] = merge.provenance.get("composer_profile_id")
    out["profile_strength"] = merge.provenance.get("profile_strength")
    return out


def profile_active_for_fake(request: Any) -> bool:
    """Whether fake LLM should apply a deterministic soft-marker shift."""
    strength = _normalize_strength(getattr(request, "profile_strength", None))
    profile_id = getattr(request, "profile_id", None)
    return strength != "off" and bool(profile_id)
