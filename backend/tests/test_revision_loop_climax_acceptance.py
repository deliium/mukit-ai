"""Weak-climax dual-revision acceptance (test-hook FakeCritic; product stylistic policy intact)."""

from __future__ import annotations

import asyncio

import pytest

from app.ai_agents import registry as agent_registry
from app.ai_agents.revision_loop import run_revision_loop
from app.ai_agents.revision_loop_schemas import RevisionMode, RevisionStopReason
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


def _weak_climax_source() -> CompositionV2:
    """Two-section-ish material; climax bars 5–8 get a hard twin for revise AC.

    Product policy: stylistic climax alone does not force revise. This AC injects
    ``fake_revise_hard_finding`` alongside climax finding emission.
    """
    return CompositionV2.model_validate(
        minimal_v2(
            bar_count=8,
            duration_ticks=15360,
            sections=[
                {
                    "type": "verse",
                    "start_bar": 1,
                    "bar_count": 4,
                    "start_tick": 0,
                    "duration_ticks": 7680,
                },
                {
                    "type": "chorus",
                    "start_bar": 5,
                    "bar_count": 4,
                    "start_tick": 7680,
                    "duration_ticks": 7680,
                },
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
                            "velocity": 70,
                        },
                        {
                            "pitch": "E4",
                            "start_tick": 480,
                            "duration_ticks": 480,
                            "velocity": 70,
                        },
                        {
                            "pitch": "G4",
                            "start_tick": 7680,
                            "duration_ticks": 480,
                            "velocity": 72,
                        },
                        {
                            "pitch": "A4",
                            "start_tick": 8160,
                            "duration_ticks": 480,
                            "velocity": 72,
                        },
                    ],
                }
            ],
        )
    )


def test_weak_climax_dual_revision_history_inspectable():
    source = _weak_climax_source()
    outside_before = events_outside_targets_fingerprint(
        source,
        affected_ranges=[{"start_bar": 5, "end_bar": 8}],
        affected_tracks=["piano-1"],
    )
    result = asyncio.run(
        run_revision_loop(
            source,
            revision_mode=RevisionMode.BALANCED,
            critic_parameters={
                "fake_revise_passes": 2,
                "fake_revise_hard_finding": True,
                "fake_emit_climax_finding": True,
            },
            raise_on_revise_exhausted=False,
        )
    )
    assert len(result.revision_history) == 3
    assert [r.pass_index for r in result.revision_history] == [0, 1, 2]
    assert result.stop_reason == RevisionStopReason.CRITIC_APPROVE
    outside_after = events_outside_targets_fingerprint(
        result.candidate,
        affected_ranges=[{"start_bar": 5, "end_bar": 8}],
        affected_tracks=["piano-1"],
    )
    # FakeHarmony only nudges tempo — note events outside climax ranges stay intact.
    assert outside_before == outside_after
    for rec in result.revision_history:
        assert rec.candidate_fingerprint
        assert len(rec.candidate_fingerprint) >= 16
