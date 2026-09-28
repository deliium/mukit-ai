"""Pure graph edits for ``adaptive.score.v1``.

No SQLite, no FastAPI, and no composition load. Each command returns a new
score or raises ``AdaptiveScoreError`` without writing a project.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

from pydantic import ValidationError

from app.adaptive_score_schemas import (
    AdaptiveScoreCommand,
    AdaptiveScoreError,
    AdaptiveScoreTransitionV1,
    AdaptiveScoreV1,
    parse_adaptive_score,
)
from app.adaptive_score_settings import load_adaptive_score_settings

logger = logging.getLogger(__name__)


def apply_adaptive_score_command(
    score: AdaptiveScoreV1,
    command: AdaptiveScoreCommand,
) -> AdaptiveScoreV1:
    """Apply one closed graph operation and return the next score."""
    op = command.op
    logger.debug(
        "Adaptive score command apply start",
        extra={"op": op, "state_count": len(score.states), "transition_count": len(score.transitions)},
    )
    try:
        updated = _HANDLERS[op](score, command.payload)
    except AdaptiveScoreError as exc:
        logger.error(
            "Adaptive score command rejected",
            extra={"code": exc.code, "op": op},
        )
        raise
    logger.info(
        "Adaptive score command applied",
        extra={
            "op": op,
            "state_count": len(updated.states),
            "transition_count": len(updated.transitions),
        },
    )
    return updated


def _handlers() -> dict[str, Any]:
    return {
        "create_state": _create_state,
        "delete_state": _delete_state,
        "duplicate_state": _duplicate_state,
        "assign_material": _assign_material,
        "create_transition": _create_transition,
        "edit_transition": _edit_transition,
        "assign_loop": _assign_loop,
        "assign_intensity": _assign_intensity,
        "assign_boundary": _assign_boundary,
    }


def _taken_ids(raw: dict[str, Any]) -> set[str]:
    taken: set[str] = set()
    for key in ("states", "variants", "transitions", "layers", "stingers"):
        for item in raw.get(key) or []:
            entity_id = item.get("id")
            if isinstance(entity_id, str):
                taken.add(entity_id)
    return taken


def _fresh_id(prefix: str, taken: set[str]) -> str:
    for _ in range(8):
        candidate = f"{prefix}{secrets.token_hex(4)}"
        if candidate not in taken:
            return candidate
    raise AdaptiveScoreError(
        "adaptive_score_invalid",
        "Could not allocate a unique entity id",
        http_status=422,
    )


def _commit(raw: dict[str, Any]) -> AdaptiveScoreV1:
    return parse_adaptive_score(raw)


def _missing_state(state_id: str) -> AdaptiveScoreError:
    return AdaptiveScoreError(
        "dangling_state_ref",
        f"State {state_id} does not exist.",
        http_status=422,
        details={"target_id": state_id},
    )


def _state_index(raw: dict[str, Any], state_id: str) -> int:
    for index, state in enumerate(raw.get("states") or []):
        if state.get("id") == state_id:
            return index
    raise _missing_state(state_id)


def _require_states(raw: dict[str, Any], *state_ids: str) -> None:
    present = {state.get("id") for state in raw.get("states") or []}
    for state_id in state_ids:
        if state_id not in present:
            raise _missing_state(state_id)


def _transition_invalid(transition_id: str) -> AdaptiveScoreError:
    return AdaptiveScoreError(
        "adaptive_score_invalid",
        f"Transition {transition_id} does not exist.",
        http_status=422,
        details={"target_id": transition_id},
    )


def _too_large(field: str) -> AdaptiveScoreError:
    return AdaptiveScoreError(
        "adaptive_score_too_large",
        f"Adaptive score exceeded the {field} cap.",
        http_status=422,
        details={"field": field},
    )


def _reject_model(exc: ValidationError) -> AdaptiveScoreError:
    return AdaptiveScoreError(
        "adaptive_score_invalid",
        "Adaptive score command failed schema validation.",
        http_status=422,
        details={"field": "payload"},
    )


def _copy_name(source_name: str) -> str:
    settings = load_adaptive_score_settings()
    combined = f"{source_name} copy"
    if len(combined) <= settings.max_name_length:
        return combined
    trimmed = combined[: settings.max_name_length].strip()
    return trimmed or source_name[: settings.max_name_length]


def _create_state(score: AdaptiveScoreV1, payload: Any) -> AdaptiveScoreV1:
    raw = score.model_dump(mode="json")
    settings = load_adaptive_score_settings()
    if len(raw["states"]) >= settings.max_states:
        raise _too_large("states")
    taken = _taken_ids(raw)
    if payload.id and payload.id in taken:
        raise AdaptiveScoreError(
            "duplicate_id",
            f"Id {payload.id} is already used.",
            http_status=422,
            details={"target_id": payload.id},
        )
    new_id = payload.id or _fresh_id("state_", taken)
    material = (
        payload.material.model_dump(mode="json")
        if payload.material is not None
        else {"kind": "bar_range", "start_bar": 1, "end_bar": 1}
    )
    raw["states"].append(
        {
            "id": new_id,
            "name": payload.name,
            "intensity": 0,
            "material": material,
            "loop": {"enabled": False, "start_bar": None, "end_bar": None},
            "entry": {"kind": "material_start"},
            "exit": {"kind": "material_end"},
            "min_duration_bars": 0,
            "max_duration_bars": None,
            "transition_ids": [],
        }
    )
    if not raw.get("initial_state_id"):
        raw["initial_state_id"] = new_id
    if not raw.get("default_state_id"):
        raw["default_state_id"] = new_id
    updated = _commit(raw)
    logger.debug("Adaptive score entity created", extra={"op": "create_state", "entity_id": new_id})
    return updated


def _delete_state(score: AdaptiveScoreV1, payload: Any) -> AdaptiveScoreV1:
    raw = score.model_dump(mode="json")
    _state_index(raw, payload.state_id)
    removed_transition_ids = {
        item["id"]
        for item in raw.get("transitions") or []
        if item.get("from_state_id") == payload.state_id or item.get("to_state_id") == payload.state_id
    }
    raw["states"] = [item for item in raw["states"] if item.get("id") != payload.state_id]
    for state in raw["states"]:
        state["transition_ids"] = [
            transition_id
            for transition_id in state.get("transition_ids") or []
            if transition_id not in removed_transition_ids
        ]
    raw["transitions"] = [
        item for item in raw.get("transitions") or [] if item.get("id") not in removed_transition_ids
    ]
    raw["variants"] = [
        item for item in raw.get("variants") or [] if item.get("state_id") != payload.state_id
    ]
    for layer in raw.get("layers") or []:
        if layer.get("state_id") == payload.state_id:
            layer["state_id"] = None
    for stinger in raw.get("stingers") or []:
        if stinger.get("associated_state_id") == payload.state_id:
            stinger["associated_state_id"] = None
    if raw.get("initial_state_id") == payload.state_id:
        raw["initial_state_id"] = None
    if raw.get("default_state_id") == payload.state_id:
        raw["default_state_id"] = None
    updated = _commit(raw)
    logger.debug(
        "Adaptive score entity deleted",
        extra={"op": "delete_state", "entity_id": payload.state_id},
    )
    return updated


def _duplicate_state(score: AdaptiveScoreV1, payload: Any) -> AdaptiveScoreV1:
    raw = score.model_dump(mode="json")
    settings = load_adaptive_score_settings()
    if len(raw["states"]) >= settings.max_states:
        raise _too_large("states")
    source = raw["states"][_state_index(raw, payload.state_id)]
    new_id = _fresh_id("state_", _taken_ids(raw))
    copied = {
        "id": new_id,
        "name": payload.name or _copy_name(str(source["name"])),
        "intensity": source["intensity"],
        "material": source["material"],
        "loop": source["loop"],
        "entry": source["entry"],
        "exit": source["exit"],
        "min_duration_bars": source.get("min_duration_bars", 0),
        "max_duration_bars": source.get("max_duration_bars"),
        "transition_ids": [],
    }
    raw["states"].append(copied)
    updated = _commit(raw)
    logger.debug(
        "Adaptive score entity created",
        extra={"op": "duplicate_state", "entity_id": new_id},
    )
    return updated


def _assign_material(score: AdaptiveScoreV1, payload: Any) -> AdaptiveScoreV1:
    raw = score.model_dump(mode="json")
    index = _state_index(raw, payload.state_id)
    raw["states"][index]["material"] = payload.material.model_dump(mode="json")
    logger.debug(
        "Adaptive score material assigned",
        extra={"op": "assign_material", "entity_id": payload.state_id},
    )
    return _commit(raw)


def _transition_body(payload: Any, transition_id: str) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": transition_id,
        "from_state_id": payload.from_state_id,
        "to_state_id": payload.to_state_id,
        "quantization": payload.quantization,
        "priority": payload.priority,
        "conditions": [item.model_dump(mode="json") for item in payload.conditions],
        "fallback_behavior": payload.fallback_behavior,
    }
    if payload.custom_grid_bars is not None:
        body["custom_grid_bars"] = payload.custom_grid_bars
    if payload.cue_label is not None:
        body["cue_label"] = payload.cue_label
    if payload.realization is not None:
        body["realization"] = payload.realization.model_dump(mode="json")
    if payload.fallback_transition_id:
        body["fallback_transition_id"] = payload.fallback_transition_id
    try:
        return AdaptiveScoreTransitionV1.model_validate(body).model_dump(mode="json")
    except ValidationError as exc:
        raise _reject_model(exc) from exc


def _create_transition(score: AdaptiveScoreV1, payload: Any) -> AdaptiveScoreV1:
    raw = score.model_dump(mode="json")
    _require_states(raw, payload.from_state_id, payload.to_state_id)
    settings = load_adaptive_score_settings()
    if len(raw.get("transitions") or []) >= settings.max_transitions:
        raise _too_large("transitions")
    taken = _taken_ids(raw)
    if payload.id and payload.id in taken:
        raise AdaptiveScoreError(
            "duplicate_id",
            f"Id {payload.id} is already used.",
            http_status=422,
            details={"target_id": payload.id},
        )
    new_id = payload.id or _fresh_id("tran_", taken)
    raw.setdefault("transitions", []).append(_transition_body(payload, new_id))
    for state in raw["states"]:
        if state["id"] == payload.from_state_id and new_id not in state["transition_ids"]:
            state["transition_ids"].append(new_id)
    updated = _commit(raw)
    logger.debug(
        "Adaptive score entity created",
        extra={"op": "create_transition", "entity_id": new_id},
    )
    return updated


def _edit_transition(score: AdaptiveScoreV1, payload: Any) -> AdaptiveScoreV1:
    raw = score.model_dump(mode="json")
    matches = [item for item in raw.get("transitions") or [] if item.get("id") == payload.transition_id]
    if not matches:
        raise _transition_invalid(payload.transition_id)
    current = dict(matches[0])
    old_from = current["from_state_id"]
    fields = payload.model_fields_set
    for key in (
        "from_state_id",
        "to_state_id",
        "quantization",
        "priority",
        "fallback_behavior",
        "fallback_transition_id",
        "custom_grid_bars",
        "cue_label",
        "realization",
    ):
        if key in fields:
            current[key] = getattr(payload, key)
    if "conditions" in fields:
        current["conditions"] = [item.model_dump(mode="json") for item in (payload.conditions or [])]
    if not current.get("from_state_id") or not current.get("to_state_id"):
        raise _missing_state(payload.transition_id)
    _require_states(raw, current["from_state_id"], current["to_state_id"])
    try:
        validated = AdaptiveScoreTransitionV1.model_validate(current).model_dump(mode="json")
    except ValidationError as exc:
        raise _reject_model(exc) from exc
    raw["transitions"] = [
        validated if item.get("id") == payload.transition_id else item
        for item in raw["transitions"]
    ]
    new_from = validated["from_state_id"]
    if new_from != old_from:
        for state in raw["states"]:
            if state["id"] == old_from:
                state["transition_ids"] = [
                    transition_id
                    for transition_id in state.get("transition_ids") or []
                    if transition_id != payload.transition_id
                ]
            if state["id"] == new_from and payload.transition_id not in state.get("transition_ids", []):
                state.setdefault("transition_ids", []).append(payload.transition_id)
    logger.debug(
        "Adaptive score transition edited",
        extra={"op": "edit_transition", "entity_id": payload.transition_id},
    )
    return _commit(raw)


def _assign_loop(score: AdaptiveScoreV1, payload: Any) -> AdaptiveScoreV1:
    raw = score.model_dump(mode="json")
    index = _state_index(raw, payload.state_id)
    raw["states"][index]["loop"] = {
        "enabled": payload.enabled,
        "start_bar": payload.start_bar,
        "end_bar": payload.end_bar,
    }
    logger.debug("Adaptive score loop assigned", extra={"op": "assign_loop", "entity_id": payload.state_id})
    return _commit(raw)


def _assign_intensity(score: AdaptiveScoreV1, payload: Any) -> AdaptiveScoreV1:
    raw = score.model_dump(mode="json")
    index = _state_index(raw, payload.state_id)
    raw["states"][index]["intensity"] = payload.intensity
    logger.debug(
        "Adaptive score intensity assigned",
        extra={"op": "assign_intensity", "entity_id": payload.state_id},
    )
    return _commit(raw)


def _assign_boundary(score: AdaptiveScoreV1, payload: Any) -> AdaptiveScoreV1:
    raw = score.model_dump(mode="json")
    index = _state_index(raw, payload.state_id)
    raw["states"][index][payload.which] = payload.boundary.model_dump(mode="json")
    logger.debug(
        "Adaptive score boundary assigned",
        extra={"op": "assign_boundary", "entity_id": payload.state_id},
    )
    return _commit(raw)


_HANDLERS = _handlers()
