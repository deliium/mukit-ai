"""One in-memory pending transition per project and score.

The slot is lost on process restart. It is not written into ``body_json``.
"""

from __future__ import annotations

import logging
import threading

from app.adaptive_score_schemas import (
    ADAPTIVE_SCORE_ERROR_CODES,
    AdaptiveScoreError,
    AdaptiveTransitionScheduleV1,
)

logger = logging.getLogger(__name__)


class TransitionPendingRegistry:
    """One schedule slot keyed by ``(project_id, score_id)``."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._slots: dict[tuple[str, str], AdaptiveTransitionScheduleV1] = {}

    def put(self, schedule: AdaptiveTransitionScheduleV1) -> str | None:
        key = (schedule.project_id, schedule.score_id)
        with self._lock:
            previous = self._slots.get(key)
            replaced = previous.request_id if previous is not None else None
            stored = schedule.model_copy(update={"replaced_request_id": replaced})
            self._slots[key] = stored
        logger.info(
            "Adaptive transition pending slot updated",
            extra={
                "project_id": schedule.project_id,
                "score_id": schedule.score_id,
                "request_id": schedule.request_id,
                "action": "put",
                "replaced": replaced is not None,
            },
        )
        return replaced

    def get(self, project_id: str, score_id: str) -> AdaptiveTransitionScheduleV1 | None:
        with self._lock:
            return self._slots.get((project_id, score_id))

    def cancel(self, project_id: str, score_id: str, request_id: str) -> None:
        key = (project_id, score_id)
        with self._lock:
            current = self._slots.get(key)
            if current is None or current.request_id != request_id:
                logger.info(
                    "Adaptive transition pending cancel missed",
                    extra={
                        "project_id": project_id,
                        "score_id": score_id,
                        "request_id": request_id,
                        "action": "cancel",
                        "replaced": False,
                    },
                )
                raise AdaptiveScoreError(
                    "transition_request_not_pending",
                    ADAPTIVE_SCORE_ERROR_CODES["transition_request_not_pending"],
                    http_status=409,
                )
            del self._slots[key]
        logger.info(
            "Adaptive transition pending slot cleared",
            extra={
                "project_id": project_id,
                "score_id": score_id,
                "request_id": request_id,
                "action": "cancel",
                "replaced": False,
            },
        )


_DEFAULT = TransitionPendingRegistry()


def get_default_registry() -> TransitionPendingRegistry:
    return _DEFAULT


def reset_default_registry() -> None:
    """Replace the process-wide slot map. API tests call this before each case."""
    global _DEFAULT
    _DEFAULT = TransitionPendingRegistry()
