"""Parse the shared adaptive client fixtures with the public engine models.

The test reads JSON only. It does not import a client package and it does not
log fixture payloads.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.adaptive_engine_schemas import (
    AdaptiveEngineAckV1,
    AdaptiveEngineCommandResultV1,
    AdaptiveEngineCueV1,
    AdaptiveEngineErrorV1,
    AdaptiveEngineSessionV1,
)
from app.adaptive_musical_context_schemas import parse_adaptive_context_mapping

_FIXTURES = Path(__file__).resolve().parents[2] / "clients" / "fixtures"
_FORBIDDEN_SESSION_KEYS = ("events", "prompt", "model_id", "composition", "api_key")


def _load(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def test_session_fixture_matches_public_model() -> None:
    raw = _load("session.v1.json")
    session = AdaptiveEngineSessionV1.model_validate(raw)
    dumped = session.model_dump(mode="json")
    for key in _FORBIDDEN_SESSION_KEYS:
        assert key not in raw
        assert key not in dumped
    assert session.session_id == "aeng_0123abcd"
    assert session.context_attached is True
    assert session.intensity == 0.2


def test_command_result_ack_and_error_fixtures() -> None:
    command = AdaptiveEngineCommandResultV1.model_validate(_load("command-result.v1.json"))
    assert command.disposition == "committed"
    assert command.request_id == "req_now1"
    assert command.coalesced is False
    assert command.applied is True
    ack = AdaptiveEngineAckV1.model_validate(_load("ack.v1.json"))
    assert ack.schema_version == "adaptive.engine.ack.v1"
    assert ack.kind == "state"
    assert ack.disposition == "finished"
    error = AdaptiveEngineErrorV1.model_validate(_load("error.v1.json"))
    assert error.code == "engine_session_missing"


def test_phase_recipe_start_uses_engine_cue_and_mapping_parsers() -> None:
    recipe = _load("game-phases.v1.json")
    mapping = parse_adaptive_context_mapping(recipe["start"]["mapping"])
    assert mapping.baseline_state_id == "explore"
    cues = [AdaptiveEngineCueV1.model_validate(item) for item in recipe["start"]["cues"]]
    assert [cue.name for cue in cues] == ["combat"]
    assert cues[0].to_state_id == "combat_state"
