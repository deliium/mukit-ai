"""UDP OSC transport + feedback listener (or in-process fake peer)."""

from __future__ import annotations

import logging
import socket
import threading
from typing import Any

from app.ardour_companion_schemas import ArdourCompanionError, ArdourHostClass
from app.services.ardour_feedback_state import ArdourFeedbackState
from app.services.ardour_osc_codec import OscCodecError, decode_osc_message, encode_osc_message
from app.services.ardour_osc_fake_peer import ArdourOscFakePeer
from app.services.ardour_osc_paths import build_set_surface

logger = logging.getLogger(__name__)

_FORBIDDEN_FEEDBACK_PORT = 3819


class ArdourOscTransport:
    """Owns send/feedback sockets (real) or a fake peer; exposes ``close`` for lifespan."""

    def __init__(
        self,
        *,
        host: str,
        osc_port: int,
        feedback_port: int,
        host_class: ArdourHostClass,
        fake: bool,
        stale_timeout_ms: int,
    ) -> None:
        self.host = host
        self.osc_port = osc_port
        self.feedback_port = feedback_port
        self.host_class = host_class
        self.fake = fake
        self.state = ArdourFeedbackState(stale_timeout_ms=stale_timeout_ms)
        self._fake_peer = ArdourOscFakePeer() if fake else None
        self._send_sock: socket.socket | None = None
        self._feedback_sock: socket.socket | None = None
        self._listener: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._closed = False

    @property
    def fake_peer(self) -> ArdourOscFakePeer | None:
        return self._fake_peer

    def open(self) -> None:
        """Bind feedback (real) and mark connecting → awaiting_feedback after set_surface."""
        with self._lock:
            if self._closed:
                raise ArdourCompanionError("ardour_not_connected", "Transport already closed.")
            self.state.reset_observed()
            self.state.mark_connecting()
            if self.feedback_port == _FORBIDDEN_FEEDBACK_PORT:
                self.state.mark_error("ardour_bind_failed")
                raise ArdourCompanionError(
                    "ardour_bind_failed",
                    details={"reason": "feedback_port_3819"},
                )
            if not self.fake:
                try:
                    feedback = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    feedback.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    feedback.bind(("0.0.0.0", self.feedback_port))
                    feedback.settimeout(0.25)
                    self._feedback_sock = feedback
                    send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    self._send_sock = send_sock
                except OSError as exc:
                    self.close()
                    self.state.mark_error("ardour_bind_failed")
                    logger.warning(
                        "Ardour feedback bind failed",
                        extra={"code": "ardour_bind_failed", "feedback_port": self.feedback_port},
                    )
                    raise ArdourCompanionError(
                        "ardour_bind_failed",
                        details={"reason": "os_error"},
                    ) from exc
                self._stop.clear()
                self._listener = threading.Thread(
                    target=self._listen_loop,
                    name="ardour-osc-feedback",
                    daemon=True,
                )
                self._listener.start()

            logger.info(
                "Ardour OSC transport open",
                extra={
                    "host_class": self.host_class,
                    "osc_port": self.osc_port,
                    "feedback_port": self.feedback_port,
                    "fake": self.fake,
                },
            )
            self.state.mark_awaiting_feedback()
            packet = build_set_surface(feedback_port=self.feedback_port)
            self.send_raw(packet)

    def send_raw(self, packet: bytes) -> None:
        path, args = decode_osc_message(packet)
        self.send_message(path, list(args), packet=packet)

    def send_message(
        self,
        path: str,
        args: list[Any] | None = None,
        *,
        packet: bytes | None = None,
    ) -> None:
        args = list(args or [])
        with self._lock:
            if self._closed:
                raise ArdourCompanionError("ardour_not_connected")
            if self.fake and self._fake_peer is not None:
                feedback = self._fake_peer.handle_outbound(path, args)
                for fb_path, fb_args in feedback:
                    self.state.apply_message(fb_path, fb_args)
                return
            data = packet if packet is not None else encode_osc_message(path, *args)
            if self._send_sock is None:
                raise ArdourCompanionError("ardour_not_connected")
            try:
                self._send_sock.sendto(data, (self.host, self.osc_port))
            except OSError as exc:
                logger.warning(
                    "Ardour OSC send failed",
                    extra={"code": "ardour_osc_send_failed", "path": path},
                )
                raise ArdourCompanionError(
                    "ardour_osc_send_failed",
                    details={"path": path},
                ) from exc
            logger.debug("OSC send", extra={"path": path, "arg_count": len(args)})

    def _listen_loop(self) -> None:
        sock = self._feedback_sock
        if sock is None:
            return
        while not self._stop.is_set():
            try:
                data, _addr = sock.recvfrom(65535)
            except TimeoutError:
                with self._lock:
                    self.state.refresh_connection_from_age()
                continue
            except OSError:
                if self._stop.is_set():
                    break
                continue
            try:
                path, args = decode_osc_message(data)
            except OscCodecError:
                logger.debug("Ignoring undecodable OSC feedback", extra={"arg_count": 0})
                continue
            with self._lock:
                self.state.apply_message(path, list(args))

    def close(self) -> None:
        """Close UDP sockets and stop the listener. Idempotent."""
        with self._lock:
            if self._closed and self._send_sock is None and self._feedback_sock is None:
                return
            self._closed = True
            self._stop.set()
            send_sock = self._send_sock
            feedback_sock = self._feedback_sock
            listener = self._listener
            self._send_sock = None
            self._feedback_sock = None
            self._listener = None
            self.state.mark_disconnected()
        if send_sock is not None:
            try:
                send_sock.close()
            except OSError:
                pass
        if feedback_sock is not None:
            try:
                feedback_sock.close()
            except OSError:
                pass
        if listener is not None and listener.is_alive():
            listener.join(timeout=1.0)
        logger.info(
            "Ardour OSC transport closed",
            extra={
                "host_class": self.host_class,
                "osc_port": self.osc_port,
                "feedback_port": self.feedback_port,
                "fake": self.fake,
            },
        )
