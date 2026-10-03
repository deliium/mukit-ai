"""Deterministic brief → ``asset.pack.plan.v1`` (AssetPackPlan) compiler.

Never writes projects, notes, or SQLite. Digests exclude ``plan_digest``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from app.asset_pack_schemas import (
    ASSET_PACK_MAX_SLOTS,
    ASSET_PACK_PLAN_SCHEMA,
    GAME_SOUNDTRACK_V1_PROPAGATE,
    GAME_SOUNDTRACK_V1_SLOTS,
    NON_SEED_DURATION_SECONDS_DEFAULT,
    SEED_DURATION_SECONDS_DEFAULT,
    SLOT_DURATION_SECONDS_MAX,
    AssetPackBriefV1,
    AssetPackError,
    AssetPackPlanConstraintsV1,
    AssetPackPlanSlotV1,
    AssetPackPlanV1,
    AssetPackProductionV1,
    AssetPackPropagateOp,
    AssetPackThemePolicyV1,
)

logger = logging.getLogger(__name__)

_DEFAULT_INSTRUMENTS = ("violin", "cello", "french_horn", "harp")


def _default_production() -> AssetPackProductionV1:
    return AssetPackProductionV1.model_validate(
        {
            "schema_version": "asset.pack.production.v1",
            "master_target": "cinematic",
            "guarantee": False,
            "catalog_instrument_ids": list(_DEFAULT_INSTRUMENTS),
        }
    )


def _narrative_for(slot_id: str, label: str, density: str) -> str:
    return f"{label} soundtrack asset ({density}) for slot {slot_id}"


def _resolve_slot_rows(brief: AssetPackBriefV1) -> list[dict[str, Any]]:
    if brief.preset == "game_soundtrack_v1":
        base = [dict(row) for row in GAME_SOUNDTRACK_V1_SLOTS]
        overrides = {slot.slot_id: slot for slot in (brief.slots or [])}
        if brief.slots:
            requested = [slot.slot_id for slot in brief.slots]
            by_id = {row["slot_id"]: row for row in base}
            if all(sid in by_id for sid in requested):
                # Preset subset: keep requested order.
                base = [dict(by_id[sid]) for sid in requested]
            else:
                base = []
                for slot in brief.slots:
                    base.append(
                        {
                            "slot_id": slot.slot_id,
                            "label": slot.label or slot.slot_id.replace("_", " ").title(),
                            "adaptive_label": slot.adaptive_label or slot.slot_id,
                            "density": slot.density or "moderate",
                        }
                    )
        rows: list[dict[str, Any]] = []
        for row in base:
            override = overrides.get(row["slot_id"])
            if override is not None:
                if override.label:
                    row["label"] = override.label
                if override.adaptive_label:
                    row["adaptive_label"] = override.adaptive_label
                if override.density:
                    row["density"] = override.density
                if override.narrative_intent:
                    row["narrative_intent"] = override.narrative_intent
                if override.duration_seconds is not None:
                    row["duration_seconds"] = override.duration_seconds
            rows.append(row)
        return rows

    if not brief.slots:
        raise AssetPackError("asset_pack_invalid", "Brief requires preset or slots")
    rows = []
    for slot in brief.slots:
        rows.append(
            {
                "slot_id": slot.slot_id,
                "label": slot.label or slot.slot_id.replace("_", " ").title(),
                "adaptive_label": slot.adaptive_label or slot.slot_id,
                "density": slot.density or "moderate",
                "narrative_intent": slot.narrative_intent,
                "duration_seconds": slot.duration_seconds,
            }
        )
    return rows


def _duration_for(slot_id: str, seed_slot_id: str, explicit: int | None) -> int:
    if explicit is not None:
        return min(int(explicit), SLOT_DURATION_SECONDS_MAX)
    if slot_id == seed_slot_id:
        return SEED_DURATION_SECONDS_DEFAULT
    return NON_SEED_DURATION_SECONDS_DEFAULT


def _build_propagate(
    brief: AssetPackBriefV1,
    slot_ids: list[str],
) -> dict[str, AssetPackPropagateOp]:
    seed = brief.seed_slot_id
    propagate: dict[str, AssetPackPropagateOp] = {}
    for slot_id in slot_ids:
        if slot_id == seed:
            continue
        raw = GAME_SOUNDTRACK_V1_PROPAGATE.get(slot_id)
        if raw is None:
            raw = {"operation": "repeat"}
        propagate[slot_id] = AssetPackPropagateOp.model_validate(
            {
                **raw,
                "destination_start_bar": 1,
            }
        )
    return propagate


def compute_plan_digest(plan_without_digest: dict[str, Any]) -> str:
    """SHA-256 of canonical plan JSON excluding plan_digest."""
    payload = {key: value for key, value in plan_without_digest.items() if key != "plan_digest"}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    logger.debug(
        "Computed asset pack plan digest",
        extra={"digest_prefix": digest[:12], "payload_bytes": len(encoded)},
    )
    return digest


def compile_asset_pack_plan(brief: AssetPackBriefV1) -> AssetPackPlanV1:
    """Compile a non-playable AssetPackPlan from a brief. No SQLite writes."""
    rows = _resolve_slot_rows(brief)
    if not rows:
        raise AssetPackError("asset_pack_invalid", "Brief produced no slots")
    if len(rows) > ASSET_PACK_MAX_SLOTS:
        raise AssetPackError("asset_pack_slot_limit")

    seed_slot_id = brief.seed_slot_id
    slot_ids = [str(row["slot_id"]) for row in rows]
    if seed_slot_id not in slot_ids:
        raise AssetPackError(
            "asset_pack_invalid",
            "seed_slot_id is not among compiled slots",
        )

    production = brief.production or _default_production()
    instrumentation = list(production.catalog_instrument_ids)

    plan_slots: list[AssetPackPlanSlotV1] = []
    for row in rows:
        slot_id = str(row["slot_id"])
        label = str(row["label"])
        density = str(row["density"])
        duration = _duration_for(slot_id, seed_slot_id, row.get("duration_seconds"))
        narrative = row.get("narrative_intent") or _narrative_for(slot_id, label, density)
        plan_slots.append(
            AssetPackPlanSlotV1.model_validate(
                {
                    "slot_id": slot_id,
                    "label": label,
                    "adaptive_label": str(row["adaptive_label"]),
                    "density": density,
                    "duration_seconds": duration,
                    "narrative_intent": narrative,
                }
            )
        )

    constraints = AssetPackPlanConstraintsV1.model_validate(
        {
            "opening_key": brief.opening_key,
            "time_signature": brief.time_signature,
            "tempo_min": brief.tempo_min,
            "tempo_max": brief.tempo_max,
            "instrumentation": instrumentation,
            "forbidden_instrument_families": [],
            "motif_label": brief.motif_label,
        }
    )
    theme_policy = AssetPackThemePolicyV1.model_validate(
        {
            "seed_slot_id": seed_slot_id,
            "propagate": {
                slot_id: op.model_dump(mode="json")
                for slot_id, op in _build_propagate(brief, slot_ids).items()
            },
            "require_motif_pin": True,
            "destination_start_bar": 1,
        }
    )

    draft: dict[str, Any] = {
        "schema_version": ASSET_PACK_PLAN_SCHEMA,
        "title": brief.title,
        "preset": brief.preset,
        "slots": [slot.model_dump(mode="json") for slot in plan_slots],
        "constraints": constraints.model_dump(mode="json"),
        "production": production.model_dump(mode="json"),
        "theme_policy": theme_policy.model_dump(mode="json"),
        "composer_profile_id": brief.composer_profile_id,
        "composer_profile_strength": brief.composer_profile_strength,
        "include_rendering": brief.include_rendering,
        "include_adaptive_scaffolds": brief.include_adaptive_scaffolds,
        "seed": brief.seed,
    }
    digest = compute_plan_digest(draft)
    draft["plan_digest"] = digest
    plan = AssetPackPlanV1.model_validate(draft)

    logger.debug(
        "Compiled asset pack plan",
        extra={
            "slot_count": len(plan.slots),
            "preset_id": plan.preset or "",
            "digest_prefix": digest[:12],
            "seed_duration": SEED_DURATION_SECONDS_DEFAULT,
            "non_seed_duration": NON_SEED_DURATION_SECONDS_DEFAULT,
        },
    )
    return plan


__all__ = [
    "compile_asset_pack_plan",
    "compute_plan_digest",
]
