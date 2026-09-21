"""Tests for multi-agent spine workflow orchestrator."""

from __future__ import annotations

import asyncio

import pytest

from app.ai_agents import registry as agent_registry
from app.ai_agents.spine import SPINE_AGENT_IDS
from app.ai_agents.workflow import run_spine_workflow
from app.ai_runtime import registry as model_registry
from app.composition_schemas import CompositionV2
from tests.test_composition_v2_schema import minimal_v2


@pytest.fixture(autouse=True)
def _clear_registries(monkeypatch):
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


def test_spine_workflow_order_and_artifacts():
    result = asyncio.run(run_spine_workflow(_source(), max_revisions=0))
    assert result.mutates_composition is False
    assert result.agent_sequence == list(SPINE_AGENT_IDS)
    assert len(result.artifact_log) >= 5
    assert result.recommendation is not None
    assert result.recommendation.value == "approve"
    assert result.candidate.schema_version == "composition.v2"
    assert result.candidate_fingerprint
    assert all(stage.get("agent_id") for stage in result.stages)
    assert [s["agent_id"] for s in result.stages] == list(SPINE_AGENT_IDS)
    # Context remains immutable snapshots — source unchanged vs original identity fields.
    assert result.context.source_composition.tempo == 100
    # Harmony fake nudges tempo on working draft.
    assert result.candidate.tempo == 101


def test_workflow_context_immutability_across_slots():
    result = asyncio.run(run_spine_workflow(_source()))
    ctx = result.context
    assert ctx.brief is not None
    assert ctx.harmony_artifact is not None
    assert ctx.melody_artifact is not None
    assert ctx.arrangement_candidate is not None
    assert ctx.critique is not None
    prior_log_len = len(ctx.artifact_log)
    # with_slot returns new instance
    again = ctx.with_slot("brief", ctx.brief)
    assert again is not ctx
    assert len(ctx.artifact_log) == prior_log_len
