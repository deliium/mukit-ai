"""Inventory / non-regression for Music Evaluation Engine stub (Task 1)."""

from __future__ import annotations

import asyncio

import pytest

from app.ai_agents import registry as agent_registry
from app.ai_agents.schemas import AgentArtifactKind, CritiqueRecommendation
from app.ai_agents.spine import SPINE_AGENT_IDS
from app.ai_agents.workflow import run_spine_workflow
from app.ai_runtime import registry as model_registry
from app.composition_schemas import CompositionV2
from app.services.composition_critique import (
    INVENTORY_CHECKLIST_IDS,
    evaluation_engine_ready,
    inventory_checklist,
)
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


def test_critique_inventory_checklist_frozen():
    ids = inventory_checklist()
    assert ids == INVENTORY_CHECKLIST_IDS
    assert "thin_critic_flat_critique_v1" in ids
    assert "never_mutate_composition_v2" in ids
    assert "subjective_ne_hard_constraint" in ids
    assert "no_composition_v4" in ids
    assert "workspace_critique_promote_already_ships" in ids
    assert evaluation_engine_ready() is True


def test_spine_fake_critic_still_approves_without_engine():
    """Non-regression: FakeCritic approves; mutates_composition stays false."""
    result = asyncio.run(run_spine_workflow(_source(), max_revisions=0))
    assert result.agent_sequence == list(SPINE_AGENT_IDS)
    assert AgentArtifactKind.CRITIQUE in [a.kind for a in result.artifact_log]
    critique = next(a for a in result.artifact_log if a.kind == AgentArtifactKind.CRITIQUE)
    assert critique.content_type == "agent.critique.v1"
    assert critique.mutates_composition is False
    assert result.recommendation == CritiqueRecommendation.APPROVE
    assert result.mutates_composition is False
    payload = critique.payload
    assert payload.get("recommendation") == "approve"
    assert payload.get("schema_version") == "agent.critique.v1"
