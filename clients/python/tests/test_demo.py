"""Terminal demo start body, phase keys, and quit behavior."""

from __future__ import annotations

import json
import logging

import pytest

from mukit_adaptive.client import MukitAdaptiveClient
from mukit_adaptive.demo import run_demo
from fakes import RecordingTransport, command_document, session_document


def _environ(**extra: str) -> dict[str, str]:
    document = session_document()
    values = {
        "MUKIT_ADAPTIVE_BASE_URL": "http://127.0.0.1:8000",
        "MUKIT_ADAPTIVE_PROJECT_ID": document["project_id"],
        "MUKIT_ADAPTIVE_SCORE_ID": document["score_id"],
        "MUKIT_ADAPTIVE_DOCUMENT_REVISION": "1",
    }
    values.update(extra)
    return values


def _started_client() -> tuple[RecordingTransport, MukitAdaptiveClient]:
    transport = RecordingTransport()
    transport.push(201, session_document())
    client = MukitAdaptiveClient("http://127.0.0.1:8000", token="token-super-unique-zz9", transport=transport)
    return transport, client


def test_start_sends_recipe_mapping_and_combat_cue(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    caplog.set_level(logging.DEBUG, logger="mukit_adaptive")
    transport, client = _started_client()
    code = run_demo(argv=[], environ=_environ(MUKIT_ADAPTIVE_TOKEN="token-super-unique-zz9"), lines=[], client=client, listen=False)
    assert code == 0
    body = json.loads(transport.calls[0]["body"])
    assert body["mapping"]["schema_version"] == "adaptive.context.mapping.v1"
    assert body["mapping"]["baseline_state_id"] == "explore"
    assert body["cues"] == [{"name": "combat", "to_state_id": "combat_state"}]
    assert "baseline_state_id" not in caplog.text
    assert "combat_state" not in caplog.text
    assert "token-super-unique-zz9" not in caplog.text
    assert len([call for call in transport.calls if call["url"].endswith("/adaptive/session")]) == 1


def test_combat_key_fires_cue_and_intensity_without_another_session() -> None:
    transport, client = _started_client()
    for _ in range(3):
        transport.push(200, command_document())
    code = run_demo(argv=[], environ=_environ(), lines=["3", "q"], client=client, listen=False)
    assert code == 0
    session_posts = [
        call
        for call in transport.calls
        if call["method"] == "POST" and call["url"].endswith("/adaptive/session")
    ]
    assert len(session_posts) == 1
    bodies = [json.loads(call["body"]) for call in transport.calls if call["body"]]
    cue = next(body for body in bodies if body.get("kind") == "cue")
    intensity = next(body for body in bodies if "intensity" in body)
    assert cue["name"] == "combat"
    assert intensity["intensity"] == 0.9
    assert not any(call["method"] == "DELETE" for call in transport.calls)


def test_quit_closes_without_delete_and_stop_flag_deletes() -> None:
    transport, client = _started_client()
    code = run_demo(argv=[], environ=_environ(), lines=["q"], client=client, listen=False)
    assert code == 0
    assert not any(call["method"] == "DELETE" for call in transport.calls)

    stop_transport, stop_client = _started_client()
    stop_transport.push(204, b"")
    code = run_demo(argv=["--stop"], environ=_environ(), lines=["q"], client=stop_client, listen=False)
    assert code == 0
    assert any(call["method"] == "DELETE" and "/adaptive/session/" in call["url"] for call in stop_transport.calls)


def test_missing_env_logs_variable_names_only(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    caplog.set_level(logging.DEBUG, logger="mukit_adaptive")
    token = "token-super-unique-zz9"
    code = run_demo(argv=[], environ={"MUKIT_ADAPTIVE_TOKEN": token}, lines=[], listen=False)
    assert code == 1
    assert "MUKIT_ADAPTIVE_PROJECT_ID" in caplog.text
    assert "MUKIT_ADAPTIVE_SCORE_ID" in caplog.text
    assert "MUKIT_ADAPTIVE_DOCUMENT_REVISION" in caplog.text
    assert token not in caplog.text
    assert "baseline_state_id" not in caplog.text
    assert "adaptive.context.mapping.v1" not in caplog.text
