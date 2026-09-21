"""Secret-safe ``generation.provenance.v1`` fragments for durable revision summary_json.

Never stores prompts, API keys, absolute home paths, or raw MIDI/audio bytes.
Checkpoint identifiers are basenames / short prefixes only.
"""

from __future__ import annotations

import logging
import os
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Mapping

from app.services.persistence_secret_guard import (
    PersistenceSecretError,
    assert_no_secret_fields,
    assert_payload_has_no_secret_values,
)

logger = logging.getLogger(__name__)

PROVENANCE_SCHEMA = "generation.provenance.v1"
_MAX_STAGES = 16
_MAX_CHECKPOINT_PREFIX = 32
_MAX_TOKENIZER_VERSION = 80
_MAX_MODEL_ID = 160
_MAX_MODEL_VERSION = 120
_MAX_RUNTIME = 64
_MAX_CAPABILITY = 64
_MAX_OPERATION = 64
_MAX_DIGEST_PREFIX = 40

_GENERATION_CONFIG_KEYS = frozenset(
    {
        "temperature",
        "timeout_seconds",
        "candidate_count",
        "sample_greedy",
        "max_new_tokens",
        "max_tokens",
    }
)


def checkpoint_basename_prefix(value: str | None, *, max_len: int = _MAX_CHECKPOINT_PREFIX) -> str | None:
    """Return a basename-only truncated prefix; reject path-like absolute homes at INFO."""
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    # Prefer OS-appropriate basename without retaining parent dirs.
    name = PureWindowsPath(raw).name if ("\\" in raw or (len(raw) > 1 and raw[1] == ":")) else PurePosixPath(raw).name
    if not name or name in {".", ".."}:
        name = raw.replace("\\", "/").rsplit("/", 1)[-1]
    prefix = name[:max_len]
    if os.path.isabs(raw) or ".." in PurePosixPath(raw.replace("\\", "/")).parts:
        logger.debug(
            "Checkpoint path reduced to basename prefix",
            extra={"basename_prefix": prefix, "had_absolute": os.path.isabs(raw)},
        )
    return prefix or None


def infer_model_version(*, model_id: str | None, runtime: str | None = None) -> str | None:
    """Best-effort version label when the registry does not supply one."""
    mid = (model_id or "").strip().lower()
    runtime_l = (runtime or "").strip().lower()
    if runtime_l in {"fake", "fake_symbolic"} or mid.startswith("fake:"):
        if "symbolic" in mid or runtime_l == "fake_symbolic":
            return "fake-symbolic-tiny"
        return "fake-v1"
    return None


def _truncate(value: str | None, max_len: int) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    if not cleaned:
        return None
    return cleaned[:max_len]


def _sanitize_stage(stage: Mapping[str, Any]) -> dict[str, Any]:
    model_id = _truncate(stage.get("model_id"), _MAX_MODEL_ID)
    runtime = _truncate(stage.get("runtime"), _MAX_RUNTIME)
    model_version = _truncate(stage.get("model_version"), _MAX_MODEL_VERSION)
    if model_version is None:
        model_version = infer_model_version(model_id=model_id, runtime=runtime)
    out: dict[str, Any] = {
        "operation": _truncate(stage.get("operation"), _MAX_OPERATION) or "generate",
        "model_id": model_id,
        "model_version": model_version,
        "runtime": runtime,
        "capability": _truncate(stage.get("capability"), _MAX_CAPABILITY),
    }
    if stage.get("seed") is not None:
        try:
            out["seed"] = int(stage["seed"])
        except (TypeError, ValueError):
            pass
    checkpoint = checkpoint_basename_prefix(stage.get("checkpoint_card_prefix"))
    if checkpoint:
        out["checkpoint_card_prefix"] = checkpoint
    tokenizer = _truncate(stage.get("tokenizer_version"), _MAX_TOKENIZER_VERSION)
    if tokenizer:
        out["tokenizer_version"] = tokenizer
    return out


def compact_generation_config(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    """Keep only bounded scalar generation knobs (never prompts/paths/keys)."""
    if not raw:
        return {}
    out: dict[str, Any] = {}
    for key in _GENERATION_CONFIG_KEYS:
        if key not in raw:
            continue
        value = raw[key]
        if value is None:
            continue
        if isinstance(value, bool):
            out[key] = value
        elif isinstance(value, int):
            out[key] = int(value)
        elif isinstance(value, float):
            out[key] = float(value)
        # Skip strings / nested objects — not part of the compact digest.
    return out


def generation_config_from_request(request: Any) -> dict[str, Any]:
    """Pull compact config scalars from an LLM generation request when present."""
    options = getattr(request, "options", None)
    if options is None:
        return {}
    raw: dict[str, Any] = {}
    for key in _GENERATION_CONFIG_KEYS:
        if hasattr(options, key):
            raw[key] = getattr(options, key)
    # Common alias: request.options may expose symbolic sample flags via nested attrs.
    symbolic = getattr(options, "symbolic", None)
    if symbolic is not None:
        for key in ("sample_greedy", "max_new_tokens"):
            if hasattr(symbolic, key):
                raw[key] = getattr(symbolic, key)
    return compact_generation_config(raw)


def build_generation_provenance_v1(
    *,
    pipeline_id: str,
    seed: int | None = None,
    stages: list[Mapping[str, Any]] | None = None,
    generation_config: Mapping[str, Any] | None = None,
    plan_schema_version: str | None = None,
    constraints_digest_prefix: str | None = None,
) -> dict[str, Any]:
    """Build a secret-safe ``generation.provenance.v1`` fragment.

    Raises ``PersistenceSecretError`` if forbidden fields/values appear.
    """
    sanitized_stages = [_sanitize_stage(stage) for stage in (stages or [])[:_MAX_STAGES]]
    config = compact_generation_config(generation_config)
    fragment: dict[str, Any] = {
        "provenance_schema": PROVENANCE_SCHEMA,
        "pipeline_id": _truncate(pipeline_id, 80) or "llm_only",
        "seed": int(seed) if seed is not None else None,
        "stages": sanitized_stages,
        "generation_config": config,
    }
    plan_sv = _truncate(plan_schema_version, 64)
    if plan_sv:
        fragment["plan_schema_version"] = plan_sv
    digest = _truncate(constraints_digest_prefix, _MAX_DIGEST_PREFIX)
    if digest:
        fragment["constraints_digest_prefix"] = digest

    assert_no_secret_fields(fragment, context="generation_provenance_v1")
    assert_payload_has_no_secret_values(fragment, context="generation_provenance_v1")

    logger.debug(
        "Built generation.provenance.v1 fragment",
        extra={
            "provenance_schema": PROVENANCE_SCHEMA,
            "pipeline_id": fragment["pipeline_id"],
            "seed": fragment["seed"],
            "stage_count": len(sanitized_stages),
            "stage_model_ids": [s.get("model_id") for s in sanitized_stages],
            "generation_config_keys": sorted(config.keys()),
            "has_plan_schema": "plan_schema_version" in fragment,
        },
    )
    logger.info(
        "Generation provenance fragment ready",
        extra={
            "pipeline_id": fragment["pipeline_id"],
            "seed": fragment["seed"],
            "stage_model_ids": [s.get("model_id") for s in sanitized_stages],
            "stage_count": len(sanitized_stages),
        },
    )
    return fragment


def attach_provenance_fragment(
    provenance: dict[str, Any],
    *,
    generation_config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Enrich a generator provenance dict with ``generation_parameters`` v1 fragment.

    Preserves top-level ``pipeline_id`` / ``stages`` / ``seed`` for API response fields.
    """
    fragment = build_generation_provenance_v1(
        pipeline_id=str(provenance.get("pipeline_id") or "llm_only"),
        seed=provenance.get("seed"),
        stages=list(provenance.get("stages") or []),
        generation_config=generation_config,
        plan_schema_version=provenance.get("plan_schema_version"),
        constraints_digest_prefix=provenance.get("constraints_digest_prefix"),
    )
    # Mirror sanitized stages (with model_version) back to the API surface.
    enriched = {
        **provenance,
        "stages": fragment["stages"],
        "generation_parameters": fragment,
    }
    return enriched


__all__ = [
    "PROVENANCE_SCHEMA",
    "PersistenceSecretError",
    "attach_provenance_fragment",
    "build_generation_provenance_v1",
    "checkpoint_basename_prefix",
    "compact_generation_config",
    "generation_config_from_request",
    "infer_model_version",
]
