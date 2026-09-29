"""One in-memory musical-context session per project and score.

The session is lost on process restart. It is not written into ``body_json``.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from app.adaptive_musical_context_schemas import AdaptiveContextMappingV1
from app.services.adaptive_musical_context import MusicalContextClock

logger = logging.getLogger(__name__)


@dataclass
class HeldContext:
    clock: MusicalContextClock
    mapping: AdaptiveContextMappingV1


class AdaptiveMusicalContextRegistry:
    """One session keyed by ``(project_id, score_id)``."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._slots: dict[tuple[str, str], HeldContext] = {}

    def put(self, project_id: str, score_id: str, held: HeldContext) -> str | None:
        key = (project_id, score_id)
        with self._lock:
            previous = self._slots.get(key)
            replaced = previous.clock.context_id if previous is not None else None
            self._slots[key] = held
        logger.debug(
            "Adaptive musical context session stored",
            extra={
                "project_id": project_id,
                "score_id": score_id,
                "context_id": held.clock.context_id,
                "binding_count": len(held.mapping.bindings),
            },
        )
        return replaced

    def get(self, project_id: str, score_id: str) -> HeldContext | None:
        with self._lock:
            return self._slots.get((project_id, score_id))

    def clear(self, project_id: str, score_id: str) -> str | None:
        with self._lock:
            current = self._slots.pop((project_id, score_id), None)
        return None if current is None else current.clock.context_id


_DEFAULT = AdaptiveMusicalContextRegistry()


def get_default_context_registry() -> AdaptiveMusicalContextRegistry:
    return _DEFAULT


def reset_default_context_registry() -> None:
    """Replace the process-wide session map. Tests call this around each case."""
    global _DEFAULT
    _DEFAULT = AdaptiveMusicalContextRegistry()
