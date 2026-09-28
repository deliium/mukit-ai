"""Deterministic adaptive transition boundary tests. No database and no LLM."""

from __future__ import annotations

import inspect
import subprocess
import sys
from pathlib import Path

import pytest

from app.adaptive_score_schemas import (
    AdaptiveBoundaryV1,
    AdaptiveLoopV1,
    AdaptiveScoreError,
    AdaptiveTransitionScheduleRequest,
    parse_adaptive_score,
)
from app.composition_schemas import CompositionV2
from app.services.adaptive_score_transitions import (
    TransitionMarkerProjection,
    TransitionSectionProjection,
    schedule_musical_transition,
)
from app.services.composition_timeline import compile_timeline
from tests.test_composition_v2_schema import minimal_v2


def _timeline(**overrides):
    duration = overrides.get("duration_ticks", 7680)
    bar_count = overrides.get("bar_count", 4)
    sections = overrides.pop(
        "sections",
        [
            {
                "id": "section-explore",
                "type": "intro",
                "start_bar": 1,
                "bar_count": bar_count,
                "start_tick": 0,
                "duration_ticks": duration,
            }
        ],
    )
    payload = {
        "tempo": 120,
        "bar_count": bar_count,
        "duration_ticks": duration,
        "sections": sections,
    }
    payload.update(overrides)
    data = minimal_v2(**payload)
    return compile_timeline(CompositionV2.model_validate(data))


def _score(quantization: str = "bar", **transition):
    edge = {
        "id": "to-combat",
        "from_state_id": "state-exploration",
        "to_state_id": "state-combat",
        "quantization": quantization,
        "conditions": [{"kind": "manual"}],
    }
    edge.update(transition)
    return parse_adaptive_score(
        {
            "schema_version": "adaptive.score.v1",
            "name": "Main cue",
            "initial_state_id": "state-exploration",
            "default_state_id": "state-exploration",
            "states": [
                {
                    "id": "state-exploration",
                    "name": "Exploration",
                    "intensity": 0.2,
                    "material": {"kind": "bar_range", "start_bar": 1, "end_bar": 4},
                    "transition_ids": ["to-combat"],
                    "exit": {"kind": "material_end"},
                },
                {
                    "id": "state-combat",
                    "name": "Combat",
                    "intensity": 0.8,
                    "material": {"kind": "bar_range", "start_bar": 1, "end_bar": 2},
                    "transition_ids": [],
                },
            ],
            "transitions": [edge],
        }
    )


def _request(position_tick: int, **overrides) -> AdaptiveTransitionScheduleRequest:
    payload = {
        "expected_document_revision": 1,
        "from_state_id": "state-exploration",
        "to_state_id": "state-combat",
        "position_tick": position_tick,
        "runtime": {"intensity": 0, "flags": {}, "bars_in_state": 0},
    }
    payload.update(overrides)
    return AdaptiveTransitionScheduleRequest.model_validate(payload)


def _schedule(score, timeline, position, sections=(), markers=(), **request):
    return schedule_musical_transition(
        score,
        timeline,
        sections,
        markers,
        _request(position, **request),
    )


def test_bar_and_beat_acceptance_numbers() -> None:
    timeline = _timeline()
    bar = _schedule(_score("bar"), timeline, 2400)
    assert (bar.boundary_tick, bar.latency_ticks, bar.latency_ms) == (3840, 1440, 1500)
    on_bar = _schedule(_score("bar"), timeline, 1920)
    assert (on_bar.boundary_tick, on_bar.latency_ticks, on_bar.latency_ms) == (1920, 0, 0)
    beat = _schedule(_score("beat"), timeline, 100)
    assert (beat.boundary_tick, beat.latency_ticks, beat.latency_ms) == (480, 380, 396)
    repeated = [_schedule(_score("bar"), timeline, 2400) for _ in range(3)]
    assert [item.boundary_tick for item in repeated] == [3840, 3840, 3840]
    assert [item.latency_ms for item in repeated] == [1500, 1500, 1500]


def test_seven_eight_beat_and_bar() -> None:
    timeline = _timeline(
        time_signature="7/8",
        bar_count=4,
        duration_ticks=6720,
        sections=[
            {
                "id": "section-explore",
                "type": "intro",
                "start_bar": 1,
                "bar_count": 4,
                "start_tick": 0,
                "duration_ticks": 6720,
            }
        ],
    )
    beat = _schedule(_score("beat"), timeline, 100)
    bar = _schedule(_score("bar"), timeline, 100)
    assert beat.boundary_tick == 240
    assert bar.boundary_tick == 1680


def test_tempo_change_at_boundary_keeps_the_tick() -> None:
    timeline = _timeline(tempo_changes=[{"tick": 3840, "bpm": 60}])
    plan = _schedule(_score("bar"), timeline, 2400)
    assert plan.boundary_tick == 3840
    assert plan.latency_ms == 1500
    assert plan.tempo_bpm == 60


def test_custom_from_before_the_anchor_lands_on_the_anchor() -> None:
    score = _score(
        "custom",
        custom_grid_bars=2,
    )
    score = parse_adaptive_score(
        score.model_copy(
            update={
                "states": [
                    score.states[0].model_copy(
                        update={
                            "material": score.states[0].material.model_copy(
                                update={"start_bar": 3, "end_bar": 4}
                            )
                        }
                    ),
                    score.states[1],
                ]
            }
        ).model_dump(mode="json")
    )
    plan = _schedule(score, _timeline(), 100)
    assert plan.boundary_tick == 3840


def test_beat_at_duration_returns_that_tick() -> None:
    timeline = _timeline()
    plan = _schedule(_score("beat"), timeline, timeline.duration_ticks)
    assert plan.boundary_tick == timeline.duration_ticks
    assert plan.latency_ticks == 0


def test_bar_edge_from_mid_bar_stays_on_a_bar_line() -> None:
    plan = _schedule(_score("bar"), _timeline(), 2400)
    assert plan.boundary_tick == 3840
    assert plan.aligned is True


def test_off_grid_cue_is_skipped_and_tick_exit_is_rejected() -> None:
    timeline = _timeline()
    score = _score("cue", cue_label="Hit")
    markers = (
        TransitionMarkerProjection(kind="rehearsal", label="Hit", tick=100),
        TransitionMarkerProjection(kind="text", label="Hit", tick=480),
        TransitionMarkerProjection(kind="rehearsal", label="Hit", tick=960),
    )
    plan = _schedule(score, timeline, 0, markers=markers)
    assert plan.boundary_tick == 960
    exit_score = parse_adaptive_score(
        _score("next_exit").model_copy(
            update={
                "states": [
                    _score().states[0].model_copy(
                        update={"exit": AdaptiveBoundaryV1(kind="tick", tick=100)}
                    ),
                    _score().states[1],
                ]
            }
        ).model_dump(mode="json")
    )
    with pytest.raises(AdaptiveScoreError) as captured:
        _schedule(exit_score, timeline, 0)
    assert captured.value.code == "exit_off_grid"


def test_position_past_loop_end_is_rejected() -> None:
    score = parse_adaptive_score(
        _score("loop_end").model_copy(
            update={
                "states": [
                    _score().states[0].model_copy(
                        update={"loop": AdaptiveLoopV1(enabled=True, start_bar=1, end_bar=1)}
                    ),
                    _score().states[1],
                ]
            }
        ).model_dump(mode="json")
    )
    with pytest.raises(AdaptiveScoreError) as captured:
        _schedule(score, _timeline(), 1921)
    assert captured.value.code == "loop_end_passed"


def test_resolver_does_not_import_an_llm() -> None:
    source = inspect.getsource(sys.modules["app.services.adaptive_score_transitions"])
    assert "app.ai_runtime" not in source
    assert "fake_llm" not in source
    assert "fastapi" not in source
    assert "sqlite" not in source
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import app.services.adaptive_score_transitions; "
                "assert 'app.ai_runtime' not in sys.modules; "
                "assert 'app.services.fake_llm' not in sys.modules"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert completed.returncode == 0, completed.stderr


def test_crossfade_keeps_the_bar_boundary_and_clamps_the_lead() -> None:
    timeline = _timeline()
    short = _score(
        "bar",
        realization={"kind": "crossfade", "crossfade_ms": 500},
    )
    plan = _schedule(short, timeline, 2400)
    assert plan.boundary_tick == 3840
    assert plan.realization.kind == "crossfade"
    assert plan.realization.fade_start_tick < 3840
    long = _score(
        "bar",
        realization={"kind": "crossfade", "crossfade_ms": 4000},
    )
    clamped = _schedule(long, timeline, 2400)
    assert clamped.boundary_tick == 3840
    assert clamped.realization.fade_start_tick == 2400


def test_overlap_on_a_bar_line_and_rejection_on_a_beat() -> None:
    timeline = _timeline()
    overlap = _score("bar", realization={"kind": "overlap", "overlap_bars": 1})
    plan = _schedule(overlap, timeline, 2400)
    assert plan.boundary_tick == 3840
    assert plan.realization.source_release_tick == 5760
    beat = _score("beat", realization={"kind": "overlap", "overlap_bars": 1})
    with pytest.raises(AdaptiveScoreError) as captured:
        _schedule(beat, timeline, 100)
    assert captured.value.code == "realization_invalid"


def test_missing_stinger_returns_no_schedule() -> None:
    score = _score(
        "bar",
        realization={"kind": "stinger", "stinger_id": "missing-stinger"},
    )
    with pytest.raises(AdaptiveScoreError) as captured:
        returned = _schedule(score, _timeline(), 2400)
        assert returned is None
    assert captured.value.code == "realization_invalid"
