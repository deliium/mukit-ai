"""Plan operations emit typed plans and do not realize notes."""

from __future__ import annotations

import asyncio
import os

import pytest

from app.ai_agents.agents import build_real_agent
from app.ai_agents.artifact_schemas import validate_artifact_payload
from app.ai_agents.fake_agents import build_fake_agent
from app.ai_agents.schemas import AGENT_CONTENT_TYPES, AgentOperation, AgentRunRequest
from app.ai_agents.workflow import build_initial_context
from app.composition_schemas import CompositionV2
from app.autonomous_composer_schemas import CreativeBriefV1, NarrativeBeat
from app.services.autonomous_project_plan import compile_project_plan
from tests.test_composition_v2_schema import minimal_v2


def _plan():
    brief = CreativeBriefV1(
        schema_version="creative.brief.v1",
        duration_seconds=150,
        narrative=[
            NarrativeBeat(intent="sparse_opening", text="cold sparse opening"),
            NarrativeBeat(intent="establish_theme", text="introduce Theme A"),
            NarrativeBeat(intent="build", text="increase tension"),
            NarrativeBeat(intent="climax", text="strong climax"),
            NarrativeBeat(intent="resolve", text="quiet transformed ending"),
        ],
        instrumentation=["piano", "cello", "strings"],
        forbidden_instrument_families=["drums"],
        opening_key="F# minor",
        final_section_key="F# major",
    )
    return compile_project_plan(brief)


def _request(agent_id: str, operation: AgentOperation, selection: dict | None = None) -> AgentRunRequest:
    return AgentRunRequest(
        agent_id=agent_id,
        operation=operation,
        context=build_initial_context(CompositionV2.model_validate(minimal_v2())),
        selection=selection or {},
    )


@pytest.mark.parametrize("factory", [build_real_agent, build_fake_agent])
def test_plan_operations_do_not_realize(factory) -> None:
    compiled = _plan()
    selection = {"compiled_project_plan": compiled.model_dump(mode="json")}
    env = dict(os.environ)
    director = asyncio.run(
        factory("creative_director", env=env).run(
            _request("creative_director", AgentOperation.PLAN, selection)
        )
    )
    harmony = asyncio.run(
        factory("harmony", env=env).run(_request("harmony", AgentOperation.PLAN, selection))
    )
    motif = asyncio.run(
        factory("melody_motif", env=env).run(
            _request("melody_motif", AgentOperation.PLAN, selection)
        )
    )
    assert director.working_draft_update is None
    assert harmony.working_draft_update is None
    assert motif.working_draft_update is None
    assert director.artifacts[0].content_type == "project.plan.v1"
    assert director.artifacts[0].kind.value == "plan"
    assert "project.plan.v1" in AGENT_CONTENT_TYPES
    stored = validate_artifact_payload("project.plan.v1", director.artifacts[0].payload)
    assert [stage["stage_id"] for stage in stored["stages"]] == [
        "plan",
        "harmony_plan",
        "motif_plan",
        "symbolic",
        "critique",
        "revision",
        "arrangement",
        "expression",
        "render",
    ]
    harmony_payload = validate_artifact_payload(
        "agent.harmony_plan.v1", harmony.artifacts[0].payload
    )
    assert harmony_payload["key"] == "F# minor"
    assert len(harmony_payload["chord_events"]) == len(compiled.sections)
    assert [event["bar"] for event in harmony_payload["chord_events"]] == [
        section.start_bar for section in compiled.sections
    ]
    motif_payload = validate_artifact_payload("agent.motif_plan.v1", motif.artifacts[0].payload)
    labels = motif_payload["motifs"][0]["section_labels"]
    assert motif_payload["motifs"][0]["motif_label"] == "Theme A"
    assert "introduce Theme A" in labels


def test_director_without_compiled_plan_keeps_spine_artifacts() -> None:
    result = asyncio.run(
        build_real_agent("creative_director", env=dict(os.environ)).run(
            _request("creative_director", AgentOperation.PLAN)
        )
    )
    types = [artifact.content_type for artifact in result.artifacts]
    assert "agent.workflow_plan.v1" in types
    assert "agent.brief.v1" in types
    assert "project.plan.v1" not in types


def test_fake_propose_still_updates_draft() -> None:
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
    result = asyncio.run(
        build_fake_agent("harmony", env=dict(os.environ)).run(
            AgentRunRequest(
                agent_id="harmony",
                operation=AgentOperation.PROPOSE,
                context=build_initial_context(composition),
            )
        )
    )
    assert result.working_draft_update is not None
    assert "reharmonize.candidate" in [item.content_type for item in result.artifacts]
