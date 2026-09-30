"""Terminal loop for exploration, danger, combat, and victory.

Quit with ``q`` closes sockets and leaves the server session. ``--stop``
deletes that session on quit.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Callable, TextIO

from mukit_adaptive.client import MukitAdaptiveClient
from mukit_adaptive.errors import AdaptiveClientError
from mukit_adaptive.log import log_event
from mukit_adaptive.phases import apply_phase, load_phase_recipe

_REQUIRED_ENV = (
    "MUKIT_ADAPTIVE_PROJECT_ID",
    "MUKIT_ADAPTIVE_SCORE_ID",
    "MUKIT_ADAPTIVE_DOCUMENT_REVISION",
)
_PHASE_KEYS = {"1": "exploration", "2": "danger", "3": "combat", "4": "victory"}
_SNAPSHOT_FIELDS = ("runtime_state_id", "bar", "beat", "intensity", "transport", "phase")
_RECIPE_RELATIVE = (
    Path("clients") / "fixtures" / "game-phases.v1.json",
    Path("fixtures") / "game-phases.v1.json",
)


def main(argv: list[str] | None = None) -> int:
    """Run the demo from the process environment and stdin."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    return run_demo(argv=arguments, environ=os_environ(), lines=_stdin_lines())


def run_demo(
    *,
    argv: list[str],
    environ: Mapping[str, str],
    lines: Iterable[str],
    client: MukitAdaptiveClient | None = None,
    listen: bool = True,
    write: Callable[[str], None] | None = None,
) -> int:
    """Start one session from the phase recipe and apply stdin keys."""
    log_event(logging.DEBUG, "demo_enter", stop_on_quit=_stop_requested(argv))
    emit = write or (lambda text: print(text, flush=True))
    config = _read_config(environ)
    if config is None:
        log_event(logging.DEBUG, "demo_exit", status=1)
        return 1
    try:
        recipe = load_phase_recipe(_recipe_path(environ))
        mapping, cues = _start_parts(recipe)
    except AdaptiveClientError as exc:
        log_event(logging.ERROR, "demo_recipe_failed", code=exc.code)
        log_event(logging.DEBUG, "demo_exit", status=1)
        return 1
    owned = client is None
    active = client if client is not None else MukitAdaptiveClient(config["base_url"], token=config["token"])
    try:
        active.start(
            config["project_id"],
            config["score_id"],
            config["revision"],
            mapping=mapping,
            cues=cues,
        )
        if listen:
            active.listen(None, None, None)
        for raw in lines:
            if _handle_line(active, recipe, raw, stop_on_quit=_stop_requested(argv), write=emit):
                log_event(logging.DEBUG, "demo_exit", status=0)
                return 0
        _quit(active, stop_on_quit=_stop_requested(argv))
    except AdaptiveClientError as exc:
        log_event(logging.ERROR, "demo_failed", code=exc.code)
        if owned:
            active.close()
        log_event(logging.DEBUG, "demo_exit", status=1)
        return 1
    log_event(logging.DEBUG, "demo_exit", status=0)
    return 0


def format_snapshot(snapshot: Any) -> str:
    """Player-facing fields. This string is printed, not logged."""
    if snapshot is None:
        return " ".join(f"{name}=none" for name in _SNAPSHOT_FIELDS)
    return " ".join(f"{name}={getattr(snapshot, name)}" for name in _SNAPSHOT_FIELDS)


def os_environ() -> Mapping[str, str]:
    import os

    return os.environ


def _stdin_lines() -> Iterable[str]:
    stream: TextIO = sys.stdin
    for line in stream:
        yield line


def _stop_requested(argv: list[str]) -> bool:
    return "--stop" in argv


def _read_config(environ: Mapping[str, str]) -> dict[str, Any] | None:
    missing = False
    values: dict[str, str] = {}
    for name in _REQUIRED_ENV:
        raw = environ.get(name, "").strip()
        if not raw:
            log_event(logging.ERROR, "demo_env_missing", variable=name)
            missing = True
            continue
        values[name] = raw
    if missing:
        return None
    revision_text = values["MUKIT_ADAPTIVE_DOCUMENT_REVISION"]
    try:
        revision = int(revision_text)
    except ValueError:
        log_event(logging.ERROR, "demo_env_missing", variable="MUKIT_ADAPTIVE_DOCUMENT_REVISION")
        return None
    if revision < 1:
        log_event(logging.ERROR, "demo_env_missing", variable="MUKIT_ADAPTIVE_DOCUMENT_REVISION")
        return None
    token = environ.get("MUKIT_ADAPTIVE_TOKEN", "").strip() or None
    return {
        "base_url": environ.get("MUKIT_ADAPTIVE_BASE_URL", "").strip() or "http://127.0.0.1:8000",
        "project_id": values["MUKIT_ADAPTIVE_PROJECT_ID"],
        "score_id": values["MUKIT_ADAPTIVE_SCORE_ID"],
        "revision": revision,
        "token": token,
    }


def _recipe_path(environ: Mapping[str, str]) -> Path:
    explicit = environ.get("MUKIT_ADAPTIVE_PHASES", "").strip()
    if explicit:
        return Path(explicit)
    start = Path(__file__).resolve().parent
    for parent in (start, *start.parents):
        for relative in _RECIPE_RELATIVE:
            candidate = parent / relative
            if candidate.is_file():
                return candidate
    raise AdaptiveClientError("engine_payload_invalid", "invalid phase recipe")


def _start_parts(recipe: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    start = recipe.get("start")
    if not isinstance(start, dict):
        raise AdaptiveClientError("engine_payload_invalid", "invalid phase recipe")
    mapping = start.get("mapping")
    cues = start.get("cues")
    if not isinstance(mapping, dict) or not isinstance(cues, list):
        raise AdaptiveClientError("engine_payload_invalid", "invalid phase recipe")
    return mapping, cues


def _handle_line(
    client: MukitAdaptiveClient,
    recipe: dict[str, Any],
    raw: str,
    *,
    stop_on_quit: bool,
    write: Callable[[str], None],
) -> bool:
    key = raw.strip()
    phase_name = _PHASE_KEYS.get(key)
    if phase_name is not None:
        try:
            apply_phase(client, recipe, phase_name)
        except AdaptiveClientError as exc:
            snapshot = client.snapshot
            log_event(
                logging.ERROR,
                "demo_command_failed",
                code=exc.code,
                phase=phase_name,
                session_id="none" if snapshot is None else snapshot.session_id,
            )
        return False
    if key == "s":
        write(format_snapshot(client.snapshot))
        return False
    if key == "r":
        client.reconnect_now()
        return False
    if key == "q":
        _quit(client, stop_on_quit=stop_on_quit)
        return True
    log_event(logging.DEBUG, "demo_key_ignored")
    return False


def _quit(client: MukitAdaptiveClient, *, stop_on_quit: bool) -> None:
    if stop_on_quit:
        client.stop()
        return
    client.close()


if __name__ == "__main__":
    raise SystemExit(main())
