"""Inventory / non-regression for agent artifact workspace stub."""

from __future__ import annotations

import asyncio

import pytest

from app.ai_agents import registry as agent_registry
from app.ai_agents.schemas import AgentArtifactKind, CritiqueRecommendation
from app.ai_agents.spine import SPINE_AGENT_IDS
from app.ai_agents.workflow import run_spine_workflow
from app.ai_runtime import registry as model_registry
from app.composition_schemas import CompositionV2
from app.services.agent_artifact_workspace import (
    INVENTORY_CHECKLIST_IDS,
    inventory_checklist,
    workspace_writes_enabled,
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


def test_inventory_checklist_frozen():
    ids = inventory_checklist()
    assert ids == INVENTORY_CHECKLIST_IDS
    assert "no_composition_v4" in ids
    assert "session_only_until_apply_default" in ids
    # Stub phase: durable writes not yet enabled; session artifact_log still works.
    assert workspace_writes_enabled() is False


def test_spine_workflow_artifact_log_without_durable_workspace():
    """Non-regression: session artifact_log works without durable workspace rows."""
    result = asyncio.run(run_spine_workflow(_source(), max_revisions=0))
    assert result.agent_sequence == list(SPINE_AGENT_IDS)
    assert len(result.artifact_log) >= 2
    kinds = [a.kind for a in result.artifact_log]
    assert AgentArtifactKind.BRIEF in kinds
    assert AgentArtifactKind.CRITIQUE in kinds
    assert result.recommendation == CritiqueRecommendation.APPROVE
    assert result.mutates_composition is False
    # Preview path did not need durable promote.
    assert result.artifact_log
