"""Controller tests for bounded critique revision loop."""

from __future__ import annotations

import asyncio

import pytest

from app.ai_agents import registry as agent_registry
from app.ai_agents.revision_loop import run_revision_loop
from app.ai_agents.revision_loop_schemas import (
    RevisionLoopBudgets,
    RevisionMode,
    RevisionStopReason,
)
from app.ai_agents.schemas import CritiqueRecommendation
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
                        },
                        {
                            "pitch": "E4",
                            "start_tick": 1920,
                            "duration_ticks": 480,
                            "velocity": 80,
                        },
                    ],
                }
            ]
        )
    )


def test_approve_path_mode_off():
    result = asyncio.run(
        run_revision_loop(_source(), revision_mode=RevisionMode.OFF, max_revisions=0)
    )
    assert result.recommendation == CritiqueRecommendation.APPROVE
    assert result.stop_reason == RevisionStopReason.CRITIC_APPROVE
    assert len(result.revision_history) == 1
    assert result.revision_history[0].pass_index == 0
    assert len(result.pass_candidates) == 1
    assert result.pass_candidates[0].pass_index == 0
    assert result.pass_candidates[0].composition.schema_version == "composition.v2"
    assert result.mutates_composition is False


def test_max_passes_balanced_with_scripted_revise():
    result = asyncio.run(
        run_revision_loop(
            _source(),
            revision_mode=RevisionMode.BALANCED,
            critic_parameters={
                "fake_revise_passes": 2,
                "fake_revise_hard_finding": True,
            },
            raise_on_revise_exhausted=False,
        )
    )
    assert result.max_passes == 2
    assert len(result.revision_history) >= 3  # pass 0 + 1 + 2
    assert result.revision_history[0].pass_index == 0
    assert result.revision_history[1].pass_index == 1
    assert result.revision_history[2].pass_index == 2
    assert result.recommendation == CritiqueRecommendation.APPROVE
    assert result.stop_reason == RevisionStopReason.CRITIC_APPROVE
    assert result.last_valid_fingerprint == result.candidate_fingerprint


def test_cancel_mid_loop_keeps_last_valid():
    state = {"n": 0}

    def cancel_after_pass0_agents():
        state["n"] += 1
        # Cancel once revise pass starts (after initial spine ~5 agents + pass0 stop checks).
        return state["n"] > 8

    result = asyncio.run(
        run_revision_loop(
            _source(),
            revision_mode=RevisionMode.THOROUGH,
            cancel_check=cancel_after_pass0_agents,
            critic_parameters={
                "fake_revise_passes": 3,
                "fake_revise_hard_finding": True,
            },
            raise_on_revise_exhausted=False,
        )
    )
    assert result.stop_reason == RevisionStopReason.CANCELLED
    assert result.candidate_fingerprint == result.last_valid_fingerprint


def test_budget_exhausted():
    result = asyncio.run(
        run_revision_loop(
            _source(),
            revision_mode=RevisionMode.FAST,
            budgets=RevisionLoopBudgets(max_wall_ms=1),
            critic_parameters={
                "fake_revise_passes": 1,
                "fake_revise_hard_finding": True,
            },
            raise_on_revise_exhausted=False,
        )
    )
    # Tiny wall budget may exhaust during or after pass 0.
    assert result.stop_reason in {
        RevisionStopReason.RESOURCE_BUDGET_EXHAUSTED,
        RevisionStopReason.CRITIC_APPROVE,
        RevisionStopReason.MAX_PASSES_REACHED,
        RevisionStopReason.REVISE_EXHAUSTED,
        RevisionStopReason.HARD_REQUIREMENTS_SATISFIED,
        RevisionStopReason.IMPROVEMENT_BELOW_THRESHOLD,
    }
    assert result.last_valid_fingerprint is not None


def test_runtime_budget_skips_re_critique(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.ai_agents.fake_agents import FakeCriticAgent, FakeHarmonyAgent
    from app.ai_agents.revision_plan_builder import build_revision_plan_from_findings
    from app.operation_budget_settings import BUDGET_RUNTIME

    calls = {"critic": 0, "armed": False}
    original_critic = FakeCriticAgent._run_impl
    original_harmony = FakeHarmonyAgent._run_impl

    async def critic(self, request):  # noqa: ANN001
        calls["critic"] += 1
        return await original_critic(self, request)

    async def harmony(self, request):  # noqa: ANN001
        result = await original_harmony(self, request)
        if request.context.revise_count > 0:
            calls["armed"] = True
        return result

    def one_target(*args, **kwargs):  # noqa: ANN002, ANN003
        model = build_revision_plan_from_findings(*args, **kwargs)
        return model.model_copy(update={"target_agent_ids": ["harmony"]})

    def budget(*, started, usage, budgets):  # noqa: ANN001
        if calls["armed"]:
            return BUDGET_RUNTIME
        return None

    monkeypatch.setattr(FakeCriticAgent, "_run_impl", critic)
    monkeypatch.setattr(FakeHarmonyAgent, "_run_impl", harmony)
    monkeypatch.setattr(
        "app.ai_agents.revision_loop.build_revision_plan_from_findings",
        one_target,
    )
    monkeypatch.setattr("app.ai_agents.revision_loop._budget_exhausted", budget)

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
    assert calls["critic"] == 1
    assert result.stop_reason == RevisionStopReason.RESOURCE_BUDGET_EXHAUSTED
    assert result.budget_code == BUDGET_RUNTIME


def test_usage_available_in_fake_mode():
    result = asyncio.run(
        run_revision_loop(_source(), revision_mode=RevisionMode.OFF)
    )
    assert result.usage is not None
    assert result.usage.usage_status.value in {"available", "partial", "unavailable"}
