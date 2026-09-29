"""One in-memory playback session per project and score.

The session is lost on process restart. It is not written into ``body_json``.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from app.services.adaptive_playback import PlaybackClock, PlaybackInputs

logger = logging.getLogger(__name__)


@dataclass
class HeldPlayback:
    clock: PlaybackClock
    inputs: PlaybackInputs


class AdaptivePlaybackRegistry:
    """One session keyed by ``(project_id, score_id)``."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._slots: dict[tuple[str, str], HeldPlayback] = {}

    def put(self, project_id: str, score_id: str, held: HeldPlayback) -> str | None:
        key = (project_id, score_id)
        with self._lock:
            previous = self._slots.get(key)
            replaced = previous.clock.playback_id if previous is not None else None
            self._slots[key] = held
        logger.info(
            "Adaptive playback session stored",
            extra={
                "project_id": project_id,
                "score_id": score_id,
                "playback_id": held.clock.playback_id,
                "action": "start",
            },
        )
        if replaced is not None:
            logger.debug(
                "Adaptive playback session replaced",
                extra={
                    "project_id": project_id,
                    "score_id": score_id,
                    "playback_id": replaced,
                    "action": "start",
                },
            )
        return replaced

    def get(self, project_id: str, score_id: str) -> HeldPlayback | None:
        with self._lock:
            return self._slots.get((project_id, score_id))

    def clear(self, project_id: str, score_id: str) -> str | None:
        key = (project_id, score_id)
        with self._lock:
            current = self._slots.pop(key, None)
        playback_id = None if current is None else current.clock.playback_id
        if playback_id is not None:
            logger.info(
                "Adaptive playback session cleared",
                extra={
                    "project_id": project_id,
                    "score_id": score_id,
                    "playback_id": playback_id,
                    "action": "clear",
                },
            )
        return playback_id


_DEFAULT = AdaptivePlaybackRegistry()


def get_default_playback_registry() -> AdaptivePlaybackRegistry:
    return _DEFAULT


def reset_default_playback_registry() -> None:
    """Replace the process-wide session map. API tests call this before each case."""
    global _DEFAULT
    _DEFAULT = AdaptivePlaybackRegistry()
