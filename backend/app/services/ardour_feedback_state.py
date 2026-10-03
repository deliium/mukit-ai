"""Feedback-observed Ardour companion state reducer (pure + timestamps)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from app.ardour_companion_schemas import (
    ArdourConnectionState,
    ArdourStripObservedV1,
)
from app.services.ardour_osc_paths import (
    PATH_HEARTBEAT,
    PATH_LOCATE,
    PATH_REC_ENABLE_TOGGLE,
    PATH_SELECT_SSID,
    PATH_STRIP_FADER,
    PATH_STRIP_MUTE,
    PATH_STRIP_NAME,
    PATH_STRIP_PAN,
    PATH_STRIP_SOLO,
    PATH_TRANSPORT_PLAY,
    PATH_TRANSPORT_STOP,
)

logger = logging.getLogger(__name__)


@dataclass
class StripState:
    ssid: int
    name: str | None = None
    fader: float | None = None
    pan: float | None = None
    mute: int | None = None
    solo: int | None = None


@dataclass
class ArdourFeedbackState:
    """Mutable observed state updated only from inbound OSC (or fake peer)."""

    connection_state: ArdourConnectionState = "disconnected"
    transport_playing: bool | None = None
    locate_samples: int | None = None
    last_requested_locate_samples: int | None = None
    record_armed: bool | None = None
    selected_ssid: int | None = None
    strips: dict[int, StripState] = field(default_factory=dict)
    last_feedback_monotonic: float | None = None
    last_error_code: str | None = None
    stale_timeout_ms: int = 3000

    def mark_connecting(self) -> None:
        self.connection_state = "connecting"
        self.last_error_code = None
        logger.debug("Feedback state", extra={"field": "connection_state", "value": "connecting"})

    def mark_awaiting_feedback(self) -> None:
        self.connection_state = "awaiting_feedback"
        logger.debug(
            "Feedback state",
            extra={"field": "connection_state", "value": "awaiting_feedback"},
        )

    def mark_disconnected(self) -> None:
        self.connection_state = "disconnected"
        self.last_feedback_monotonic = None
        logger.debug(
            "Feedback state",
            extra={"field": "connection_state", "value": "disconnected"},
        )

    def mark_error(self, code: str) -> None:
        self.connection_state = "error"
        self.last_error_code = code
        logger.warning("Feedback state error", extra={"code": code})

    def note_requested_locate(self, samples: int) -> None:
        self.last_requested_locate_samples = samples
        logger.debug("Feedback state", extra={"field": "last_requested_locate_samples"})

    def feedback_age_ms(self, *, now: float | None = None) -> int | None:
        if self.last_feedback_monotonic is None:
            return None
        clock = time.monotonic() if now is None else now
        return max(0, int((clock - self.last_feedback_monotonic) * 1000))

    def is_stale(self, *, now: float | None = None) -> bool:
        age = self.feedback_age_ms(now=now)
        if age is None:
            return self.connection_state in {"connected", "awaiting_feedback", "stale"}
        return age > self.stale_timeout_ms

    def refresh_connection_from_age(self, *, now: float | None = None) -> None:
        """Promote/demote connection_state from feedback age without inventing fields."""
        if self.connection_state in {"disconnected", "connecting", "error", "disabled"}:
            return
        if self.last_feedback_monotonic is None:
            if self.connection_state != "awaiting_feedback":
                self.connection_state = "awaiting_feedback"
            return
        if self.is_stale(now=now):
            if self.connection_state != "stale":
                self.connection_state = "stale"
                logger.warning(
                    "Ardour feedback stale",
                    extra={"code": "ardour_feedback_stale"},
                )
            return
        if self.connection_state in {"awaiting_feedback", "stale", "connected"}:
            self.connection_state = "connected"

    def _touch(self, *, now: float | None = None) -> None:
        self.last_feedback_monotonic = time.monotonic() if now is None else now
        self.refresh_connection_from_age(now=self.last_feedback_monotonic)

    def _strip(self, ssid: int) -> StripState:
        strip = self.strips.get(ssid)
        if strip is None:
            strip = StripState(ssid=ssid)
            self.strips[ssid] = strip
        return strip

    def apply_message(
        self,
        path: str,
        args: list[Any],
        *,
        now: float | None = None,
    ) -> None:
        """Reduce one inbound OSC path into observed fields."""
        logger.debug("Inbound OSC path", extra={"path": path, "arg_count": len(args)})
        self._touch(now=now)

        if path in {PATH_HEARTBEAT, "/heartbeat"}:
            return

        if path == PATH_TRANSPORT_PLAY:
            self.transport_playing = _as_boolish(args[0]) if args else True
            logger.debug("Feedback state", extra={"field": "transport_playing"})
            return

        if path == PATH_TRANSPORT_STOP:
            # Ardour may send state; treat presence as stop confirmation when 1/true or empty.
            if not args:
                self.transport_playing = False
            else:
                stopped = _as_boolish(args[0])
                self.transport_playing = not stopped if stopped is not None else False
            logger.debug("Feedback state", extra={"field": "transport_playing"})
            return

        if path in {"/transport_speed", "/transport/speed"}:
            if args:
                speed = float(args[0])
                self.transport_playing = speed != 0.0
            return

        if path == PATH_LOCATE or path in {"/position/smpte", "/position/samples"}:
            if args:
                try:
                    self.locate_samples = int(args[0])
                    logger.debug("Feedback state", extra={"field": "locate_samples"})
                except (TypeError, ValueError):
                    pass
            return

        if path in {PATH_REC_ENABLE_TOGGLE, "/rec_enable", "/master/rec_enable"}:
            if args:
                armed = _as_boolish(args[0])
                if armed is not None:
                    self.record_armed = armed
                    logger.debug("Feedback state", extra={"field": "record_armed"})
            return

        if path == PATH_SELECT_SSID or path.endswith("/select"):
            if args:
                try:
                    self.selected_ssid = int(args[0])
                    logger.debug("Feedback state", extra={"field": "selected_ssid"})
                except (TypeError, ValueError):
                    pass
            return

        if path == PATH_STRIP_NAME and len(args) >= 2:
            ssid = int(args[0])
            self._strip(ssid).name = str(args[1])
            logger.debug("Feedback state", extra={"field": "strip_name"})
            return

        if path == PATH_STRIP_FADER and len(args) >= 2:
            ssid = int(args[0])
            self._strip(ssid).fader = float(args[1])
            logger.debug("Feedback state", extra={"field": "strip_fader"})
            return

        if path in {PATH_STRIP_PAN, "/strip/pan_stereo_position"} and len(args) >= 2:
            ssid = int(args[0])
            self._strip(ssid).pan = float(args[1])
            logger.debug("Feedback state", extra={"field": "strip_pan"})
            return

        if path == PATH_STRIP_MUTE and len(args) >= 2:
            ssid = int(args[0])
            self._strip(ssid).mute = 1 if _as_boolish(args[1]) else 0
            logger.debug("Feedback state", extra={"field": "strip_mute"})
            return

        if path == PATH_STRIP_SOLO and len(args) >= 2:
            ssid = int(args[0])
            self._strip(ssid).solo = 1 if _as_boolish(args[1]) else 0
            logger.debug("Feedback state", extra={"field": "strip_solo"})
            return

        # Unknown paths still count as heartbeat-like activity via _touch.
        return

    def strips_list(self) -> list[ArdourStripObservedV1]:
        ordered = sorted(self.strips.values(), key=lambda s: s.ssid)
        return [
            ArdourStripObservedV1(
                ssid=s.ssid,
                name=s.name,
                fader=s.fader,
                pan=s.pan,
                mute=s.mute,
                solo=s.solo,
            )
            for s in ordered
        ]

    def reset_observed(self) -> None:
        self.transport_playing = None
        self.locate_samples = None
        self.last_requested_locate_samples = None
        self.record_armed = None
        self.selected_ssid = None
        self.strips.clear()
        self.last_feedback_monotonic = None
        self.last_error_code = None


def _as_boolish(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "on", "yes"}:
            return True
        if text in {"0", "false", "off", "no"}:
            return False
    return None
