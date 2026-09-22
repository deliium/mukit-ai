"""Failure / rollback tests for revision loop last_valid semantics."""

from __future__ import annotations

import asyncio
from unittest import mock

import pytest

from app.ai_agents import registry as agent_registry
from app.ai_agents.revision_loop import run_revision_loop
from app.ai_agents.revision_loop_schemas import RevisionMode, RevisionStopReason
from app.ai_runtime import registry as model_registry
from app.composition_schemas import CompositionV2
from tests.test_composition_v2_schema import minimal_v2


@pytest.fixture(autouse=True)
def _registries(monkeypatch):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    agent_registry.clear_registry_for_tests()
    model_registry.clear_registry_for_tests()
    env = dict(**{k: v for k, v in __import__("os").environ.items()})
    model_registry.reload_registry(env)
    agent_registry.reload_agent_registry(env)
    yield
    agent_registry.clear_registry_for_tests()
    model_registry.clear_registry_for_tests()


def _source() -> CompositionV2:
    return CompositionV2.model_validate(
        minimal_v2(
            tracks=[
                {
                    "id": "piano-1",
                    "name": "Piano",
                    "instrument": "piano",
                    "role": "melody",
                    "midi_program": 0,
                    "channel": 1,
                    "events": [
                        {
                            "pitch": "C4",
                            "start_tick": 0,
                            "duration_ticks": 480,
                            "velocity": 80,
                        }
                    ],
                }
            ]
        )
    )


def test_validation_failure_keeps_last_valid_candidate():
    calls = {"n": 0}
    real_validate = __import__(
        "app.services.composition_validator", fromlist=["validate_composition_integrity"]
    ).validate_composition_integrity

    def flaky_validate(composition, profile="canonical"):
        calls["n"] += 1
        # Fail on first revise-pass validation (after pass 0 succeeded).
        if calls["n"] >= 2:
            class _Bad:
                ok = False
                errors = ["forced"]

            return _Bad()
        return real_validate(composition, profile=profile)

    with mock.patch(
        "app.ai_agents.revision_loop.validate_composition_integrity",
        side_effect=flaky_validate,
    ):
        result = asyncio.run(
            run_revision_loop(
                _source(),
                revision_mode=RevisionMode.FAST,
                critic_parameters={
                    "fake_revise_passes": 1,
                    "fake_revise_hard_finding": True,
                },
                raise_on_revise_exhausted=False,
            )
        )
    assert result.stop_reason == RevisionStopReason.VALIDATION_FAILED_KEPT_LAST_VALID
    assert result.candidate_fingerprint == result.last_valid_fingerprint
    # Candidate must still validate.
    report = real_validate(result.candidate, profile="canonical")
    assert report.ok
