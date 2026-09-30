"""Command, context, and rate-limit tests. HTTP only."""

from __future__ import annotations

import json
import logging

import pytest

from mukit_adaptive.client import MukitAdaptiveClient
from mukit_adaptive.errors import AdaptiveClientError
from fakes import (
    RecordingTransport,
    assert_package_has_no_studio_import,
    command_document,
    session_document,
)


def _started(transport: RecordingTransport) -> MukitAdaptiveClient:
    transport.push(201, session_document())
    client = MukitAdaptiveClient("http://127.0.0.1:8000", transport=transport)
    client.start("project", "score", 1)
    return client


def test_set_state_body_fields() -> None:
    transport = RecordingTransport()
    client = _started(transport)
    transport.push(200, command_document())
    client.set_state("danger", transition_id="tr-now")
    body = json.loads(transport.calls[-1]["body"])
    assert transport.calls[-1]["url"].endswith("/state")
    assert body["to_state_id"] == "danger"
    assert body["transition_id"] == "tr-now"
    assert body["request_id"] == "req_00000001"


def test_set_intensity_sends_point_eight() -> None:
    transport = RecordingTransport()
    client = _started(transport)
    transport.push(200, command_document())
    client.set_intensity(0.8)
    body = json.loads(transport.calls[-1]["body"])
    assert body["intensity"] == 0.8


def test_cue_and_stinger_discriminators() -> None:
    transport = RecordingTransport()
    client = _started(transport)
    transport.push(200, command_document())
    transport.push(200, command_document())
    client.fire_cue("combat")
    client.fire_stinger("stinger-hit", transition_id="tr-hit")
    cue = json.loads(transport.calls[-2]["body"])
    stinger = json.loads(transport.calls[-1]["body"])
    assert cue == {"kind": "cue", "name": "combat", "request_id": "req_00000001"}
    assert stinger["kind"] == "stinger"
    assert stinger["stinger_id"] == "stinger-hit"
    assert stinger["transition_id"] == "tr-hit"


def test_context_202_does_not_issue_a_second_request() -> None:
    transport = RecordingTransport()
    client = _started(transport)
    queued = command_document(disposition="queued", request_id=None, coalesced=True, applied=False, retry_after_ms=50)
    transport.push(202, queued)
    result = client.send_context({"threat": 0.6})
    assert result.coalesced is True
    assert result.applied is False
    context_calls = [call for call in transport.calls if call["url"].endswith("/context")]
    assert len(context_calls) == 1
    body = json.loads(context_calls[0]["body"])
    assert body["schema_version"] == "adaptive.context.external.v1"
    assert body["values"] == {"threat": 0.6}


def test_http_200_rejected_does_not_raise() -> None:
    transport = RecordingTransport()
    client = _started(transport)
    rejected = command_document(disposition="rejected")
    rejected["session"] = session_document(runtime_state_id="danger")
    transport.push(200, rejected)
    result = client.set_state("danger")
    assert result.disposition == "rejected"
    assert client.snapshot is not None
    assert client.snapshot.runtime_state_id == "danger"


def test_one_429_then_success_and_a_second_429_raises() -> None:
    slept: list[int] = []
    transport = RecordingTransport()
    client = MukitAdaptiveClient("http://127.0.0.1:8000", transport=transport, sleeper=slept.append)
    transport.push(201, session_document())
    client.start("project", "score", 1)
    limited = error_limited(2500)
    transport.push(429, limited)
    transport.push(200, command_document())
    client.set_intensity(0.4)
    assert slept == [1000]
    intensity_posts = [call for call in transport.calls if call["url"].endswith("/intensity")]
    assert len(intensity_posts) == 2

    transport.push(429, limited)
    transport.push(429, limited)
    with pytest.raises(AdaptiveClientError) as caught:
        client.set_intensity(0.4)
    assert caught.value.code == "engine_rate_limited"
    assert len([call for call in transport.calls if call["url"].endswith("/intensity")]) == 4


def test_nested_context_object_is_rejected_locally() -> None:
    transport = RecordingTransport()
    client = _started(transport)
    before = len(transport.calls)
    with pytest.raises(AdaptiveClientError) as caught:
        client.send_context({"threat": {"nested": 1}})
    assert caught.value.code == "engine_payload_invalid"
    assert len(transport.calls) == before


def test_logs_omit_context_values_and_include_command_and_warnings(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    caplog.set_level(logging.DEBUG, logger="mukit_adaptive")
    transport = RecordingTransport()
    client = _started(transport)
    warned = command_document()
    warned["session"] = session_document(warnings=["engine_queue_full"])
    transport.push(200, warned)
    client.send_context({"threat": "threat-marker-9f3c"})
    assert "threat-marker-9f3c" not in caplog.text
    assert "command_result" in caplog.text
    assert "kind=context" in caplog.text
    assert "engine_queue_full" in caplog.text


def test_package_source_does_not_import_studio() -> None:
    assert_package_has_no_studio_import()


def error_limited(retry_after_ms: int) -> dict:
    return {
        "detail": {
            "schema_version": "adaptive.engine.error.v1",
            "code": "engine_rate_limited",
            "message": "The engine command rate is exceeded.",
            "session_id": "aeng_0123abcd",
            "retry_after_ms": retry_after_ms,
            "details": None,
        }
    }
