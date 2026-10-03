"""In-process Ardour OSC peer for ``ARDOUR_COMPANION_FAKE=1`` CI.

Records outbound paths and emits deterministic feedback. Never opens UDP.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.services.ardour_osc_paths import (
    PATH_LOCATE,
    PATH_REC_ENABLE_TOGGLE,
    PATH_SET_SURFACE,
    PATH_STRIP_FADER,
    PATH_STRIP_MUTE,
    PATH_STRIP_PAN,
    PATH_STRIP_SOLO,
    PATH_TRANSPORT_PLAY,
    PATH_TRANSPORT_STOP,
)

logger = logging.getLogger(__name__)


@dataclass
class FakeOutbound:
    path: str
    args: tuple[Any, ...]


@dataclass
class ArdourOscFakePeer:
    """Deterministic mock surface that mirrors ship-1 control paths."""

    outbound: list[FakeOutbound] = field(default_factory=list)
    transport_playing: bool = False
    locate_samples: int = 0
    record_armed: bool = False
    selected_ssid: int = 1
    strips: dict[int, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.strips:
            self.strips = {
                1: {
                    "name": "Audio 1",
                    "fader": 0.75,
                    "pan": 0.5,
                    "mute": 0,
                    "solo": 0,
                },
                2: {
                    "name": "MIDI 1",
                    "fader": 0.5,
                    "pan": 0.5,
                    "mute": 0,
                    "solo": 0,
                },
            }

    def handle_outbound(self, path: str, args: list[Any]) -> list[tuple[str, list[Any]]]:
        """Record a command and return feedback messages to apply locally."""
        self.outbound.append(FakeOutbound(path=path, args=tuple(args)))
        logger.debug(
            "Fake peer outbound",
            extra={"path": path, "arg_count": len(args)},
        )
        feedback: list[tuple[str, list[Any]]] = []

        if path == PATH_SET_SURFACE:
            feedback.append(("/heartbeat", [1]))
            feedback.append(("/select/ssid", [self.selected_ssid]))
            feedback.append((PATH_REC_ENABLE_TOGGLE, [1 if self.record_armed else 0]))
            feedback.append(
                (PATH_TRANSPORT_PLAY if self.transport_playing else PATH_TRANSPORT_STOP, [1])
            )
            for ssid, strip in sorted(self.strips.items()):
                feedback.append(("/strip/name", [ssid, strip["name"]]))
                feedback.append((PATH_STRIP_FADER, [ssid, float(strip["fader"])]))
                feedback.append((PATH_STRIP_PAN, [ssid, float(strip["pan"])]))
                feedback.append((PATH_STRIP_MUTE, [ssid, int(strip["mute"])]))
                feedback.append((PATH_STRIP_SOLO, [ssid, int(strip["solo"])]))
            return feedback

        if path == PATH_TRANSPORT_PLAY:
            self.transport_playing = True
            feedback.append((PATH_TRANSPORT_PLAY, [1]))
            return feedback

        if path == PATH_TRANSPORT_STOP:
            self.transport_playing = False
            feedback.append((PATH_TRANSPORT_STOP, [1]))
            return feedback

        if path == PATH_LOCATE and args:
            self.locate_samples = int(args[0])
            feedback.append(("/position/samples", [self.locate_samples]))
            return feedback

        if path == PATH_REC_ENABLE_TOGGLE:
            self.record_armed = not self.record_armed
            feedback.append((PATH_REC_ENABLE_TOGGLE, [1 if self.record_armed else 0]))
            return feedback

        if path == PATH_STRIP_FADER and len(args) >= 2:
            ssid = int(args[0])
            value = float(args[1])
            strip = self.strips.setdefault(
                ssid,
                {"name": f"Strip {ssid}", "fader": 0.0, "pan": 0.5, "mute": 0, "solo": 0},
            )
            strip["fader"] = value
            feedback.append((PATH_STRIP_FADER, [ssid, value]))
            return feedback

        if path == PATH_STRIP_PAN and len(args) >= 2:
            ssid = int(args[0])
            value = float(args[1])
            strip = self.strips.setdefault(
                ssid,
                {"name": f"Strip {ssid}", "fader": 0.75, "pan": 0.5, "mute": 0, "solo": 0},
            )
            strip["pan"] = value
            feedback.append((PATH_STRIP_PAN, [ssid, value]))
            return feedback

        if path == PATH_STRIP_MUTE and len(args) >= 2:
            ssid = int(args[0])
            value = int(args[1])
            strip = self.strips.setdefault(
                ssid,
                {"name": f"Strip {ssid}", "fader": 0.75, "pan": 0.5, "mute": 0, "solo": 0},
            )
            strip["mute"] = value
            feedback.append((PATH_STRIP_MUTE, [ssid, value]))
            return feedback

        if path == PATH_STRIP_SOLO and len(args) >= 2:
            ssid = int(args[0])
            value = int(args[1])
            strip = self.strips.setdefault(
                ssid,
                {"name": f"Strip {ssid}", "fader": 0.75, "pan": 0.5, "mute": 0, "solo": 0},
            )
            strip["solo"] = value
            feedback.append((PATH_STRIP_SOLO, [ssid, value]))
            return feedback

        return feedback

    def paths_sent(self) -> list[str]:
        return [item.path for item in self.outbound]
