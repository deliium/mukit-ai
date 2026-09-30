"""Apply one game phase from ``adaptive.client.phases.v1``."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from mukit_adaptive.errors import AdaptiveClientError
from mukit_adaptive.log import log_event
from mukit_adaptive.models import PHASES_SCHEMA

_PHASE_NAMES = ("exploration", "danger", "combat", "victory")


def load_phase_recipe(path: str | Path) -> dict[str, Any]:
    """Load a phase recipe. The log records the file name, not the document."""
    recipe_path = Path(path)
    log_event(logging.DEBUG, "phase_recipe_load_enter", filename=recipe_path.name)
    try:
        loaded = json.loads(recipe_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log_event(logging.ERROR, "phase_recipe_load_failed", error_class=type(exc).__name__)
        raise AdaptiveClientError("engine_payload_invalid", "invalid phase recipe") from exc
    recipe = _validate_recipe(loaded)
    log_event(logging.DEBUG, "phase_recipe_load_exit", filename=recipe_path.name)
    return recipe


def apply_phase(client: Any, recipe: dict[str, Any], phase_name: str) -> list[Any]:
    """Run the calls for one phase. Skip context when the snapshot is not attached."""
    log_event(logging.DEBUG, "apply_phase_enter", phase=phase_name)
    checked = _validate_recipe(recipe)
    if phase_name not in _PHASE_NAMES:
        raise AdaptiveClientError("engine_payload_invalid", "invalid phase")
    spec = checked["phases"][phase_name]
    results: list[Any] = []
    if spec.get("cue"):
        results.append(client.fire_cue(spec["cue"]))
    else:
        results.append(client.set_state(spec["state_id"]))
        stinger_id = spec.get("stinger_id")
        if stinger_id:
            results.append(client.fire_stinger(stinger_id))
    results.append(client.set_intensity(spec["intensity"]))
    snapshot = client.snapshot
    session_id = "none" if snapshot is None else snapshot.session_id
    if snapshot is not None and snapshot.context_attached:
        results.append(client.send_context({"threat": spec["threat"]}))
    else:
        log_event(logging.INFO, "context_skipped", session_id=session_id, phase=phase_name)
    latest = client.snapshot
    runtime_state_id = "none" if latest is None else latest.runtime_state_id
    log_event(
        logging.INFO,
        "phase_applied",
        phase=phase_name,
        session_id=session_id if latest is None else latest.session_id,
        runtime_state_id=runtime_state_id,
    )
    log_event(logging.DEBUG, "apply_phase_exit", phase=phase_name, calls=len(results))
    return results


def _validate_recipe(loaded: object) -> dict[str, Any]:
    if not isinstance(loaded, dict):
        raise AdaptiveClientError("engine_payload_invalid", "invalid phase recipe")
    if loaded.get("schema_version") != PHASES_SCHEMA:
        raise AdaptiveClientError("engine_payload_invalid", "invalid phase recipe")
    phases = loaded.get("phases")
    if not isinstance(phases, dict):
        raise AdaptiveClientError("engine_payload_invalid", "invalid phase recipe")
    for name in _PHASE_NAMES:
        spec = phases.get(name)
        if not isinstance(spec, dict):
            raise AdaptiveClientError("engine_payload_invalid", "invalid phase")
        if "intensity" not in spec or "threat" not in spec:
            raise AdaptiveClientError("engine_payload_invalid", "invalid phase")
        if not spec.get("cue") and not spec.get("state_id"):
            raise AdaptiveClientError("engine_payload_invalid", "invalid phase")
    return loaded
