"""Synchronous client for the public adaptive music engine.

Socket reads run on one background thread. HTTP calls and snapshot writes
share one lock. Callbacks run after that lock is released.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlencode, urlsplit, urlunsplit

import http.client

from mukit_adaptive.errors import AdaptiveClientError
from mukit_adaptive.log import log_event
from mukit_adaptive.models import (
    CONTEXT_SCHEMA,
    EngineCommandResult,
    EngineSession,
    parse_ack,
    parse_command_result,
    parse_error_document,
    parse_session,
    request_id_ok,
    session_id_ok,
    validate_context_values,
)
from mukit_adaptive.reconnect import DEFAULT_MAX_ATTEMPTS, ReconnectCounter

_POLL_SECONDS = 0.05
_RATE_LIMIT_CAP_MS = 1000

HttpOpener = Callable[[str, dict[str, str]], "AdaptiveSocket"]
Sleeper = Callable[[int], None]
Clock = Callable[[], float]


class SocketClosed(Exception):
    """The peer or the local close ended one socket."""

    def __init__(self, code: int) -> None:
        super().__init__(str(code))
        self.code = code


class AdaptiveSocket:
    """The small surface ``listen`` needs from a socket."""

    def recv(self, timeout: float | None = None) -> str:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


@dataclass
class HttpResult:
    status: int
    body: bytes


class StdlibTransport:
    """``http.client`` transport. It does not follow redirects."""

    def request(self, method: str, url: str, headers: dict[str, str], body: bytes | None) -> HttpResult:
        parts = urlsplit(url)
        log_event(logging.DEBUG, "http_transport_enter", method=method, path=parts.path)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise AdaptiveClientError("engine_payload_invalid", "invalid base URL")
        connection_cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"
        connection = connection_cls(parts.hostname, parts.port, timeout=30)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            payload = response.read()
            status = response.status
        finally:
            connection.close()
        log_event(logging.DEBUG, "http_transport_exit", method=method, status=status)
        return HttpResult(status=status, body=payload)


@dataclass
class _Pair:
    events: AdaptiveSocket
    status: AdaptiveSocket
    session_id: str


class _WebSocketAdapter(AdaptiveSocket):
    """Blocking wrapper around ``websockets.sync.client``."""

    def __init__(self, connection: Any, closed_type: type[BaseException]) -> None:
        self._connection = connection
        self._closed_type = closed_type

    def recv(self, timeout: float | None = None) -> str:
        try:
            message = self._connection.recv(timeout=timeout)
        except TimeoutError:
            raise
        except self._closed_type as exc:
            raise SocketClosed(_close_code(exc)) from exc
        if isinstance(message, bytes):
            return message.decode("utf-8")
        if not isinstance(message, str):
            raise AdaptiveClientError("engine_payload_invalid", "invalid socket text")
        return message

    def close(self) -> None:
        self._connection.close()


def open_default_websocket(url: str, headers: dict[str, str]) -> AdaptiveSocket:
    """Open one socket. Imports ``websockets`` only when listening needs it."""
    log_event(logging.DEBUG, "websocket_import_enter")
    try:
        from websockets.exceptions import ConnectionClosed
        from websockets.sync.client import connect
    except ImportError as exc:
        log_event(
            logging.ERROR,
            "websocket_dependency_missing",
            code="engine_client_dependency_missing",
            error_class=type(exc).__name__,
        )
        raise AdaptiveClientError(
            "engine_client_dependency_missing",
            "websockets is not installed",
        ) from exc
    log_event(logging.DEBUG, "websocket_import_exit")
    connection = connect(url, additional_headers=dict(headers))
    return _WebSocketAdapter(connection, ConnectionClosed)


class MukitAdaptiveClient:
    """Drive one already-stored adaptive score over ``/adaptive/*``."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        token: str | None = None,
        *,
        transport: Any = None,
        opener: HttpOpener | None = None,
        sleeper: Sleeper | None = None,
        clock: Clock | None = None,
        max_reconnect_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        log_event(logging.DEBUG, "client_init")
        self._http_base = _normalize_base_url(base_url)
        self._ws_base = _websocket_base(self._http_base)
        self._token = token or None
        self._transport = transport if transport is not None else StdlibTransport()
        self._opener = opener
        self._sleeper = sleeper if sleeper is not None else _sleep_ms
        self._clock = clock if clock is not None else time.monotonic
        self._counter = ReconnectCounter(max_reconnect_attempts)
        self._lock = threading.RLock()
        self._pair_lock = threading.Lock()
        self._cond = threading.Condition()
        self._snapshot: EngineSession | None = None
        self._request_counter = 0
        self._thread: threading.Thread | None = None
        self._pair: _Pair | None = None
        self._intentional = False
        self._give_up = False
        self._reconnect_requested = False
        self._terminal_code: str | None = None
        self._socket_opens = 0
        self._stopped = threading.Event()
        self._on_ack: Callable[..., None] | None = None
        self._on_status: Callable[..., None] | None = None
        self._on_error: Callable[..., None] | None = None
        log_event(logging.DEBUG, "client_init_exit", ws_scheme=urlsplit(self._ws_base).scheme)

    @property
    def snapshot(self) -> EngineSession | None:
        with self._lock:
            return self._snapshot

    @property
    def terminal_code(self) -> str | None:
        return self._terminal_code

    def next_request_id(self) -> str:
        with self._lock:
            self._request_counter += 1
            request_id = f"req_{self._request_counter:08d}"
        log_event(logging.DEBUG, "next_request_id", request_id=request_id)
        return request_id

    def start(
        self,
        project_id: str,
        score_id: str,
        expected_document_revision: int,
        mapping: dict[str, Any] | None = None,
        cues: list[dict[str, Any]] | None = None,
        *,
        adopt_existing: bool = False,
    ) -> EngineSession:
        """``POST /adaptive/session``. Adopt only when the caller sets the flag."""
        log_event(logging.DEBUG, "start_enter", project_id=project_id, score_id=score_id)
        body: dict[str, Any] = {
            "project_id": _require_caller_text(project_id, "project_id"),
            "score_id": _require_caller_text(score_id, "score_id"),
            "expected_document_revision": _require_revision(expected_document_revision),
        }
        if mapping is not None:
            if not isinstance(mapping, dict):
                raise AdaptiveClientError("engine_payload_invalid", "invalid mapping")
            body["mapping"] = mapping
        if cues is not None:
            if not isinstance(cues, list):
                raise AdaptiveClientError("engine_payload_invalid", "invalid cues")
            body["cues"] = cues
        try:
            session = self._read_session("POST", "/adaptive/session", body)
        except AdaptiveClientError as exc:
            if not (adopt_existing and exc.status == 409 and exc.code == "engine_session_exists"):
                raise
            existing = _existing_session_id(exc)
            if existing is None:
                raise
            session = self.attach(existing)
            log_event(logging.DEBUG, "start_exit", session_id=session.session_id)
            return session
        log_event(
            logging.INFO,
            "session_start",
            session_id=session.session_id,
            clock_owner=session.clock_owner,
        )
        log_event(logging.DEBUG, "start_exit", session_id=session.session_id)
        return session

    def attach(self, session_id: str) -> EngineSession:
        """``GET /adaptive/session/{session_id}`` and replace the snapshot."""
        log_event(logging.DEBUG, "attach_enter", session_id=session_id)
        if not isinstance(session_id, str) or not session_id_ok(session_id):
            raise AdaptiveClientError("engine_payload_invalid", "invalid session_id")
        session = self._read_session("GET", f"/adaptive/session/{session_id}", None)
        log_event(
            logging.INFO,
            "session_attach",
            session_id=session.session_id,
            clock_owner=session.clock_owner,
        )
        log_event(logging.DEBUG, "attach_exit", session_id=session.session_id)
        return session

    def stop(self) -> None:
        """``DELETE`` the current session, then stop sockets. ``204`` has no JSON body."""
        with self._lock:
            session = self._snapshot
        if session is None:
            raise AdaptiveClientError("engine_session_missing", "No engine session is attached.")
        log_event(logging.DEBUG, "stop_enter", session_id=session.session_id)
        with self._lock:
            result = self._exchange("DELETE", f"/adaptive/session/{session.session_id}", None)
            if result.status != 204:
                raise self._failure(result)
            self._snapshot = None
        log_event(
            logging.INFO,
            "session_stop",
            session_id=session.session_id,
            clock_owner=session.clock_owner,
        )
        self.close()
        log_event(logging.DEBUG, "stop_exit", session_id=session.session_id)

    def set_state(
        self,
        to_state_id: str,
        transition_id: str | None = None,
        request_id: str | None = None,
    ) -> EngineCommandResult:
        log_event(logging.DEBUG, "set_state_enter")
        body: dict[str, Any] = {
            "to_state_id": _require_caller_text(to_state_id, "to_state_id"),
            "request_id": self._resolve_request_id(request_id),
        }
        if transition_id is not None:
            body["transition_id"] = _require_caller_text(transition_id, "transition_id")
        result = self._command("state", "state", body)
        log_event(logging.DEBUG, "set_state_exit", request_id=result.request_id, disposition=result.disposition)
        return result

    def set_intensity(self, intensity: float, request_id: str | None = None) -> EngineCommandResult:
        log_event(logging.DEBUG, "set_intensity_enter")
        if isinstance(intensity, bool) or not isinstance(intensity, (int, float)):
            raise AdaptiveClientError("engine_payload_invalid", "invalid intensity")
        body = {"intensity": intensity, "request_id": self._resolve_request_id(request_id)}
        result = self._command("intensity", "intensity", body)
        log_event(
            logging.DEBUG,
            "set_intensity_exit",
            request_id=result.request_id,
            disposition=result.disposition,
        )
        return result

    def fire_stinger(
        self,
        stinger_id: str,
        transition_id: str | None = None,
        request_id: str | None = None,
    ) -> EngineCommandResult:
        log_event(logging.DEBUG, "fire_stinger_enter")
        body: dict[str, Any] = {
            "kind": "stinger",
            "stinger_id": _require_caller_text(stinger_id, "stinger_id"),
            "request_id": self._resolve_request_id(request_id),
        }
        if transition_id is not None:
            body["transition_id"] = _require_caller_text(transition_id, "transition_id")
        result = self._command("stinger", "event", body)
        log_event(logging.DEBUG, "fire_stinger_exit", request_id=result.request_id)
        return result

    def fire_cue(self, name: str, request_id: str | None = None) -> EngineCommandResult:
        log_event(logging.DEBUG, "fire_cue_enter")
        body = {
            "kind": "cue",
            "name": _require_caller_text(name, "name"),
            "request_id": self._resolve_request_id(request_id),
        }
        result = self._command("cue", "event", body)
        log_event(logging.DEBUG, "fire_cue_exit", request_id=result.request_id)
        return result

    def send_context(
        self,
        values: dict[str, Any],
        source_id: str | None = None,
        observed_at_ms: int | None = None,
    ) -> EngineCommandResult:
        log_event(logging.DEBUG, "send_context_enter")
        body: dict[str, Any] = {
            "schema_version": CONTEXT_SCHEMA,
            "values": validate_context_values(values),
        }
        if source_id is not None:
            body["source_id"] = _require_caller_text(source_id, "source_id")
        if observed_at_ms is not None:
            if isinstance(observed_at_ms, bool) or not isinstance(observed_at_ms, int) or observed_at_ms < 0:
                raise AdaptiveClientError("engine_payload_invalid", "invalid observed_at_ms")
            body["observed_at_ms"] = observed_at_ms
        result = self._command("context", "context", body)
        log_event(logging.DEBUG, "send_context_exit", request_id=result.request_id, coalesced=result.coalesced)
        return result

    def maintain_continuous(self) -> dict[str, Any]:
        """``POST …/continuous/maintain``. Returns the continuation snapshot JSON.

        Does not mutate the engine session snapshot. When the engine continuous
        flag is off the server returns ``adaptive_engine_continuous_disabled``.
        """
        log_event(logging.DEBUG, "maintain_continuous_enter")
        with self._lock:
            session = self._snapshot
            if session is None:
                raise AdaptiveClientError("engine_session_missing", "No engine session is attached.")
            path = f"/adaptive/session/{session.session_id}/continuous/maintain"
            result = self._exchange("POST", path, None)
            if result.status != 200:
                raise self._failure(result)
            payload = _decode_json(result)
        if not isinstance(payload, dict):
            raise AdaptiveClientError("engine_payload_invalid", "invalid continuous snapshot")
        log_event(
            logging.DEBUG,
            "maintain_continuous_exit",
            session_id=session.session_id,
            job_status=payload.get("job_status"),
            continuous=payload.get("continuous"),
        )
        return payload

    def get_continuous_buffer(self) -> dict[str, Any] | None:
        """``GET …/continuous/buffer``. ``None`` when the server returns 204."""
        log_event(logging.DEBUG, "get_continuous_buffer_enter")
        with self._lock:
            session = self._snapshot
            if session is None:
                raise AdaptiveClientError("engine_session_missing", "No engine session is attached.")
            path = f"/adaptive/session/{session.session_id}/continuous/buffer"
            result = self._exchange("GET", path, None)
            if result.status == 204:
                log_event(logging.DEBUG, "get_continuous_buffer_exit", empty=True)
                return None
            if result.status != 200:
                raise self._failure(result)
            payload = _decode_json(result)
        if not isinstance(payload, dict):
            raise AdaptiveClientError("engine_payload_invalid", "invalid continuous buffer")
        log_event(
            logging.DEBUG,
            "get_continuous_buffer_exit",
            session_id=session.session_id,
            event_count=len(payload.get("events") or []) if isinstance(payload.get("events"), list) else 0,
        )
        return payload

    def listen(
        self,
        on_ack: Callable[..., None] | None,
        on_status: Callable[..., None] | None,
        on_error: Callable[..., None] | None,
    ) -> None:
        """Open the events and status sockets on one background thread."""
        log_event(logging.DEBUG, "listen_enter")
        if self._thread is not None and self._thread.is_alive():
            log_event(logging.DEBUG, "listen_exit", already_running=True)
            return
        self._on_ack = on_ack
        self._on_status = on_status
        self._on_error = on_error
        self._intentional = False
        self._give_up = False
        self._terminal_code = None
        self._reconnect_requested = False
        self._stopped.clear()
        self._counter.reset()
        self._thread = threading.Thread(target=self._socket_loop, name="mukit-adaptive-listen", daemon=True)
        self._thread.start()
        log_event(logging.DEBUG, "listen_exit", already_running=False)

    def reconnect_now(self) -> None:
        """Run the abnormal-close path for the same session. Do not POST commands."""
        log_event(logging.DEBUG, "reconnect_now_enter")
        self._reconnect_requested = True
        self._wake_sockets()
        log_event(logging.DEBUG, "reconnect_now_exit")

    def close(self) -> None:
        """Close sockets. This does not ``DELETE`` the server session."""
        log_event(logging.DEBUG, "close_enter")
        self._intentional = True
        self._reconnect_requested = False
        self._drop_current_pair()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)
        log_event(logging.DEBUG, "close_exit")

    def _command(self, kind: str, suffix: str, body: dict[str, Any]) -> EngineCommandResult:
        with self._lock:
            session = self._snapshot
            if session is None:
                raise AdaptiveClientError("engine_session_missing", "No engine session is attached.")
            path = f"/adaptive/session/{session.session_id}/{suffix}"
            result = self._exchange("POST", path, body)
            if result.status not in {200, 202}:
                raise self._failure(result)
            parsed = parse_command_result(_decode_json(result))
            self._snapshot = parsed.session
            warnings = parsed.session.warnings
            session_id = parsed.session.session_id
        log_event(
            logging.DEBUG,
            "command_result",
            session_id=session_id,
            kind=kind,
            request_id=parsed.request_id,
            disposition=parsed.disposition,
            coalesced=parsed.coalesced,
        )
        _log_warnings(session_id, warnings)
        return parsed

    def _read_session(self, method: str, path: str, body: dict[str, Any] | None) -> EngineSession:
        with self._lock:
            result = self._exchange(method, path, body)
            if result.status not in {200, 201}:
                raise self._failure(result)
            session = parse_session(_decode_json(result))
            self._snapshot = session
            warnings = session.warnings
        _log_warnings(session.session_id, warnings)
        return session

    def _exchange(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        *,
        allow_retry: bool = True,
    ) -> HttpResult:
        url = self._http_base + path
        headers = self._auth_headers(json_body=body is not None)
        payload = None if body is None else json.dumps(body).encode("utf-8")
        log_event(logging.DEBUG, "http_request", method=method, path=path)
        result = self._transport.request(method, url, headers, payload)
        log_event(logging.DEBUG, "http_response", method=method, path=path, status=result.status)
        if 300 <= result.status < 400:
            raise AdaptiveClientError(
                "engine_payload_invalid",
                "HTTP redirect is not followed",
                status=result.status,
            )
        if result.status == 429 and allow_retry:
            error = self._failure(result)
            delay = 0 if error.retry_after_ms is None else min(error.retry_after_ms, _RATE_LIMIT_CAP_MS)
            session_id = self._snapshot.session_id if self._snapshot is not None else "none"
            log_event(
                logging.WARNING,
                "rate_limited",
                session_id=session_id,
                code=error.code,
                retry_after_ms=error.retry_after_ms if error.retry_after_ms is not None else 0,
            )
            self._sleeper(delay)
            return self._exchange(method, path, body, allow_retry=False)
        if result.status >= 400:
            raise self._failure(result)
        return result

    def _failure(self, result: HttpResult) -> AdaptiveClientError:
        if not result.body:
            return AdaptiveClientError("engine_payload_invalid", "invalid error", status=result.status)
        try:
            payload = json.loads(result.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AdaptiveClientError("engine_payload_invalid", "invalid error", status=result.status) from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("detail"), dict):
            raise AdaptiveClientError("engine_payload_invalid", "invalid error", status=result.status)
        return parse_error_document(payload["detail"], status=result.status)

    def _auth_headers(self, *, json_body: bool) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if json_body:
            headers["Content-Type"] = "application/json"
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _resolve_request_id(self, request_id: str | None) -> str:
        if request_id is None:
            return self.next_request_id()
        if not isinstance(request_id, str) or not request_id_ok(request_id):
            raise AdaptiveClientError("engine_payload_invalid", "invalid request_id")
        return request_id

    def _socket_loop(self) -> None:
        log_event(logging.DEBUG, "socket_loop_enter")
        try:
            while not self._intentional and not self._give_up:
                try:
                    pair = self._open_pair()
                except AdaptiveClientError as exc:
                    log_event(
                        logging.DEBUG,
                        "socket_open_failed",
                        code=exc.code,
                        error_class=type(exc).__name__,
                    )
                    if exc.code in {"engine_client_dependency_missing", "engine_session_missing"}:
                        self._fail(exc.code, exc.session_id)
                        return
                    if not self._refresh_snapshot():
                        return
                    continue
                except Exception as exc:
                    log_event(logging.DEBUG, "socket_open_failed", code="connect", error_class=type(exc).__name__)
                    if not self._refresh_snapshot():
                        return
                    continue
                self._counter.reset()
                with self._pair_lock:
                    self._pair = pair
                outcome = self._pump(pair)
                self._drop_pair(pair)
                if self._intentional or self._give_up or outcome in {"stop", "fatal"}:
                    return
                if not self._refresh_snapshot():
                    return
        finally:
            self._stopped.set()
            log_event(logging.DEBUG, "socket_loop_exit")

    def _open_pair(self) -> _Pair:
        with self._lock:
            session = self._snapshot
            if session is None:
                raise AdaptiveClientError("engine_session_missing", "No engine session is attached.")
            session_id = session.session_id
            headers = self._auth_headers(json_body=False)
        opener = self._opener if self._opener is not None else open_default_websocket
        events_url = self._socket_url("events", session_id)
        status_url = self._socket_url("status", session_id)
        _reject_token_in_url(events_url, self._token)
        _reject_token_in_url(status_url, self._token)
        events = opener(events_url, dict(headers))
        try:
            status = opener(status_url, dict(headers))
        except Exception:
            _safe_close(events)
            raise
        with self._cond:
            self._socket_opens += 2
            self._cond.notify_all()
        log_event(logging.INFO, "socket_open", session_id=session_id, feed="events")
        log_event(logging.INFO, "socket_open", session_id=session_id, feed="status")
        return _Pair(events=events, status=status, session_id=session_id)

    def _socket_url(self, feed: str, session_id: str) -> str:
        query = urlencode({"session_id": session_id})
        return f"{self._ws_base}/adaptive/{feed}?{query}"

    def _pump(self, pair: _Pair) -> str:
        while not self._intentional and not self._give_up:
            closed: list[int] = []
            for feed, socket in (("events", pair.events), ("status", pair.status)):
                try:
                    message = socket.recv(timeout=_POLL_SECONDS)
                except TimeoutError:
                    continue
                except SocketClosed as exc:
                    log_event(
                        logging.INFO,
                        "socket_close",
                        session_id=pair.session_id,
                        feed=feed,
                        close_code=exc.code,
                    )
                    closed.append(exc.code)
                    continue
                self._dispatch(feed, message)
            forced = self._reconnect_requested
            if forced:
                self._reconnect_requested = False
            if closed or forced:
                if self._intentional:
                    return "stop"
                terminal = next((code for code in closed if code in {4401, 4404}), None)
                if terminal is not None:
                    public = "engine_unauthorized" if terminal == 4401 else "engine_session_missing"
                    self._fail(public, pair.session_id)
                    return "fatal"
                return "reconnect"
        return "stop"

    def _dispatch(self, feed: str, message: str) -> None:
        log_event(logging.DEBUG, "socket_frame", feed=feed)
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            self._emit(self._on_error, AdaptiveClientError("engine_payload_invalid", "invalid socket text"))
            return
        schema = payload.get("schema_version") if isinstance(payload, dict) else None
        try:
            if schema == "adaptive.engine.session.v1":
                session = parse_session(payload)
                with self._lock:
                    self._snapshot = session
                _log_warnings(session.session_id, session.warnings)
                self._emit(self._on_status, session)
                return
            if schema == "adaptive.engine.ack.v1":
                self._emit(self._on_ack, parse_ack(payload))
                return
            if schema == "adaptive.engine.error.v1":
                self._emit(self._on_error, parse_error_document(payload))
                return
        except AdaptiveClientError as exc:
            self._emit(self._on_error, exc)
            return
        self._emit(self._on_error, AdaptiveClientError("engine_payload_invalid", "invalid socket text"))

    def _refresh_snapshot(self) -> bool:
        while not self._intentional and not self._give_up:
            delay = self._counter.next_delay_ms()
            session_id = self._current_session_id()
            if delay is None:
                self._fail("engine_reconnect_exhausted", session_id)
                return False
            now = self._clock()
            log_event(
                logging.INFO,
                "reconnect_attempt",
                session_id=session_id or "none",
                attempt=self._counter.attempt,
                delay_ms=delay,
            )
            log_event(logging.DEBUG, "reconnect_clock", clock=now)
            self._sleeper(delay)
            if self._intentional or self._give_up:
                return False
            if not session_id:
                self._fail("engine_session_missing", None)
                return False
            try:
                self.attach(session_id)
            except AdaptiveClientError as exc:
                log_event(
                    logging.DEBUG,
                    "reconnect_get_failed",
                    session_id=session_id,
                    code=exc.code,
                    status=exc.status if exc.status is not None else 0,
                )
                if exc.status in {401, 404} or exc.code in {"engine_unauthorized", "engine_session_missing"}:
                    self._fail(exc.code, session_id)
                    return False
                continue
            return True
        return False

    def _fail(self, code: str, session_id: str | None) -> None:
        if self._give_up:
            return
        self._give_up = True
        self._terminal_code = code
        log_event(
            logging.ERROR,
            "reconnect_stopped",
            session_id=session_id or "none",
            code=code,
        )
        self._emit(self._on_error, AdaptiveClientError(code, code, session_id=session_id))
        self._stopped.set()

    def _emit(self, callback: Callable[..., None] | None, value: object) -> None:
        if callback is None:
            return
        try:
            callback(value)
        except Exception as exc:
            log_event(logging.ERROR, "callback_failed", error_class=type(exc).__name__)

    def _current_session_id(self) -> str | None:
        with self._lock:
            if self._snapshot is None:
                return None
            return self._snapshot.session_id

    def _wake_sockets(self) -> None:
        with self._pair_lock:
            pair = self._pair
        if pair is None:
            return
        for socket in (pair.events, pair.status):
            _safe_close(socket)

    def _drop_current_pair(self) -> None:
        with self._pair_lock:
            pair = self._pair
            self._pair = None
        if pair is not None:
            self._drop_pair(pair)

    def _drop_pair(self, pair: _Pair) -> None:
        with self._pair_lock:
            if self._pair is pair:
                self._pair = None
        _safe_close(pair.events)
        _safe_close(pair.status)


def _normalize_base_url(base_url: str) -> str:
    if not isinstance(base_url, str) or not base_url.strip():
        raise AdaptiveClientError("engine_payload_invalid", "invalid base URL")
    parts = urlsplit(base_url.strip())
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise AdaptiveClientError("engine_payload_invalid", "invalid base URL")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise AdaptiveClientError("engine_payload_invalid", "invalid base URL")
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _websocket_base(http_base: str) -> str:
    parts = urlsplit(http_base)
    scheme = "wss" if parts.scheme == "https" else "ws"
    return urlunsplit((scheme, parts.netloc, parts.path, "", ""))


def _reject_token_in_url(url: str, token: str | None) -> None:
    query = urlsplit(url).query
    if "token=" in query or (token and token in query):
        raise AdaptiveClientError("engine_payload_invalid", "token must not be in the socket URL")


def _existing_session_id(exc: AdaptiveClientError) -> str | None:
    if isinstance(exc.session_id, str) and session_id_ok(exc.session_id):
        return exc.session_id
    details = exc.details or {}
    nested = details.get("session_id")
    if isinstance(nested, str) and session_id_ok(nested):
        return nested
    return None


def _decode_json(result: HttpResult) -> object:
    if not result.body:
        raise AdaptiveClientError("engine_payload_invalid", "invalid body", status=result.status)
    try:
        return json.loads(result.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdaptiveClientError("engine_payload_invalid", "invalid body", status=result.status) from exc


def _log_warnings(session_id: str, warnings: tuple[str, ...]) -> None:
    if not warnings:
        return
    log_event(
        logging.DEBUG,
        "snapshot_warnings",
        session_id=session_id,
        warnings=",".join(warnings),
    )


def _close_code(exc: BaseException) -> int:
    received = getattr(exc, "rcvd", None)
    received_code = None if received is None else getattr(received, "code", None)
    if isinstance(received_code, int):
        return received_code
    sent = getattr(exc, "sent", None)
    sent_code = None if sent is None else getattr(sent, "code", None)
    if isinstance(sent_code, int):
        return sent_code
    return 1006


def _safe_close(socket: AdaptiveSocket) -> None:
    try:
        socket.close()
    except Exception as exc:
        log_event(logging.DEBUG, "socket_close_failed", error_class=type(exc).__name__)


def _sleep_ms(delay_ms: int) -> None:
    time.sleep(delay_ms / 1000)


def _require_caller_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise AdaptiveClientError("engine_payload_invalid", f"invalid {field}")
    return value


def _require_revision(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise AdaptiveClientError("engine_payload_invalid", "invalid expected_document_revision")
    return value
