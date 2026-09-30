"""Session start, attach, stop, and auth header tests."""

from __future__ import annotations

import logging

import http.client
import pytest

from mukit_adaptive.client import MukitAdaptiveClient, StdlibTransport
from mukit_adaptive.errors import AdaptiveClientError
from fakes import assert_package_has_no_studio_import, error_document, session_document, RecordingTransport


def _client(transport: RecordingTransport, token: str | None = None) -> MukitAdaptiveClient:
    return MukitAdaptiveClient("http://127.0.0.1:8000", token=token, transport=transport)


def test_start_201_stores_snapshot() -> None:
    transport = RecordingTransport()
    document = session_document()
    transport.push(201, document)
    client = _client(transport)
    session = client.start(document["project_id"], document["score_id"], 1)
    assert session.session_id == "aeng_0123abcd"
    assert client.snapshot is not None
    assert client.snapshot.clock_owner == "engine"
    assert transport.calls[0]["method"] == "POST"
    assert transport.calls[0]["url"].endswith("/adaptive/session")


def test_missing_token_omits_authorization() -> None:
    transport = RecordingTransport()
    transport.push(201, session_document())
    client = _client(transport, token=None)
    client.start("project", "score", 1)
    assert "Authorization" not in transport.calls[0]["headers"]


def test_token_sends_bearer() -> None:
    transport = RecordingTransport()
    transport.push(201, session_document())
    client = _client(transport, token="header-token")
    client.start("project", "score", 1)
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer header-token"


def test_401_raises_engine_unauthorized() -> None:
    transport = RecordingTransport()
    transport.push(
        401,
        error_document("engine_unauthorized", "The engine request was not authorized."),
    )
    client = _client(transport, token="header-token")
    with pytest.raises(AdaptiveClientError) as caught:
        client.start("project", "score", 1)
    assert caught.value.code == "engine_unauthorized"
    assert caught.value.status == 401


def test_409_without_adopt_raises_and_does_not_get() -> None:
    transport = RecordingTransport()
    transport.push(
        409,
        error_document(
            "engine_session_exists",
            "An engine session already exists for this score.",
            session_id="aeng_0123abcd",
            details={"session_id": "aeng_0123abcd"},
        ),
    )
    client = _client(transport)
    with pytest.raises(AdaptiveClientError) as caught:
        client.start("project", "score", 1)
    assert caught.value.code == "engine_session_exists"
    assert len(transport.calls) == 1
    assert client.snapshot is None


def test_409_with_adopt_calls_get() -> None:
    transport = RecordingTransport()
    transport.push(
        409,
        error_document(
            "engine_session_exists",
            "An engine session already exists for this score.",
            session_id="aeng_0123abcd",
            details={"session_id": "aeng_0123abcd"},
        ),
    )
    transport.push(200, session_document())
    client = _client(transport)
    session = client.start("project", "score", 1, adopt_existing=True)
    assert session.session_id == "aeng_0123abcd"
    assert transport.calls[1]["method"] == "GET"
    assert transport.calls[1]["url"].endswith("/adaptive/session/aeng_0123abcd")


def test_delete_204_empty_body_returns(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="mukit_adaptive")
    transport = RecordingTransport()
    document = session_document()
    transport.push(201, document)
    transport.push(204, b"")
    client = _client(transport)
    client.start(document["project_id"], document["score_id"], 1)
    client.stop()
    assert client.snapshot is None
    assert transport.calls[1]["method"] == "DELETE"
    assert "invalid error" not in caplog.text


def test_redirect_raises_and_does_not_issue_a_second_request() -> None:
    transport = RecordingTransport()
    transport.push(302, b"")
    client = _client(transport)
    with pytest.raises(AdaptiveClientError) as caught:
        client.start("project", "score", 1)
    assert caught.value.status == 302
    assert len(transport.calls) == 1


def test_stdlib_transport_returns_redirect_without_a_second_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Response:
        status = 302

        def read(self) -> bytes:
            return b""

    class _Connection:
        instances = 0

        def __init__(self, host: str, port: int | None = None, timeout: int | None = None) -> None:
            _Connection.instances += 1

        def request(self, method: str, path: str, body: bytes | None = None, headers: dict | None = None) -> None:
            self.path = path

        def getresponse(self) -> _Response:
            return _Response()

        def close(self) -> None:
            return None

    monkeypatch.setattr(http.client, "HTTPConnection", _Connection)
    result = StdlibTransport().request("POST", "http://127.0.0.1:8000/adaptive/session", {}, b"{}")
    assert result.status == 302
    assert _Connection.instances == 1


def test_logs_omit_the_bearer_token(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    caplog.set_level(logging.DEBUG, logger="mukit_adaptive")
    token = "token-super-unique-zz9"
    transport = RecordingTransport()
    transport.push(201, session_document())
    client = _client(transport, token=token)
    client.start("project", "score", 1)
    assert token not in caplog.text
    assert "Authorization" not in caplog.text
    assert "session_start" in caplog.text
    assert "aeng_0123abcd" in caplog.text


def test_package_source_does_not_import_studio() -> None:
    assert_package_has_no_studio_import()
