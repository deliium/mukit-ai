"""Pack-side Composer Profile → ``creative.brief.v1`` soft fold (Part I.B).

Hard plan constraints always win. Never extends ``AutonomousRunStartV1``.
Never logs ``soft_fragment`` contents.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from app.asset_pack_schemas import (
    AssetPackError,
    AssetPackPlanSlotV1,
    AssetPackPlanV1,
)
from app.autonomous_composer_schemas import CreativeBriefV1
from app.composer_profile_schemas import ComposerProfileError
from app.services.composer_profile_merge import (
    ComposerProfileMergeResult,
    resolve_profile_merge,
)
from app.services.composer_profile_resolve import resolve_preference_view
from app.services import composer_profile_store

logger = logging.getLogger(__name__)

_DENSITY_TO_CLIMAX_INTENT = {
    "sparse": "build",
    "moderate": "build",
    "dense": "climax",
}


@dataclass(frozen=True)
class AssetPackBriefBuildResult:
    brief: CreativeBriefV1
    profile_id: str | None
    profile_strength: str
    applied_field_count: int
    merge_applied: bool


def _require_profile(profile_id: str, *, db_path: Path | None) -> None:
    try:
        composer_profile_store.get_profile(profile_id, db_path=db_path)
    except ComposerProfileError as exc:
        if exc.code == "composer_profile_not_found":
            logger.warning(
                "Asset pack profile unknown",
                extra={"code": "asset_pack_profile_unknown", "profile_id": profile_id},
            )
            raise AssetPackError("asset_pack_profile_unknown") from exc
        raise


def resolve_pack_profile_merge(
    plan: AssetPackPlanV1,
    *,
    db_path: Path | None = None,
) -> ComposerProfileMergeResult:
    """Resolve soft merge once per pack. Raises on unknown profile id."""
    profile_id = plan.composer_profile_id
    strength = plan.composer_profile_strength or "normal"
    if not profile_id or strength == "off":
        logger.debug(
            "Asset pack profile merge skipped",
            extra={"strength": strength, "has_profile_id": bool(profile_id)},
        )
        return resolve_profile_merge(
            profile_id=None,
            profile_strength="off",
            db_path=db_path,
        )

    _require_profile(profile_id, db_path=db_path)
    merge = resolve_profile_merge(
        profile_id=profile_id,
        profile_strength=strength,
        db_path=db_path,
    )
    logger.debug(
        "Asset pack profile merge resolved",
        extra={
            "profile_id": profile_id,
            "strength": strength,
            "applied_field_count": merge.applied_field_count,
            "merge_applied": bool(merge.soft_fragment),
        },
    )
    return merge


def _soft_narrative_hint(merge: ComposerProfileMergeResult) -> str | None:
    """Derive a short narrative hint without exposing the full soft_fragment."""
    if not merge.soft_fragment or merge.applied_field_count <= 0:
        return None
    # Prefer tokenizer labels / applied count — never paste soft_fragment into logs.
    labels = sorted(merge.tokenizer_labels.values())[:3]
    if labels:
        return f"Soft style hints: {', '.join(labels)}"
    return f"Soft composer-profile guidance ({merge.applied_field_count} fields)"


def _soft_instrument_hints(
    merge: ComposerProfileMergeResult,
    *,
    hard_instruments: list[str],
    db_path: Path | None,
) -> list[str]:
    """Return hard instruments unchanged; soft prefs never replace them."""
    # Identity: hard plan instrumentation always wins.
    _ = merge
    _ = db_path
    return list(hard_instruments)


def build_slot_creative_brief(
    plan: AssetPackPlanV1,
    slot: AssetPackPlanSlotV1,
    *,
    merge: ComposerProfileMergeResult | None = None,
    db_path: Path | None = None,
) -> AssetPackBriefBuildResult:
    """Build one slot ``creative.brief.v1`` with hard constraints from the plan."""
    active_merge = merge
    if active_merge is None:
        active_merge = resolve_pack_profile_merge(plan, db_path=db_path)

    hard = plan.constraints
    instruments = _soft_instrument_hints(
        active_merge,
        hard_instruments=list(hard.instrumentation),
        db_path=db_path,
    )
    climax_intent = _DENSITY_TO_CLIMAX_INTENT.get(slot.density, "build")
    narrative_text = slot.narrative_intent
    hint = _soft_narrative_hint(active_merge)
    if hint and len(narrative_text) + len(hint) + 2 <= 240:
        narrative_text = f"{narrative_text}. {hint}"

    # Autonomous Theme A binding requires an outro (resolve) section.
    narrative = [
        {"intent": "sparse_opening", "text": f"Open {slot.label}"[:240]},
        {"intent": "establish_theme", "text": narrative_text[:240]},
        {"intent": climax_intent, "text": f"Develop {slot.label}"[:240]},
        {"intent": "resolve", "text": f"Close {slot.label} with Theme A"[:240]},
    ]

    brief = CreativeBriefV1.model_validate(
        {
            "schema_version": "creative.brief.v1",
            "title": f"{plan.title} — {slot.label}"[:120],
            "duration_seconds": slot.duration_seconds,
            "narrative": narrative,
            "instrumentation": instruments,
            "forbidden_instrument_families": list(hard.forbidden_instrument_families),
            "opening_key": hard.opening_key,
            "motif_label": hard.motif_label,
            "motif_must_remain_recognizable": True,
            "time_signature": hard.time_signature,
            "tempo_min": hard.tempo_min,
            "tempo_max": hard.tempo_max,
        }
    )

    applied = active_merge.applied_field_count if active_merge.soft_fragment else 0
    logger.debug(
        "Built slot creative brief",
        extra={
            "slot_id": slot.slot_id,
            "profile_merge_applied": bool(active_merge.soft_fragment),
            "strength": active_merge.strength,
            "applied_field_count": applied,
        },
    )
    return AssetPackBriefBuildResult(
        brief=brief,
        profile_id=active_merge.profile_id,
        profile_strength=active_merge.strength,
        applied_field_count=applied,
        merge_applied=bool(active_merge.soft_fragment),
    )


def fold_profile_into_slot_briefs(
    plan: AssetPackPlanV1,
    *,
    db_path: Path | None = None,
) -> dict[str, CreativeBriefV1]:
    """Build briefs for every plan slot using one profile merge."""
    merge = resolve_pack_profile_merge(plan, db_path=db_path)
    out: dict[str, CreativeBriefV1] = {}
    for slot in plan.slots:
        built = build_slot_creative_brief(plan, slot, merge=merge, db_path=db_path)
        out[slot.slot_id] = built.brief
    return out


def preferred_instruments_from_merge(
    merge: ComposerProfileMergeResult,
    *,
    profile_id: str | None,
    db_path: Path | None = None,
) -> list[str]:
    """Read soft preferred_instruments for tests/inspection — never applied as hard."""
    if not profile_id or not merge.soft_fragment:
        return []
    try:
        profile = composer_profile_store.get_profile(profile_id, db_path=db_path)
    except ComposerProfileError:
        return []
    resolved = resolve_preference_view(profile)
    raw = resolved.get("preferred_instruments")
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if isinstance(item, str)]


__all__ = [
    "AssetPackBriefBuildResult",
    "build_slot_creative_brief",
    "fold_profile_into_slot_briefs",
    "preferred_instruments_from_merge",
    "resolve_pack_profile_merge",
]
