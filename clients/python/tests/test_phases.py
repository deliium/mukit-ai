"""Phase recipe application."""

from __future__ import annotations

import json
import logging

import pytest

from mukit_adaptive.client import MukitAdaptiveClient
from mukit_adaptive.phases import apply_phase, load_phase_recipe
from fakes import FIXTURES, RecordingTransport, assert_package_has_no_studio_import, command_document, session_document


def _client(attached: bool) -> tuple[MukitAdaptiveClient, RecordingTransport]:
    transport = RecordingTransport()
    transport.push(201, session_document(context_attached=attached))
    client = MukitAdaptiveClient("http://127.0.0.1:8000", transport=transport)
    client.start("project", "score", 1)
    return client, transport


def _queue_commands(transport: RecordingTransport, count: int, *, attached: bool, state_id: str) -> None:
    for _ in range(count):
        document = command_document()
        document["session"] = session_document(context_attached=attached, runtime_state_id=state_id)
        transport.push(200, document)


def test_combat_order_is_cue_then_intensity_then_context() -> None:
    client, transport = _client(True)
    _queue_commands(transport, 3, attached=True, state_id="combat_state")
    recipe = load_phase_recipe(FIXTURES / "game-phases.v1.json")
    apply_phase(client, recipe, "combat")
    paths = [call["url"].rsplit("/", 1)[-1] for call in transport.calls if call["method"] == "POST"]
    assert paths[1:] == ["event", "intensity", "context"]
    cue = json.loads(transport.calls[1]["body"])
    intensity = json.loads(transport.calls[2]["body"])
    context = json.loads(transport.calls[3]["body"])
    assert cue["kind"] == "cue"
    assert cue["name"] == "combat"
    assert intensity["intensity"] == 0.9
    assert context["values"] == {"threat": 0.95}


def test_false_context_attached_skips_context(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    caplog.set_level(logging.INFO, logger="mukit_adaptive")
    client, transport = _client(False)
    _queue_commands(transport, 2, attached=False, state_id="explore")
    recipe = load_phase_recipe(FIXTURES / "game-phases.v1.json")
    apply_phase(client, recipe, "exploration")
    assert not any(call["url"].endswith("/context") for call in transport.calls)
    assert "context_skipped" in caplog.text
    assert "phase=exploration" in caplog.text
    assert "0.1" not in caplog.text


def test_stinger_runs_after_state() -> None:
    client, transport = _client(True)
    _queue_commands(transport, 4, attached=True, state_id="explore")
    recipe = load_phase_recipe(FIXTURES / "game-phases.v1.json")
    recipe["phases"]["exploration"] = {
        "state_id": "explore",
        "stinger_id": "hit",
        "intensity": 0.2,
        "threat": 0.1,
    }
    apply_phase(client, recipe, "exploration")
    bodies = [json.loads(call["body"]) for call in transport.calls if call["method"] == "POST"]
    assert bodies[1]["to_state_id"] == "explore"
    assert bodies[2]["kind"] == "stinger"
    assert bodies[2]["stinger_id"] == "hit"
    assert bodies[3]["intensity"] == 0.2
    assert bodies[4]["values"]["threat"] == 0.1


def test_package_source_does_not_import_studio() -> None:
    assert_package_has_no_studio_import()
