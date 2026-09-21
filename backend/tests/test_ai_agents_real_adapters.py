"""Coverage for real spine agent adapters (non-fake service wrappers)."""

from __future__ import annotations

import asyncio
import os

import pytest

from app.ai_agents import registry as agent_registry
from app.ai_agents.agents import build_all_real_agents, build_real_agent
from app.ai_agents.agents.arrangement import ArrangementAgent
from app.ai_agents.agents.creative_director import CreativeDirectorAgent
from app.ai_agents.agents.critic import CriticAgent
from app.ai_agents.agents.harmony import HarmonyAgent
from app.ai_agents.agents.melody_motif import MelodyMotifAgent
from app.ai_agents.bootstrap import bootstrap_agents
from app.ai_agents.schemas import AgentOperation, AgentRunRequest, CritiqueRecommendation
from app.ai_agents.workflow import build_initial_context, run_spine_workflow
from app.ai_runtime import registry as model_registry
from app.composition_schemas import CompositionV2
from app.services.composition_edit_fingerprint import composition_edit_fingerprint
from tests.test_composition_reharmonization import _sixteen_bar_composition
from tests.test_composition_v2_schema import minimal_v2


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.setenv("PROJECT_DB_PATH", str(tmp_path / "ai-agents-real.db"))
    agent_registry.clear_registry_for_tests()
    model_registry.clear_registry_for_tests()
    yield
    agent_registry.clear_registry_for_tests()
    model_registry.clear_registry_for_tests()


def test_bootstrap_real_adapters_when_fake_mode_off(monkeypatch):
    model_registry.reload_registry(dict(os.environ))
    monkeypatch.setenv("LLM_FAKE_MODE", "0")
    agent_registry.clear_registry_for_tests()
    count = bootstrap_agents(env=dict(os.environ))
    assert count == 9
    assert isinstance(agent_registry.get_agent("harmony"), HarmonyAgent)
    assert isinstance(agent_registry.get_agent("critic"), CriticAgent)
    assert isinstance(agent_registry.get_agent("creative_director"), CreativeDirectorAgent)
    assert isinstance(agent_registry.get_agent("melody_motif"), MelodyMotifAgent)
    assert isinstance(agent_registry.get_agent("arrangement"), ArrangementAgent)


def test_real_harmony_wraps_reharmonization():
    composition = _sixteen_bar_composition()
    agent = build_real_agent("harmony", env=dict(os.environ))
    assert isinstance(agent, HarmonyAgent)
    ctx = build_initial_context(composition)
    result = asyncio.run(
        agent.run(
            AgentRunRequest(
                agent_id="harmony",
                operation=AgentOperation.PROPOSE,
                context=ctx,
                selection={
                    "start_bar": 9,
                    "end_bar": 12,
                    "target_track_ids": ["bass", "accomp"],
                },
            )
        )
    )
    assert result.working_draft_update is not None
    assert result.artifacts[0].content_type == "reharmonize.candidate"
    assert result.provenance_stage.get("agent_id") == "harmony"
    src_mel = next(t for t in composition.tracks if t.id == "melody").events
    out_mel = next(t for t in result.working_draft_update.tracks if t.id == "melody").events
    assert [e.model_dump(mode="json") for e in out_mel] == [
        e.model_dump(mode="json") for e in src_mel
    ]


def test_real_critic_uses_analysis():
    composition = CompositionV2.model_validate(
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
                            "id": "n1",
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
    agent = build_real_agent("critic", env=dict(os.environ))
    ctx = build_initial_context(composition)
    result = asyncio.run(
        agent.run(
            AgentRunRequest(
                agent_id="critic",
                operation=AgentOperation.CRITIQUE,
                context=ctx,
            )
        )
    )
    assert result.recommendation == CritiqueRecommendation.APPROVE
    kinds = {a.kind.value for a in result.artifacts}
    assert "critique" in kinds
    assert "analysis" in kinds


def test_real_arrangement_retain_path():
    composition = _sixteen_bar_composition()
    agent = build_real_agent("arrangement", env=dict(os.environ))
    ctx = build_initial_context(composition)
    result = asyncio.run(
        agent.run(
            AgentRunRequest(
                agent_id="arrangement",
                operation=AgentOperation.PROPOSE,
                context=ctx,
            )
        )
    )
    assert result.artifacts[0].content_type == "arrangement.candidate"
    assert result.working_draft_update is not None
    assert result.working_draft_update.schema_version == "composition.v2"


def test_real_spine_workflow_with_reharm_composition():
    model_registry.reload_registry(dict(os.environ))
    agent_registry.clear_registry_for_tests()
    for agent in build_all_real_agents(env=dict(os.environ)):
        agent_registry.register_agent(agent, replace=True)

    composition = _sixteen_bar_composition()
    source_fp = composition_edit_fingerprint(composition)
    result = asyncio.run(
        run_spine_workflow(
            composition,
            max_revisions=0,
            selection={
                "start_bar": 9,
                "end_bar": 12,
                "target_track_ids": ["bass", "accomp"],
            },
        )
    )
    assert result.candidate is not None
    assert result.candidate.schema_version == "composition.v2"
    assert result.agent_sequence == [
        "creative_director",
        "harmony",
        "melody_motif",
        "arrangement",
        "critic",
    ]
    assert any(s.get("agent_id") == "harmony" for s in result.stages)
    assert result.source_fingerprint == source_fp
    assert result.mutates_composition is False
