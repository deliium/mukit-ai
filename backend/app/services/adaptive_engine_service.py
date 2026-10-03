"""External engine session over the existing playback and musical-context services.

Process memory only. Does not write the adaptive score, the composition, or
note events, and does not call a model.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from app.adaptive_engine_schemas import (
    AdaptiveEngineAckV1,
    AdaptiveEngineCommandResultV1,
    AdaptiveEngineCueEventV1,
    AdaptiveEngineError,
    AdaptiveEngineIntensityCommandV1,
    AdaptiveEngineSessionV1,
    AdaptiveEngineStartV1,
    AdaptiveEngineStateCommandV1,
    AdaptiveEngineStingerEventV1,
    AdaptiveEngineTelemetryV1,
    EngineAckKind,
    EngineDisposition,
)
from app.adaptive_engine_settings import load_adaptive_engine_settings
from app.adaptive_musical_context_schemas import (
    AdaptiveContextExternalV1,
    AdaptiveContextStartRequest,
    parse_adaptive_context_external,
)
from app.adaptive_playback_schemas import (
    AdaptivePlaybackAdvanceCommand,
    AdaptivePlaybackRequestStateCommand,
    AdaptivePlaybackRuntimeV1,
    AdaptivePlaybackSetIntensityCommand,
    AdaptivePlaybackStartRequest,
)
from app.adaptive_score_schemas import AdaptiveScoreError, AdaptiveScoreV1
from app.services.adaptive_engine_backpressure import (
    EnginePressure,
    drain_context,
    offer_context,
    try_command,
)
from app.services.adaptive_musical_context_service import (
    get_adaptive_musical_context,
    sample_adaptive_musical_context,
    start_adaptive_musical_context,
    stop_adaptive_musical_context,
)
from app.services.adaptive_playback_runtime import get_default_playback_registry
from app.services.adaptive_playback_service import (
    command_adaptive_playback,
    get_adaptive_playback,
    start_adaptive_playback,
    stop_adaptive_playback,
)
from app.services.adaptive_score_service import get_adaptive_score

logger = logging.getLogger(__name__)

_COMMIT_EVENTS = frozenset({"state_committed", "stinger_started", "intensity_changed"})
_FINISH_EVENTS = frozenset({"state_committed", "stinger_started", "stinger_finished"})
_SESSION_ID_ATTEMPTS = 8


@dataclass
class _OpenRequest:
    request_id: str | None
    kind: EngineAckKind


@dataclass
class EngineFeed:
    """One socket subscriber. The queue holds public frames only."""

    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue[dict[str, Any]]
    kind: Literal["events", "status"]
    close_code: int | None = None


@dataclass
class _HeldEngine:
    session_id: str
    project_id: str
    score_id: str
    clock_owner: Literal["engine", "existing"]
    document_revision: int
    context_started: bool
    context_attached: bool
    cues: dict[str, tuple[str, str | None]]
    tempo: int
    ticks_per_quarter: int
    remainder: int
    pressure: EnginePressure[AdaptiveContextExternalV1]
    warnings: list[str]
    command_count: int = 0
    rejected_count: int = 0
    coalesced_count: int = 0
    dropped_context_count: int = 0
    ack_count: int = 0
    open_request: _OpenRequest | None = None
    task: asyncio.Task[None] | None = None
    feeds: list[EngineFeed] = field(default_factory=list)
    status_signature: tuple[Any, ...] | None = None
    last_status_at: float | None = None
    status_dirty: bool = False
    last_now_ms: int = 0


class AdaptiveEngineRegistry:
    """One engine session per project and score. Lost on process restart."""

    def __init__(self) -> None:
        self._sessions: dict[str, _HeldEngine] = {}
        self._by_pair: dict[tuple[str, str], str] = {}

    def count(self) -> int:
        return len(self._sessions)

    def by_id(self, session_id: str) -> _HeldEngine | None:
        return self._sessions.get(session_id)

    def by_pair(self, project_id: str, score_id: str) -> _HeldEngine | None:
        session_id = self._by_pair.get((project_id, score_id))
        if session_id is None:
            return None
        return self._sessions.get(session_id)

    def add(self, held: _HeldEngine) -> None:
        self._sessions[held.session_id] = held
        self._by_pair[(held.project_id, held.score_id)] = held.session_id

    def discard(self, session_id: str) -> _HeldEngine | None:
        held = self._sessions.pop(session_id, None)
        if held is not None:
            self._by_pair.pop((held.project_id, held.score_id), None)
        return held

    def values(self) -> list[_HeldEngine]:
        return list(self._sessions.values())

    def clear(self) -> None:
        self._sessions.clear()
        self._by_pair.clear()


_REGISTRY = AdaptiveEngineRegistry()
_LOCK = __import__("threading").RLock()


def get_default_engine_registry() -> AdaptiveEngineRegistry:
    return _REGISTRY


def reset_default_engine_registry() -> None:
    """Drop sessions and cancel tickers. Playback and context registries stay put."""
    with _LOCK:
        for held in _REGISTRY.values():
            _cancel_task(held)
        _REGISTRY.clear()
    logger.info("Adaptive engine registry reset")


def cancel_adaptive_engine_tasks() -> None:
    """Stop tickers during process shutdown. Does not write the score."""
    with _LOCK:
        session_ids = [held.session_id for held in _REGISTRY.values()]
        for held in _REGISTRY.values():
            _cancel_task(held)
    logger.info(
        "Adaptive engine tickers cancelled",
        extra={"session_count": len(session_ids)},
    )


def start_engine_session(
    body: AdaptiveEngineStartV1,
    *,
    db_path: Path | None = None,
) -> AdaptiveEngineSessionV1:
    """Start or bind playback. A second session for the same score is refused."""
    logger.debug(
        "Adaptive engine start requested",
        extra={"project_id": body.project_id, "score_id": body.score_id},
    )
    record = _load_score(body.project_id, body.score_id, db_path=db_path)
    if record.document_revision != body.expected_document_revision:
        logger.debug(
            "Adaptive engine translated domain code",
            extra={"code": "adaptive_score_conflict"},
        )
        raise AdaptiveEngineError("engine_revision_conflict")
    settings = load_adaptive_engine_settings()
    with _LOCK:
        existing = _REGISTRY.by_pair(body.project_id, body.score_id)
        if existing is not None:
            raise AdaptiveEngineError(
                "engine_session_exists",
                session_id=existing.session_id,
                details={"session_id": existing.session_id},
            )
        if _REGISTRY.count() >= settings.max_sessions:
            raise AdaptiveEngineError("engine_session_limit")
        started_playback = False
        started_context = False
        try:
            playback = _playback_or_none(body.project_id, body.score_id, db_path=db_path)
            if playback is None:
                playback = start_adaptive_playback(
                    body.project_id,
                    body.score_id,
                    AdaptivePlaybackStartRequest(
                        expected_document_revision=body.expected_document_revision,
                        mode="simulation",
                    ),
                    db_path=db_path,
                )
                started_playback = True
                clock_owner: Literal["engine", "existing"] = "engine"
            else:
                clock_owner = "existing"
            warnings: list[str] = []
            context_attached = False
            if body.mapping is not None:
                current = _context_or_none(body.project_id, body.score_id, db_path=db_path)
                if current is None:
                    start_adaptive_musical_context(
                        body.project_id,
                        body.score_id,
                        AdaptiveContextStartRequest(
                            expected_document_revision=body.expected_document_revision,
                            mapping=body.mapping,
                        ),
                        db_path=db_path,
                    )
                    started_context = True
                    context_attached = True
                else:
                    _append_code(warnings, "engine_context_busy")
            tempo, ticks_per_quarter = _latched_tempo(body.project_id, body.score_id)
            now_ms = _wall_ms()
            held = _HeldEngine(
                session_id=_mint_session_id(),
                project_id=body.project_id,
                score_id=body.score_id,
                clock_owner=clock_owner,
                document_revision=record.document_revision,
                context_started=started_context,
                context_attached=context_attached,
                cues={
                    cue.name: (cue.to_state_id, cue.transition_id) for cue in body.cues
                },
                tempo=tempo,
                ticks_per_quarter=ticks_per_quarter,
                remainder=0,
                pressure=EnginePressure.create(
                    command_hz=settings.command_hz,
                    command_burst=settings.command_burst,
                    context_hz=settings.context_hz,
                    context_burst=settings.context_burst,
                    now_ms=now_ms,
                ),
                warnings=warnings,
                last_now_ms=now_ms,
            )
            _REGISTRY.add(held)
            if clock_owner == "engine" and settings.ticker == "auto":
                _start_ticker(held)
        except AdaptiveEngineError:
            _rollback(body.project_id, body.score_id, started_playback, started_context, db_path)
            raise
        except AdaptiveScoreError as exc:
            _rollback(body.project_id, body.score_id, started_playback, started_context, db_path)
            raise _translate(exc) from exc
        except Exception:
            _rollback(body.project_id, body.score_id, started_playback, started_context, db_path)
            raise
        snapshot = _require_playback(held, db_path=db_path)
        session = _project(held, snapshot)
    logger.info(
        "Adaptive engine session bound" if clock_owner == "existing" else "Adaptive engine session started",
        extra={
            "session_id": session.session_id,
            "project_id": body.project_id,
            "score_id": body.score_id,
            "clock_owner": clock_owner,
        },
    )
    logger.debug(
        "Adaptive engine start completed",
        extra={"session_id": session.session_id, "context_attached": context_attached},
    )
    return session


def get_engine_session(
    session_id: str,
    *,
    db_path: Path | None = None,
) -> AdaptiveEngineSessionV1:
    logger.debug("Adaptive engine session read", extra={"session_id": session_id})
    with _LOCK:
        held = _require_held(session_id)
        snapshot = _require_playback(held, db_path=db_path)
        return _project(held, snapshot)


def delete_engine_session(
    session_id: str,
    *,
    db_path: Path | None = None,
) -> None:
    logger.debug("Adaptive engine delete requested", extra={"session_id": session_id})
    with _LOCK:
        held = _REGISTRY.discard(session_id)
        if held is None:
            raise AdaptiveEngineError("engine_session_missing")
        _cancel_task(held)
        held.pressure.slot = None
        _close_feeds(held, 4404)
        project_id = held.project_id
        score_id = held.score_id
        clock_owner = held.clock_owner
        context_started = held.context_started
    if context_started:
        _quiet_stop_context(project_id, score_id, db_path=db_path)
    if clock_owner == "engine":
        _quiet_stop_playback(project_id, score_id, db_path=db_path)
    logger.info(
        "Adaptive engine session deleted",
        extra={
            "session_id": session_id,
            "project_id": project_id,
            "score_id": score_id,
            "clock_owner": clock_owner,
        },
    )


def command_engine_state(
    session_id: str,
    body: AdaptiveEngineStateCommandV1,
    *,
    db_path: Path | None = None,
) -> AdaptiveEngineCommandResultV1:
    return _run_command(
        session_id,
        kind="state",
        request_id=body.request_id,
        db_path=db_path,
        command=AdaptivePlaybackRequestStateCommand(
            op="request_state",
            to_state_id=body.to_state_id,
            transition_id=body.transition_id,
        ),
    )


def command_engine_intensity(
    session_id: str,
    body: AdaptiveEngineIntensityCommandV1,
    *,
    db_path: Path | None = None,
) -> AdaptiveEngineCommandResultV1:
    return _run_command(
        session_id,
        kind="intensity",
        request_id=body.request_id,
        db_path=db_path,
        command=AdaptivePlaybackSetIntensityCommand(op="set_intensity", intensity=body.intensity),
    )


def command_engine_event(
    session_id: str,
    body: AdaptiveEngineStingerEventV1 | AdaptiveEngineCueEventV1,
    *,
    db_path: Path | None = None,
) -> AdaptiveEngineCommandResultV1:
    with _LOCK:
        held = _require_held(session_id)
        if isinstance(body, AdaptiveEngineCueEventV1):
            cue = held.cues.get(body.name)
            if cue is None:
                raise AdaptiveEngineError("engine_event_unknown", session_id=session_id)
            to_state_id, transition_id = cue
            kind: EngineAckKind = "cue"
        else:
            snapshot = _require_playback(held, db_path=db_path)
            score = _load_score(held.project_id, held.score_id, db_path=db_path).score
            chosen = _resolve_stinger(
                score,
                snapshot.runtime_state_id,
                body.stinger_id,
                body.transition_id,
                session_id=session_id,
            )
            to_state_id = chosen.to_state_id
            transition_id = chosen.id
            kind = "stinger"
    return _run_command(
        session_id,
        kind=kind,
        request_id=body.request_id,
        db_path=db_path,
        command=AdaptivePlaybackRequestStateCommand(
            op="request_state",
            to_state_id=to_state_id,
            transition_id=transition_id,
        ),
    )


def command_engine_context(
    session_id: str,
    sample: object,
    *,
    db_path: Path | None = None,
) -> AdaptiveEngineCommandResultV1:
    """Apply one sample when a token remains. Otherwise keep only the newest."""
    logger.debug("Adaptive engine context offered", extra={"session_id": session_id})
    try:
        parsed = parse_adaptive_context_external(sample)
    except AdaptiveScoreError as exc:
        raise _translate(exc, context=True) from exc
    with _LOCK:
        held = _require_held(session_id)
        if not held.context_attached:
            raise AdaptiveEngineError("engine_context_unmapped", session_id=session_id)
        now_ms = _touch_now(held, None)
        offer = offer_context(held.pressure, parsed, now_ms)
        if not offer.accepted:
            held.coalesced_count += 1
            held.dropped_context_count = held.pressure.dropped_count
            logger.warning(
                "Adaptive engine context coalesced",
                extra={
                    "session_id": session_id,
                    "code": "coalesced",
                    "retry_after_ms": offer.retry_after_ms,
                },
            )
            session = _project(held, _require_playback(held, db_path=db_path))
            return AdaptiveEngineCommandResultV1(
                session=session,
                disposition="queued",
                request_id=None,
                coalesced=True,
                applied=False,
                retry_after_ms=offer.retry_after_ms,
            )
        before = _require_playback(held, db_path=db_path)
        context_snapshot = _sample_context(held, parsed, db_path=db_path)
        _raise_if_revision_conflict(context_snapshot.warnings)
        after = _require_playback(held, db_path=db_path)
        _raise_if_revision_conflict(after.warnings)
        disposition, detail_code = _classify(before, after)
        held.command_count += 1
        if disposition == "rejected":
            held.rejected_count += 1
        if disposition == "queued":
            _supersede(held, before.runtime_state_id)
            held.open_request = _OpenRequest(request_id=None, kind="context")
        _remember_playback_warnings(held, after)
        session = _project(held, after)
        _publish_ack(
            held,
            kind="context",
            disposition=disposition,
            request_id=None,
            runtime_state_id=after.runtime_state_id,
            detail_code=detail_code,
        )
        _publish_status(held, session)
    logger.debug(
        "Adaptive engine command",
        extra={
            "session_id": session_id,
            "kind": "context",
            "request_id": None,
            "disposition": disposition,
        },
    )
    return AdaptiveEngineCommandResultV1(
        session=session,
        disposition=disposition,
        request_id=None,
        coalesced=False,
        applied=True,
    )


def advance_engine_clock(
    session_id: str,
    steps: int = 1,
    *,
    db_path: Path | None = None,
) -> AdaptiveEngineSessionV1:
    """Step the latched tempo when this session owns the clock. Not an HTTP route."""
    logger.debug(
        "Adaptive engine clock step requested",
        extra={"session_id": session_id, "steps": steps},
    )
    session: AdaptiveEngineSessionV1 | None = None
    for _ in range(max(0, steps)):
        try:
            session = _step(session_id, synthetic=True, db_path=db_path)
        except Exception as exc:
            logger.error(
                "Adaptive engine tick failed",
                extra={"session_id": session_id, "error_type": type(exc).__name__},
            )
    if session is not None:
        return session
    return get_engine_session(session_id, db_path=db_path)


def subscribe_engine_feed(session_id: str, kind: Literal["events", "status"]) -> EngineFeed:
    """Register the current event-loop queue. Status sends the snapshot immediately."""
    loop = asyncio.get_running_loop()
    settings = load_adaptive_engine_settings()
    feed = EngineFeed(loop=loop, queue=asyncio.Queue(maxsize=settings.event_queue), kind=kind)
    with _LOCK:
        held = _require_held(session_id)
        held.feeds.append(feed)
        if kind == "status":
            snapshot = get_adaptive_playback(held.project_id, held.score_id)
            if snapshot is not None:
                session = _project(held, snapshot)
                held.status_signature = _signature(session)
                held.last_status_at = time.monotonic()
                body = session.model_dump(mode="json")
            else:
                body = None
        else:
            body = None
    if body is not None:
        _offer_frame(feed, {"frame": "status", "body": body})
    logger.info(
        "Adaptive engine socket accepted",
        extra={"session_id": session_id, "close_code": None, "kind": kind},
    )
    return feed


def unsubscribe_engine_feed(session_id: str, feed: EngineFeed, close_code: int) -> None:
    with _LOCK:
        held = _REGISTRY.by_id(session_id)
        if held is not None and feed in held.feeds:
            held.feeds.remove(feed)
    logger.info(
        "Adaptive engine socket closed",
        extra={"session_id": session_id, "close_code": close_code},
    )


def _run_command(
    session_id: str,
    *,
    kind: EngineAckKind,
    request_id: str | None,
    command: AdaptivePlaybackRequestStateCommand | AdaptivePlaybackSetIntensityCommand,
    db_path: Path | None,
) -> AdaptiveEngineCommandResultV1:
    logger.debug(
        "Adaptive engine command requested",
        extra={"session_id": session_id, "kind": kind, "request_id": request_id},
    )
    with _LOCK:
        held = _require_held(session_id)
        now_ms = _touch_now(held, None)
        taken = try_command(held.pressure, now_ms)
        if not taken.allowed:
            logger.warning(
                "Adaptive engine command rate limited",
                extra={
                    "session_id": session_id,
                    "code": "engine_rate_limited",
                    "retry_after_ms": taken.retry_after_ms,
                },
            )
            raise AdaptiveEngineError(
                "engine_rate_limited",
                session_id=session_id,
                retry_after_ms=taken.retry_after_ms,
            )
        before = _require_playback(held, db_path=db_path)
        _supersede(held, before.runtime_state_id)
        try:
            after = command_adaptive_playback(
                held.project_id,
                held.score_id,
                command,
                db_path=db_path,
            )
        except AdaptiveScoreError as exc:
            raise _translate(exc) from exc
        _raise_if_revision_conflict(after.warnings)
        disposition, detail_code = _classify(before, after)
        held.command_count += 1
        if disposition == "rejected":
            held.rejected_count += 1
        if disposition == "queued":
            held.open_request = _OpenRequest(request_id=request_id, kind=kind)
        else:
            held.open_request = None
        _remember_playback_warnings(held, after)
        session = _project(held, after)
        _publish_ack(
            held,
            kind=kind,
            disposition=disposition,
            request_id=request_id,
            runtime_state_id=after.runtime_state_id,
            detail_code=detail_code,
        )
        _publish_status(held, session)
    logger.debug(
        "Adaptive engine command",
        extra={
            "session_id": session_id,
            "kind": kind,
            "request_id": request_id,
            "disposition": disposition,
        },
    )
    return AdaptiveEngineCommandResultV1(
        session=session,
        disposition=disposition,
        request_id=request_id,
        coalesced=False,
        applied=True,
    )


def _step(
    session_id: str,
    *,
    synthetic: bool,
    db_path: Path | None,
) -> AdaptiveEngineSessionV1 | None:
    with _LOCK:
        held = _REGISTRY.by_id(session_id)
        if held is None:
            return None
        if held.clock_owner != "engine":
            _append_code(held.warnings, "engine_clock_not_owned")
            logger.warning(
                "Adaptive engine clock not owned",
                extra={"session_id": session_id, "code": "engine_clock_not_owned"},
            )
            snapshot = _require_playback(held, db_path=db_path)
            return _project(held, snapshot)
        settings = load_adaptive_engine_settings()
        now_ms = held.last_now_ms + settings.tick_ms if synthetic else _touch_now(held, _wall_ms())
        if synthetic:
            held.last_now_ms = now_ms
        before = _require_playback(held, db_path=db_path)
        held.remainder += held.tempo * held.ticks_per_quarter * settings.tick_ms
        ticks = held.remainder // 60000
        held.remainder %= 60000
        after = before
        if ticks > 0:
            try:
                after = command_adaptive_playback(
                    held.project_id,
                    held.score_id,
                    AdaptivePlaybackAdvanceCommand(op="advance", advance_ticks=ticks),
                    db_path=db_path,
                )
            except AdaptiveScoreError as exc:
                logger.debug(
                    "Adaptive engine translated domain code",
                    extra={"code": exc.code},
                )
                raise _translate(exc) from exc
            if not _has_code(after.warnings, "document_revision_conflict"):
                _finish_open(held, before, after)
        sample = drain_context(held.pressure, now_ms)
        if sample is not None:
            context_snapshot = _sample_context(held, sample, db_path=db_path)
            if not _has_code(context_snapshot.warnings, "document_revision_conflict"):
                after = _require_playback(held, db_path=db_path)
                if not _has_code(after.warnings, "document_revision_conflict"):
                    disposition, detail_code = _classify(before, after)
                    held.command_count += 1
                    if disposition == "queued":
                        held.open_request = _OpenRequest(request_id=None, kind="context")
                    elif disposition == "rejected":
                        held.rejected_count += 1
                    _publish_ack(
                        held,
                        kind="context",
                        disposition=disposition,
                        request_id=None,
                        runtime_state_id=after.runtime_state_id,
                        detail_code=detail_code,
                    )
        _remember_playback_warnings(held, after)
        session = _project(held, after)
        _publish_status(held, session)
        return session


async def _ticker(session_id: str) -> None:
    logger.info("Adaptive engine ticker started", extra={"session_id": session_id, "clock_owner": "engine"})
    try:
        while True:
            settings = load_adaptive_engine_settings()
            await asyncio.sleep(settings.tick_ms / 1000)
            with _LOCK:
                if _REGISTRY.by_id(session_id) is None:
                    return
            try:
                stepped = _step(session_id, synthetic=False, db_path=None)
            except Exception as exc:
                logger.error(
                    "Adaptive engine tick failed",
                    extra={"session_id": session_id, "error_type": type(exc).__name__},
                )
                continue
            if stepped is None:
                return
    except asyncio.CancelledError:
        logger.info(
            "Adaptive engine ticker stopped",
            extra={"session_id": session_id, "close_code": None},
        )
        raise


def _start_ticker(held: _HeldEngine) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning(
            "Adaptive engine ticker skipped because no event loop is running",
            extra={"session_id": held.session_id, "code": "engine_clock_not_owned"},
        )
        return
    held.task = loop.create_task(_ticker(held.session_id))


def _cancel_task(held: _HeldEngine) -> None:
    task = held.task
    held.task = None
    if task is not None and not task.done():
        task.cancel()


def _classify(
    before: AdaptivePlaybackRuntimeV1,
    after: AdaptivePlaybackRuntimeV1,
) -> tuple[EngineDisposition, str | None]:
    before_event = before.telemetry.last_event
    after_event = after.telemetry.last_event
    before_pending = None if before.pending_transition is None else before.pending_transition.transition_id
    after_pending = None if after.pending_transition is None else after.pending_transition.transition_id
    changed = after_event != before_event
    if changed and after_event in _COMMIT_EVENTS:
        return "committed", None
    if changed and after_event == "request_rejected":
        return "rejected", "engine_state_rejected"
    pending_arrived = before_pending is None and after_pending is not None
    if (changed and after_event == "request_queued") or (
        pending_arrived and after_event not in _COMMIT_EVENTS
    ):
        return "queued", None
    return "committed", None


def _finish_open(
    held: _HeldEngine,
    before: AdaptivePlaybackRuntimeV1,
    after: AdaptivePlaybackRuntimeV1,
) -> None:
    if held.open_request is None:
        return
    if after.telemetry.last_event == before.telemetry.last_event:
        return
    if after.telemetry.last_event not in _FINISH_EVENTS:
        return
    request = held.open_request
    held.open_request = None
    _publish_ack(
        held,
        kind=request.kind,
        disposition="finished",
        request_id=request.request_id,
        runtime_state_id=after.runtime_state_id,
        detail_code=None,
    )


def _supersede(held: _HeldEngine, runtime_state_id: str) -> None:
    if held.open_request is None:
        return
    previous = held.open_request
    held.open_request = None
    held.rejected_count += 1
    _publish_ack(
        held,
        kind=previous.kind,
        disposition="rejected",
        request_id=previous.request_id,
        runtime_state_id=runtime_state_id,
        detail_code="engine_superseded",
    )


def _resolve_stinger(
    score: AdaptiveScoreV1,
    runtime_state_id: str,
    stinger_id: str,
    transition_id: str | None,
    *,
    session_id: str,
):
    matches = [
        transition
        for transition in score.transitions
        if transition.from_state_id == runtime_state_id
        and transition.realization.kind == "stinger"
        and transition.realization.stinger_id == stinger_id
    ]
    if len(matches) == 1 and (transition_id is None or matches[0].id == transition_id):
        return matches[0]
    if len(matches) > 1 and transition_id is not None:
        chosen = next((item for item in matches if item.id == transition_id), None)
        if chosen is not None:
            return chosen
    raise AdaptiveEngineError(
        "engine_stinger_unwired",
        session_id=session_id,
        details={"count": len(matches)},
    )


def _project(held: _HeldEngine, snapshot: AdaptivePlaybackRuntimeV1) -> AdaptiveEngineSessionV1:
    _remember_playback_warnings(held, snapshot)
    pending = snapshot.pending_transition
    # Echo deployment flags only (no MusicState / buffer). Settings module is
    # outside the engine import ban list used by architecture tests.
    from app.adaptive_runtime_continuation_settings import (
        load_adaptive_runtime_continuation_settings,
    )

    continuation_settings = load_adaptive_runtime_continuation_settings()
    return AdaptiveEngineSessionV1(
        session_id=held.session_id,
        project_id=held.project_id,
        score_id=held.score_id,
        clock_owner=held.clock_owner,
        transport=snapshot.transport,
        runtime_state_id=snapshot.runtime_state_id,
        bar=snapshot.bar,
        beat=snapshot.beat,
        intensity=snapshot.intensity,
        phase=snapshot.phase,
        active_stinger_id=snapshot.active_stinger_id,
        pending_transition_id=None if pending is None else pending.transition_id,
        pending_to_state_id=None if pending is None else pending.to_state_id,
        warnings=list(held.warnings),
        document_revision=held.document_revision,
        context_attached=held.context_attached,
        continuous_enabled=continuation_settings.adaptive_continuous_enabled,
        engine_continuous_enabled=continuation_settings.adaptive_engine_continuous_enabled,
        telemetry=AdaptiveEngineTelemetryV1(
            command_count=held.command_count,
            rejected_count=held.rejected_count,
            coalesced_count=held.coalesced_count,
            dropped_context_count=held.dropped_context_count,
            ack_count=held.ack_count,
        ),
    )


def _remember_playback_warnings(held: _HeldEngine, snapshot: AdaptivePlaybackRuntimeV1) -> None:
    if _has_code(snapshot.warnings, "playback_queue_full"):
        _append_code(held.warnings, "engine_queue_full")


def _publish_ack(
    held: _HeldEngine,
    *,
    kind: EngineAckKind,
    disposition: Literal["committed", "queued", "rejected", "finished"],
    request_id: str | None,
    runtime_state_id: str,
    detail_code: str | None,
) -> None:
    ack = AdaptiveEngineAckV1(
        session_id=held.session_id,
        request_id=request_id,
        kind=kind,
        disposition=disposition,
        runtime_state_id=runtime_state_id,
        detail_code=detail_code,
    )
    held.ack_count += 1
    _broadcast(held, "events", ack.model_dump(mode="json"))


def _publish_status(held: _HeldEngine, session: AdaptiveEngineSessionV1, *, force: bool = False) -> None:
    signature = _signature(session)
    settings = load_adaptive_engine_settings()
    now = time.monotonic()
    interval = 1.0 / settings.status_hz
    changed = signature != held.status_signature
    due = held.last_status_at is None or (now - held.last_status_at) >= interval
    if not force and not changed and not held.status_dirty:
        return
    if not force and not due:
        held.status_dirty = True
        return
    held.status_signature = signature
    held.last_status_at = now
    held.status_dirty = False
    _broadcast(held, "status", session.model_dump(mode="json"))


def _signature(session: AdaptiveEngineSessionV1) -> tuple[Any, ...]:
    return (
        session.bar,
        session.beat,
        session.runtime_state_id,
        session.intensity,
        session.transport,
        session.phase,
        session.active_stinger_id,
        session.pending_transition_id,
    )


def _broadcast(held: _HeldEngine, kind: Literal["events", "status"], body: dict[str, Any]) -> None:
    frame_name = "ack" if kind == "events" else "status"
    for feed in list(held.feeds):
        if feed.kind != kind:
            continue
        _offer_frame(feed, {"frame": frame_name, "body": body})


def _offer_frame(feed: EngineFeed, frame: dict[str, Any]) -> None:
    def _put() -> None:
        _put_now(feed, frame)

    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is feed.loop:
        _put()
        return
    try:
        feed.loop.call_soon_threadsafe(_put)
    except RuntimeError:
        logger.info(
            "Adaptive engine socket closed",
            extra={"session_id": None, "close_code": 1001},
        )


def _put_now(feed: EngineFeed, frame: dict[str, Any]) -> None:
    if feed.close_code is not None:
        return
    if frame["frame"] == "close":
        feed.close_code = int(frame.get("code") or 1000)
        _enqueue_or_hold(feed, frame)
        return
    if feed.queue.full() and frame["frame"] == "status":
        _drop_oldest_status(feed)
    if feed.queue.full() and frame["frame"] == "status":
        return
    if feed.queue.full():
        _drop_oldest_status(feed)
    if feed.queue.full():
        feed.close_code = 1013
        logger.warning(
            "[FIX] Adaptive engine socket queue full of acknowledgements",
            extra={"close_code": 1013, "frame": frame["frame"]},
        )
        return
    _enqueue_or_hold(feed, frame)


def _enqueue_or_hold(feed: EngineFeed, frame: dict[str, Any]) -> None:
    try:
        feed.queue.put_nowait(frame)
    except asyncio.QueueFull:
        if feed.close_code is None:
            feed.close_code = 1013
        logger.warning(
            "[FIX] Adaptive engine close held beside a full socket queue",
            extra={"close_code": feed.close_code, "frame": frame["frame"]},
        )


def _drop_oldest_status(feed: EngineFeed) -> None:
    kept: list[dict[str, Any]] = []
    while not feed.queue.empty():
        item = feed.queue.get_nowait()
        if item.get("frame") != "status":
            kept.append(item)
    for item in kept:
        try:
            feed.queue.put_nowait(item)
        except asyncio.QueueFull:
            return


def _close_feeds(held: _HeldEngine, code: int) -> None:
    for feed in list(held.feeds):
        _offer_frame(feed, {"frame": "close", "code": code})
    held.feeds.clear()


def _sample_context(
    held: _HeldEngine,
    sample: AdaptiveContextExternalV1,
    *,
    db_path: Path | None,
):
    try:
        return sample_adaptive_musical_context(
            held.project_id,
            held.score_id,
            sample,
            db_path=db_path,
        )
    except AdaptiveScoreError as exc:
        raise _translate(exc, context=True) from exc


def _require_held(session_id: str) -> _HeldEngine:
    held = _REGISTRY.by_id(session_id)
    if held is None:
        raise AdaptiveEngineError("engine_session_missing")
    return held


def _require_playback(held: _HeldEngine, *, db_path: Path | None) -> AdaptivePlaybackRuntimeV1:
    try:
        snapshot = get_adaptive_playback(held.project_id, held.score_id, db_path=db_path)
    except AdaptiveScoreError as exc:
        raise _translate(exc) from exc
    if snapshot is None:
        raise AdaptiveEngineError("engine_playback_stopped", session_id=held.session_id)
    return snapshot


def _playback_or_none(project_id: str, score_id: str, *, db_path: Path | None):
    try:
        return get_adaptive_playback(project_id, score_id, db_path=db_path)
    except AdaptiveScoreError as exc:
        raise _translate(exc) from exc


def _context_or_none(project_id: str, score_id: str, *, db_path: Path | None):
    try:
        return get_adaptive_musical_context(project_id, score_id, db_path=db_path)
    except AdaptiveScoreError as exc:
        raise _translate(exc, context=True) from exc


def _load_score(project_id: str, score_id: str, *, db_path: Path | None):
    try:
        return get_adaptive_score(project_id, score_id, db_path=db_path)
    except AdaptiveScoreError as exc:
        raise _translate(exc) from exc


def _latched_tempo(project_id: str, score_id: str) -> tuple[int, int]:
    held = get_default_playback_registry().get(project_id, score_id)
    if held is None:
        raise AdaptiveEngineError("engine_playback_stopped")
    timeline = held.inputs.timeline
    return int(timeline.root_tempo), int(timeline.ticks_per_quarter)


def _rollback(
    project_id: str,
    score_id: str,
    started_playback: bool,
    started_context: bool,
    db_path: Path | None,
) -> None:
    if started_context:
        _quiet_stop_context(project_id, score_id, db_path=db_path)
    if started_playback:
        _quiet_stop_playback(project_id, score_id, db_path=db_path)


def _quiet_stop_playback(project_id: str, score_id: str, *, db_path: Path | None) -> None:
    try:
        stop_adaptive_playback(project_id, score_id, db_path=db_path)
    except AdaptiveScoreError as exc:
        logger.debug("Adaptive engine translated domain code", extra={"code": exc.code})
    except Exception as exc:
        logger.error(
            "Adaptive engine playback stop failed",
            extra={"error_type": type(exc).__name__, "project_id": project_id, "score_id": score_id},
        )


def _quiet_stop_context(project_id: str, score_id: str, *, db_path: Path | None) -> None:
    try:
        stop_adaptive_musical_context(project_id, score_id, db_path=db_path)
    except AdaptiveScoreError as exc:
        logger.debug("Adaptive engine translated domain code", extra={"code": exc.code})
    except Exception as exc:
        logger.error(
            "Adaptive engine context stop failed",
            extra={"error_type": type(exc).__name__, "project_id": project_id, "score_id": score_id},
        )


def _translate(exc: AdaptiveScoreError, *, context: bool = False) -> AdaptiveEngineError:
    logger.debug("Adaptive engine translated domain code", extra={"code": exc.code})
    if exc.code in {"project_not_found", "adaptive_score_not_found"}:
        return AdaptiveEngineError("engine_score_missing")
    if exc.code == "dangling_state_ref":
        return AdaptiveEngineError("engine_score_invalid")
    if exc.code == "adaptive_score_conflict":
        return AdaptiveEngineError("engine_revision_conflict")
    if exc.code == "playback_not_running":
        return AdaptiveEngineError("engine_playback_stopped")
    if context or exc.code in {
        "context_not_running",
        "embedded_note_material",
        "unsupported_schema_version",
        "adaptive_score_invalid",
        "adaptive_score_too_large",
    }:
        if exc.code == "context_not_running":
            return AdaptiveEngineError("engine_context_unmapped")
        return AdaptiveEngineError("engine_context_invalid")
    return AdaptiveEngineError("engine_score_invalid")


def _raise_if_revision_conflict(warnings: list[Any]) -> None:
    if _has_code(warnings, "document_revision_conflict"):
        logger.debug(
            "Adaptive engine translated domain code",
            extra={"code": "document_revision_conflict"},
        )
        raise AdaptiveEngineError("engine_revision_conflict")


def _has_code(warnings: list[Any], code: str) -> bool:
    return any(getattr(item, "code", None) == code for item in warnings)


def _append_code(warnings: list[str], code: str) -> None:
    if code in warnings or len(warnings) >= 8:
        return
    warnings.append(code)


def _touch_now(held: _HeldEngine, now_ms: int | None) -> int:
    current = _wall_ms() if now_ms is None else now_ms
    if current < held.last_now_ms:
        current = held.last_now_ms
    held.last_now_ms = current
    return current


def _wall_ms() -> int:
    return int(time.monotonic() * 1000)


def _mint_session_id() -> str:
    for _ in range(_SESSION_ID_ATTEMPTS):
        session_id = "aeng_" + secrets.token_hex(4)
        if _REGISTRY.by_id(session_id) is None:
            return session_id
    raise AdaptiveEngineError("engine_session_limit")
