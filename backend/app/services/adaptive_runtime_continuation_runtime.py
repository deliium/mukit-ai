"""One in-memory continuation session per project and score.

The session is lost on process restart. It is not written into ``body_json``.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any

from app.adaptive_runtime_continuation_schemas import (
    AdaptiveRuntimeBufferV1,
    AdaptiveRuntimeContinuationV1,
)
from app.adaptive_runtime_music_state_schemas import AdaptiveRuntimeMusicStateV1
from app.composition_plan_schemas import CompositionPlan
from app.composition_schemas import CompositionV2

logger = logging.getLogger(__name__)


@dataclass
class ContinuationJob:
    """Armed symbolic job. The prefix slice stays here, off the HTTP snapshot."""

    job_id: str
    anchor_bar: int
    mode: str
    runtime_state_id: str
    document_revision: int
    prefix_digest: str
    harmony_tail: str | None
    intensity: float
    target_start_bar: int | None
    deadline_tick: int
    armed_monotonic_ms: int
    fallback_kind: str
    pipeline_id: str
    seed: int
    ticks_per_bar: int
    generate_bars: int
    meter_changed: bool
    plan: CompositionPlan | None
    prefix_composition: CompositionV2 | None
    handle: Any = None
    coroutine: Any = None


@dataclass
class HeldContinuation:
    continuation_id: str
    mode: str
    snapshot: AdaptiveRuntimeContinuationV1
    buffer: AdaptiveRuntimeBufferV1 | None = None
    job: ContinuationJob | None = None
    active_job_id: str | None = None
    session_revision: int = 1
    continuous: bool = False
    music_state: AdaptiveRuntimeMusicStateV1 | None = None
    last_applied_digest: str | None = None
    seed_bump: int = 0
    _guard: threading.Lock = field(default_factory=threading.Lock, repr=False)


class AdaptiveRuntimeContinuationRegistry:
    """One session keyed by ``(project_id, score_id)``."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._slots: dict[tuple[str, str], HeldContinuation] = {}

    def put(self, project_id: str, score_id: str, held: HeldContinuation) -> str | None:
        key = (project_id, score_id)
        with self._lock:
            previous = self._slots.get(key)
            replaced = previous.continuation_id if previous is not None else None
            self._slots[key] = held
        logger.debug(
            "Adaptive runtime continuation session stored",
            extra={
                "project_id": project_id,
                "score_id": score_id,
                "continuation_id": held.continuation_id,
                "replaced": replaced is not None,
            },
        )
        return replaced

    def get(self, project_id: str, score_id: str) -> HeldContinuation | None:
        with self._lock:
            return self._slots.get((project_id, score_id))

    def clear(self, project_id: str, score_id: str) -> HeldContinuation | None:
        with self._lock:
            return self._slots.pop((project_id, score_id), None)


_DEFAULT = AdaptiveRuntimeContinuationRegistry()


def get_default_continuation_registry() -> AdaptiveRuntimeContinuationRegistry:
    return _DEFAULT


def reset_default_continuation_registry() -> None:
    """Replace the process-wide session map. Tests call this around each case."""
    global _DEFAULT
    _DEFAULT = AdaptiveRuntimeContinuationRegistry()
