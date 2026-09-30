"""Socket close, snapshot frames, and reconnect."""

from __future__ import annotations

import builtins
import json
import logging
import queue
import threading

import pytest

from mukit_adaptive.client import HttpResult, MukitAdaptiveClient, SocketClosed, open_default_websocket
from mukit_adaptive.errors import AdaptiveClientError
from mukit_adaptive.reconnect import ReconnectCounter, delay_ms_for_attempt
from fakes import (
    RecordingTransport,
    assert_package_has_no_studio_import,
    load_json,
    session_document,
)


class ScriptedSocket:
    def __init__(self, script: list[tuple]) -> None:
        self._inbox: queue.Queue = queue.Queue()
        for item in script:
            self._inbox.put(item)
        self.url = ""
        self.headers: dict[str, str] = {}

    def recv(self, timeout: float | None = None) -> str:
        try:
            item = self._inbox.get(timeout=0.05 if timeout is None else timeout)
        except queue.Empty as exc:
            raise TimeoutError from exc
        if item[0] == "text":
            return item[1]
        if item[0] == "close":
            raise SocketClosed(item[1])
        raise AssertionError(item)

    def close(self) -> None:
        self._inbox.put(("close", 1006))


class ScriptedOpener:
    def __init__(self, scripts: dict[str, list[tuple]] | None = None) -> None:
        self.scripts = scripts or {}
        self.sockets: list[ScriptedSocket] = []
        self._lock = threading.Lock()

    def __call__(self, url: str, headers: dict[str, str]) -> ScriptedSocket:
        feed = "status" if "/adaptive/status" in url else "events"
        with self._lock:
            index = len(self.sockets)
        script = list(self.scripts.get(feed, []))
        if not self.scripts and index < 2:
            script = [("close", 1013)]
        socket = ScriptedSocket(script)
        socket.url = url
        socket.headers = headers
        with self._lock:
            self.sockets.append(socket)
        return socket


def _wait_opens(client: MukitAdaptiveClient, count: int) -> None:
    with client._cond:
        ready = client._cond.wait_for(lambda: client._socket_opens >= count, 2)
    assert ready, client._socket_opens


def test_delay_doubles_and_caps() -> None:
    delays = [delay_ms_for_attempt(attempt) for attempt in range(1, 9)]
    assert delays == [200, 400, 800, 1600, 3200, 5000, 5000, 5000]
    counter = ReconnectCounter(max_attempts=8)
    assert [counter.next_delay_ms() for _ in range(8)] == delays
    assert counter.next_delay_ms() is None


def test_1013_on_both_feeds_gets_once_and_reopens_without_commands() -> None:
    transport = RecordingTransport()
    document = session_document()
    transport.push(201, document)
    transport.push(200, document)
    client = MukitAdaptiveClient(
        "http://127.0.0.1:8000",
        token="socket-token-unique-qq8",
        transport=transport,
        opener=ScriptedOpener(),
        sleeper=lambda _delay: None,
        clock=lambda: 0,
    )
    client.start(document["project_id"], document["score_id"], 1)
    client.listen(None, None, None)
    _wait_opens(client, 4)
    gets = [call for call in transport.calls if call["method"] == "GET"]
    posts_after_start = [call for call in transport.calls if call["method"] == "POST"]
    assert len(gets) == 1
    assert gets[0]["url"].endswith("/adaptive/session/aeng_0123abcd")
    assert len(posts_after_start) == 1
    opener_sockets = client._opener.sockets if isinstance(client._opener, ScriptedOpener) else []
    assert len(opener_sockets) == 4
    assert all("socket-token-unique-qq8" not in socket.url for socket in opener_sockets)
    assert all(socket.headers["Authorization"] == "Bearer socket-token-unique-qq8" for socket in opener_sockets)
    client.close()


def test_second_close_during_get_does_not_start_another_cycle() -> None:
    document = session_document()
    release = threading.Event()
    started = threading.Event()

    class _Gate(RecordingTransport):
        def request(self, method: str, url: str, headers: dict[str, str], body: bytes | None):
            self.calls.append(
                {"method": method, "url": url, "headers": dict(headers), "body": body}
            )
            if method == "GET":
                started.set()
                assert release.wait(2)
            if not self._queued:
                raise AssertionError(f"unexpected {method} {url}")
            status, payload = self._queued.pop(0)
            return HttpResult(status=status, body=payload)

    gate = _Gate()
    gate.push(201, document)
    gate.push(200, document)
    client = MukitAdaptiveClient(
        "http://127.0.0.1:8000",
        transport=gate,
        opener=ScriptedOpener(),
        sleeper=lambda _delay: None,
        clock=lambda: 1,
    )
    client.start(document["project_id"], document["score_id"], 1)
    client.listen(None, None, None)
    assert started.wait(2)
    assert client._socket_opens == 2
    assert len([call for call in gate.calls if call["method"] == "GET"]) == 1
    release.set()
    _wait_opens(client, 4)
    assert len([call for call in gate.calls if call["method"] == "GET"]) == 1
    assert not any(call["url"].endswith("/state") or call["url"].endswith("/event") for call in gate.calls)
    client.close()


def test_4401_and_4404_stop_without_a_new_session() -> None:
    for code, public in ((4401, "engine_unauthorized"), (4404, "engine_session_missing")):
        transport = RecordingTransport()
        document = session_document()
        transport.push(201, document)
        opener = ScriptedOpener({"events": [("close", code)], "status": [("close", code)]})
        client = MukitAdaptiveClient(
            "http://127.0.0.1:8000",
            transport=transport,
            opener=opener,
            sleeper=lambda _delay: None,
        )
        client.start(document["project_id"], document["score_id"], 1)
        client.listen(None, None, None)
        assert client._stopped.wait(2)
        assert client.terminal_code == public
        assert not any(call["method"] == "GET" for call in transport.calls)
        assert len([call for call in transport.calls if call["method"] == "POST"]) == 1
        client.close()


def test_status_replaces_snapshot_and_error_frame_does_not() -> None:
    transport = RecordingTransport()
    document = session_document()
    transport.push(201, document)
    updated = session_document(runtime_state_id="danger")
    ack = load_json("ack.v1.json")
    error = load_json("error.v1.json")
    opener = ScriptedOpener(
        {
            "status": [("text", json.dumps(updated))],
            "events": [("text", json.dumps(ack)), ("text", json.dumps(error))],
        }
    )
    seen: dict[str, object] = {}
    ack_ready = threading.Event()
    error_ready = threading.Event()

    def on_ack(frame: object) -> None:
        seen["ack"] = frame
        ack_ready.set()

    def on_error(frame: object) -> None:
        seen["snapshot"] = None if client.snapshot is None else client.snapshot.runtime_state_id
        seen["error"] = frame
        error_ready.set()

    client = MukitAdaptiveClient("http://127.0.0.1:8000", transport=transport, opener=opener)
    client.start(document["project_id"], document["score_id"], 1)
    client.listen(on_ack, None, on_error)
    assert ack_ready.wait(2)
    assert error_ready.wait(2)
    assert client.snapshot is not None
    assert client.snapshot.runtime_state_id == "danger"
    assert seen["snapshot"] == "danger"
    assert getattr(seen["ack"], "disposition") == "finished"
    assert getattr(seen["error"], "code") == "engine_session_missing"
    client.close()


def test_status_callback_can_send_a_command_without_deadlock() -> None:
    transport = RecordingTransport()
    document = session_document()
    transport.push(201, document)
    transport.push(200, _command_with(document))
    opener = ScriptedOpener({"status": [("text", json.dumps(session_document(bar=2)))], "events": []})
    done = threading.Event()
    client = MukitAdaptiveClient("http://127.0.0.1:8000", transport=transport, opener=opener)

    def on_status(_session: object) -> None:
        client.set_intensity(0.3)
        done.set()

    client.start(document["project_id"], document["score_id"], 1)
    client.listen(None, on_status, None)
    assert done.wait(1)
    client.close()


def test_local_close_1006_stays_stopped() -> None:
    transport = RecordingTransport()
    document = session_document()
    transport.push(201, document)
    client = MukitAdaptiveClient(
        "http://127.0.0.1:8000",
        transport=transport,
        opener=ScriptedOpener({"events": [], "status": []}),
        sleeper=lambda _delay: None,
    )
    client.start(document["project_id"], document["score_id"], 1)
    client.listen(None, None, None)
    _wait_opens(client, 2)
    client.close()
    assert not any(call["method"] == "GET" for call in transport.calls)
    assert client.terminal_code is None


def test_reconnect_now_uses_one_get() -> None:
    transport = RecordingTransport()
    document = session_document()
    transport.push(201, document)
    transport.push(200, document)
    client = MukitAdaptiveClient(
        "http://127.0.0.1:8000",
        transport=transport,
        opener=ScriptedOpener({"events": [], "status": []}),
        sleeper=lambda _delay: None,
        clock=lambda: 5,
    )
    client.start(document["project_id"], document["score_id"], 1)
    client.listen(None, None, None)
    _wait_opens(client, 2)
    client.reconnect_now()
    _wait_opens(client, 4)
    assert len([call for call in transport.calls if call["method"] == "GET"]) == 1
    client.close()


def test_missing_websockets_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def blocked(name: str, globals: dict | None = None, locals: dict | None = None, fromlist: tuple = (), level: int = 0):
        if name == "websockets" or name.startswith("websockets."):
            raise ImportError("no websockets")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(AdaptiveClientError) as caught:
        open_default_websocket("ws://127.0.0.1:8000/adaptive/events", {})
    assert caught.value.code == "engine_client_dependency_missing"


def test_package_source_does_not_import_studio() -> None:
    assert_package_has_no_studio_import()


def _command_with(document: dict) -> dict:
    command = json.loads(json.dumps({"session": document, "disposition": "committed", "request_id": "req_now1", "coalesced": False, "applied": True, "retry_after_ms": None}))
    return command
