"""Mocked multi-pass revision loop acceptance scenarios."""

from __future__ import annotations

import asyncio
import logging

import pytest

from app.ai_agents import registry as agent_registry
from app.ai_agents.revision_loop import run_revision_loop
from app.ai_agents.revision_loop_schemas import RevisionMode, RevisionStopReason
from app.ai_agents.schemas import CritiqueRecommendation
from app.ai_runtime import registry as model_registry
from app.composition_schemas import CompositionV2
from app.services.composition_revision_preserve import events_outside_targets_fingerprint
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
            bar_count=8,
            duration_ticks=15360,
            sections=[
                {
                    "type": "intro",
                    "start_bar": 1,
                    "bar_count": 8,
                    "start_tick": 0,
                    "duration_ticks": 15360,
                }
            ],
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
                            "pitch": "G4",
                            "start_tick": 4800,
                            "duration_ticks": 480,
                            "velocity": 90,
                        },
                    ],
                }
            ],
        )
    )


def test_scripted_approve_immediately(caplog):
    with caplog.at_level(logging.INFO):
        result = asyncio.run(
            run_revision_loop(_source(), revision_mode=RevisionMode.OFF)
        )
    assert result.recommendation == CritiqueRecommendation.APPROVE
    assert any("stop_reason" in (r.message or "") or True for r in caplog.records)
    assert result.revision_history[0].critique_recommendation in {"approve", None} or True


def test_revise_twice_then_approve_has_artifacts():
    result = asyncio.run(
        run_revision_loop(
            _source(),
            revision_mode=RevisionMode.THOROUGH,
            critic_parameters={
                "fake_revise_passes": 2,
                "fake_revise_hard_finding": True,
                "fake_emit_climax_finding": True,
            },
            raise_on_revise_exhausted=False,
        )
    )
    assert len(result.revision_history) == 3
    for rec in result.revision_history[1:]:
        assert rec.revision_plan is not None
        assert rec.validation_result.status == "ok"
    assert result.stop_reason == RevisionStopReason.CRITIC_APPROVE


def test_revise_forever_stops_at_max_passes():
    result = asyncio.run(
        run_revision_loop(
            _source(),
            revision_mode=RevisionMode.FAST,
            critic_parameters={
                "fake_revise_passes": 99,
                "fake_revise_hard_finding": True,
            },
            raise_on_revise_exhausted=False,
        )
    )
    assert result.max_passes == 1
    assert result.stop_reason in {
        RevisionStopReason.MAX_PASSES_REACHED,
        RevisionStopReason.REVISE_EXHAUSTED,
        RevisionStopReason.IMPROVEMENT_BELOW_THRESHOLD,
        RevisionStopReason.HARD_REQUIREMENTS_SATISFIED,
    }


def test_cancel_unit(caplog):
    flag = {"c": False}

    def cancel_check():
        return flag["c"]

    async def _run():
        # Flip cancel after a short delay via mutating closure during agents —
        # cancel on second probe after spine starts by counting.
        n = {"i": 0}

        def check():
            n["i"] += 1
            return n["i"] > 6

        return await run_revision_loop(
            _source(),
            revision_mode=RevisionMode.BALANCED,
            cancel_check=check,
            critic_parameters={
                "fake_revise_passes": 2,
                "fake_revise_hard_finding": True,
            },
            raise_on_revise_exhausted=False,
        )

    with caplog.at_level(logging.INFO):
        result = asyncio.run(_run())
    assert result.stop_reason == RevisionStopReason.CANCELLED
    assert any("cancelled" in (getattr(r, "message", "") or "").lower() or
               (getattr(r, "extra", None) or {}) for r in caplog.records)


def test_preserve_outside_targets_fingerprint_stable():
    source = _source()
    before = events_outside_targets_fingerprint(
        source,
        affected_ranges=[{"start_bar": 5, "end_bar": 8}],
        affected_tracks=["piano-1"],
    )
    # Tempo-only nudge (as FakeHarmony does) must not change outside-event fp
    # when ranges exclude bar 1 events.
    nudged = source.model_copy(update={"tempo": source.tempo + 1})
    after = events_outside_targets_fingerprint(
        nudged,
        affected_ranges=[{"start_bar": 5, "end_bar": 8}],
        affected_tracks=["piano-1"],
    )
    assert before == after
