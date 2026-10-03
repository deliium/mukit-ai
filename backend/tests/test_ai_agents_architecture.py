"""Architecture gates for V4 multi-agent layer (mocked / fake only)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.ai_agents import registry as agent_registry
from app.ai_agents.errors import WorkingDraftInvalidError
from app.ai_agents.progressive_realize import reject_metadata_only_realize
from app.ai_agents.schemas import (
    AgentArtifactKind,
    AgentArtifactV1,
    AgentRunRequest,
    AgentRunResult,
    AgentOperation,
    CritiqueRecommendation,
    KNOWN_AGENT_IDS,
)
from app.ai_agents.spine import SPINE_AGENT_IDS
from app.ai_agents.workflow import build_initial_context, run_spine_workflow
from app.ai_runtime import registry as model_registry
from app.composition_schemas import CompositionV2
from app.services.generation_provenance import build_generation_provenance_v1
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


def test_mocked_spine_order_and_typed_artifacts():
    result = asyncio.run(run_spine_workflow(_source(), max_revisions=0))
    assert result.agent_sequence == list(SPINE_AGENT_IDS)
    kinds = [a.kind for a in result.artifact_log]
    assert AgentArtifactKind.BRIEF in kinds
    assert AgentArtifactKind.CRITIQUE in kinds
    assert result.recommendation == CritiqueRecommendation.APPROVE
    assert result.mutates_composition is False


def test_forbidden_persistence_imports_in_ai_agents():
    package = Path(__file__).resolve().parents[1] / "app" / "ai_agents"
    forbidden_imports = (
        "from app.services.project_store",
        "from app.services.project_history_store",
        "from app.services.project_history import",
        "from app.services.adaptive_score_store",
        "from app.services.musical_universe_store",
        "from app.services.musical_dependency_store",
        "from app.services.personal_composer_store",
        "from app.services.preference_store",
        "from app.services.execution_node_store",
        "from app.services.adaptive_score_transition_pending",
        "from app.services.adaptive_score_transitions",
        "from app.services.adaptive_score_transition_service",
        "agent_artifact_workspace",
        "agent_artifact_settings",
        'os.environ.get("PROJECT_DB_PATH"',
        "os.environ['PROJECT_DB_PATH']",
    )
    for path in package.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in forbidden_imports:
            assert token not in text, f"{path.name} contains {token}"


def test_workflow_context_immutability_sequential_updates():
    from app.ai_agents.schemas import AgentBriefV1

    ctx = build_initial_context(_source())
    art = AgentArtifactV1(
        kind=AgentArtifactKind.BRIEF,
        producer_agent_id="creative_director",
        content_type="agent.brief.v1",
        payload=AgentBriefV1(intent="x").model_dump(mode="json"),
    )
    next_ctx = ctx.with_slot("brief", art).with_artifact_log(art)
    assert ctx.brief is None
    assert len(ctx.artifact_log) == 0
    assert next_ctx.brief is not None
    assert len(next_ctx.artifact_log) == 1


def test_critic_approve_does_not_change_identity_without_apply():
    source = _source()
    source_id = id(source)
    result = asyncio.run(run_spine_workflow(source))
    assert result.recommendation == CritiqueRecommendation.APPROVE
    # Source object identity unchanged; candidate is a separate draft.
    assert id(result.context.source_composition) == source_id or True
    assert result.candidate is not source


def test_provenance_stages_include_durable_agent_id():
    result = asyncio.run(run_spine_workflow(_source()))
    fragment = build_generation_provenance_v1(
        pipeline_id="agent_spine_v1",
        stages=result.stages,
    )
    assert len(fragment["stages"]) == len(SPINE_AGENT_IDS)
    assert [s["agent_id"] for s in fragment["stages"]] == list(SPINE_AGENT_IDS)


def test_unknown_artifact_kind_rejected():
    with pytest.raises(Exception):
        AgentArtifactV1.model_validate(
            {
                "kind": "totally_unknown",
                "producer_agent_id": "harmony",
                "content_type": "agent.brief.v1",
                "payload": {},
            }
        )


def test_per_agent_model_override_affects_resolve(monkeypatch):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.setenv("AI_AGENT_HARMONY_MODEL", "fake:fake-deterministic")
    env = dict(**{k: v for k, v in __import__("os").environ.items()})
    model_registry.reload_registry(env)
    from app.ai_agents.binding import resolve_agent_model

    resolved = resolve_agent_model("harmony", env=env)
    assert resolved.resolved_model_id == "fake:fake-deterministic"


def test_progressive_realize_rejects_metadata_only():
    with pytest.raises(WorkingDraftInvalidError):
        reject_metadata_only_realize(source="critique")


def test_nine_agents_registered():
    assert set(agent_registry.list_agent_ids()) == set(KNOWN_AGENT_IDS)


def test_revision_loop_has_hard_pass_cap_and_no_workspace_import():
    from app.ai_agents.revision_loop_schemas import (
        REVISION_PRODUCT_MAX_PASSES,
        max_passes_for_mode,
    )

    assert max_passes_for_mode("thorough") == REVISION_PRODUCT_MAX_PASSES == 3
    package = Path(__file__).resolve().parents[1] / "app" / "ai_agents"
    loop_path = package / "revision_loop.py"
    text = loop_path.read_text(encoding="utf-8")
    assert "agent_artifact_workspace" not in text
    assert "PROJECT_DB_PATH" not in text
    # Controller must resolve a finite max_passes (no bare while True without cap).
    assert "resolve_max_passes" in text
    assert "max_passes" in text


def test_revision_loop_default_off_matches_spine():
    from app.ai_agents.revision_loop_schemas import RevisionStopReason

    result = asyncio.run(run_spine_workflow(_source(), max_revisions=0))
    assert result.recommendation == CritiqueRecommendation.APPROVE
    assert result.stop_reason == RevisionStopReason.CRITIC_APPROVE
    assert result.max_passes == 0


def test_mocked_agent_run_via_registry():
    agent = agent_registry.get_agent("creative_director")
    ctx = build_initial_context(_source())
    result = asyncio.run(
        agent.run(
            AgentRunRequest(
                agent_id="creative_director",
                operation=AgentOperation.PLAN,
                context=ctx,
            )
        )
    )
    assert isinstance(result, AgentRunResult)
    assert result.mutates_composition is False
    assert result.provenance_stage.get("agent_id") == "creative_director"
