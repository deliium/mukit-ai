"""In-memory context session and playback command emission."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from app.adaptive_musical_context_schemas import (
    parse_adaptive_context_external,
    parse_adaptive_context_start,
)
from app.adaptive_score_schemas import AdaptiveTransitionScheduleWarningV1
from app.services import adaptive_musical_context_service as service
from app.services.adaptive_musical_context_runtime import reset_default_context_registry
from app.services.adaptive_playback_runtime import reset_default_playback_registry


def _record(revision: int = 3):
    states = [SimpleNamespace(id="state-exploration"), SimpleNamespace(id="state-combat")]
    return SimpleNamespace(document_revision=revision, score=SimpleNamespace(states=states))


def _start_body(*, target: str = "state-combat", dwell: int = 1) -> dict:
    return {
        "expected_document_revision": 3,
        "mapping": {
            "schema_version": "adaptive.context.mapping.v1",
            "baseline_state_id": "state-exploration",
            "bindings": [
                {
                    "id": "bind-threat",
                    "kind": "numeric",
                    "external_key": "threat_level",
                    "slot": "danger",
                    "transform": "identity",
                    "smooth_alpha": 1,
                }
            ],
            "state_rules": [
                {
                    "id": "rule-combat",
                    "kind": "numeric_band",
                    "slot": "danger",
                    "polarity": "high",
                    "enter": 0.65,
                    "exit": 0.35,
                    "min_dwell_samples": dwell,
                    "target_state_id": target,
                    "priority": 10,
                }
            ],
            "intensity": {"slot": "danger", "emit_epsilon": 0.02},
        },
    }


@pytest.fixture(autouse=True)
def _registries():
    reset_default_context_registry()
    reset_default_playback_registry()
    yield
    reset_default_context_registry()
    reset_default_playback_registry()


def test_playback_warning_codes_survive_in_call_order(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(service, "get_score", lambda *args, **kwargs: _record())
    monkeypatch.setattr(service, "get_default_playback_registry", lambda: SimpleNamespace(get=lambda *_args, **_kwargs: object()))
    calls: list[str] = []

    def fake_command(project_id: str, score_id: str, command, **kwargs):
        calls.append(command.op)
        code = "dangling_state_ref" if command.op == "set_intensity" else "phrase_unavailable"
        return SimpleNamespace(
            warnings=[AdaptiveTransitionScheduleWarningV1(code=code, message="held")]
        )

    monkeypatch.setattr(service, "command_adaptive_playback", fake_command)
    caplog.set_level(logging.INFO)
    service.start_adaptive_musical_context(
        "project-1",
        "score-1",
        parse_adaptive_context_start(_start_body()),
    )
    snapshot = service.sample_adaptive_musical_context(
        "project-1",
        "score-1",
        parse_adaptive_context_external(
            {
                "schema_version": "adaptive.context.external.v1",
                "values": {"threat_level": 0.7},
            }
        ),
    )
    assert calls == ["set_intensity", "request_state"]
    assert [item.code for item in snapshot.warnings] == ["dangling_state_ref", "phrase_unavailable"]
    assert snapshot.musical_state_id == "state-combat"
    assert "body_json" not in caplog.text
    assert "threat_level" not in caplog.text
    info = [record for record in caplog.records if getattr(record, "adaptive_musical_context", None) is True]
    assert info
    assert all(getattr(record, "musical_state_id", None) for record in info)


def test_a_second_start_replaces_only_that_score(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "get_score", lambda *args, **kwargs: _record())
    first = service.start_adaptive_musical_context(
        "project-1",
        "score-a",
        parse_adaptive_context_start(_start_body()),
    )
    other = service.start_adaptive_musical_context(
        "project-1",
        "score-b",
        parse_adaptive_context_start(_start_body()),
    )
    replaced = service.start_adaptive_musical_context(
        "project-1",
        "score-a",
        parse_adaptive_context_start(_start_body()),
    )
    current_a = service.get_adaptive_musical_context("project-1", "score-a")
    current_b = service.get_adaptive_musical_context("project-1", "score-b")
    assert current_a is not None and current_b is not None
    assert current_a.context_id == replaced.context_id
    assert current_a.context_id != first.context_id
    assert current_b.context_id == other.context_id


def test_stale_revision_does_not_step_or_emit(monkeypatch: pytest.MonkeyPatch) -> None:
    revisions = iter((_record(3), _record(4)))
    monkeypatch.setattr(service, "get_score", lambda *args, **kwargs: next(revisions))
    calls: list[str] = []
    monkeypatch.setattr(
        service,
        "get_default_playback_registry",
        lambda: SimpleNamespace(get=lambda *_a, **_k: object()),
    )
    monkeypatch.setattr(
        service,
        "command_adaptive_playback",
        lambda *args, **kwargs: calls.append("called"),
    )
    service.start_adaptive_musical_context(
        "project-1",
        "score-1",
        parse_adaptive_context_start(_start_body()),
    )
    snapshot = service.sample_adaptive_musical_context(
        "project-1",
        "score-1",
        parse_adaptive_context_external(
            {"schema_version": "adaptive.context.external.v1", "values": {"threat_level": 0.7}}
        ),
    )
    assert calls == []
    assert snapshot.sample_index == 0
    assert snapshot.musical_state_id == "state-exploration"
    assert snapshot.telemetry.rejected_sample_count == 1
    assert snapshot.warnings[0].code == "document_revision_conflict"
