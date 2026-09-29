"""Pure clock for the Exploration → Suspense → Combat → Victory scenario."""

from __future__ import annotations

import logging

import pytest

from app.adaptive_playback_schemas import parse_adaptive_playback_command
from app.adaptive_score_schemas import AdaptiveScoreV1
from app.services.adaptive_playback import (
    PlaybackInputs,
    begin_adaptive_playback,
    step_adaptive_playback,
)
from app.services.adaptive_score_layers import LayerProjection
from app.services.adaptive_score_transitions import (
    TransitionMarkerProjection,
    TransitionSectionProjection,
)
from app.services.composition_timeline import CompiledTimeline

_PLAYBACK = "pbr_0123abcd"


def _timeline() -> CompiledTimeline:
    return CompiledTimeline(
        ticks_per_quarter=480,
        duration_ticks=30720,
        bar_count=16,
        bar_boundaries=tuple(index * 1920 for index in range(17)),
        root_tempo=120,
        root_time_signature="4/4",
        root_key="C major",
        tempo_changes=(),
        time_signature_changes=(),
        key_changes=(),
    )


def _sections() -> tuple[TransitionSectionProjection, ...]:
    rows = (
        ("section-exploration", 1, 4, 0),
        ("section-suspense", 5, 4, 7680),
        ("section-combat", 9, 4, 15360),
        ("section-victory", 13, 4, 23040),
    )
    return tuple(
        TransitionSectionProjection(
            section_id=section_id,
            start_bar=start_bar,
            bar_count=bar_count,
            start_tick=start_tick,
        )
        for section_id, start_bar, bar_count, start_tick in rows
    )


def scenario_score() -> dict:
    """Locked exploration_suspense_combat_victory graph. Section beds, track layers."""
    layers = [
        ("layer-pad", "ambient", "track-pad", 0, 1, None, 0, "cut", "cut", 0, 0),
        ("layer-piano", "harmony", "track-piano", 0, 1, None, 0, "cut", "cut", 0, 0),
        ("layer-bass", "bass", "track-bass", 0.5, 1, None, 0, "linear", "linear", 400, 400),
        ("layer-strings", "strings", "track-strings", 0.5, 1, None, 0, "linear", "linear", 400, 400),
        ("layer-perc", "percussion", "track-perc", 0.8, 1, None, 0, "cut", "cut", 0, 0),
        ("layer-brass-hint", "brass", "track-brass", 0.9, 1, "orchestration", 0, "bar", "bar", 0, 0),
        ("layer-orch", "brass", "track-orch", 1, 1, "orchestration", 1, "bar", "bar", 0, 0),
    ]
    return {
        "schema_version": "adaptive.score.v1",
        "name": "exploration_suspense_combat_victory",
        "initial_state_id": "state-exploration",
        "default_state_id": "state-exploration",
        "states": [
            {
                "id": "state-exploration",
                "name": "Exploration",
                "intensity": 0.2,
                "material": {"kind": "section", "section_id": "section-exploration"},
                "loop": {"enabled": True, "start_bar": 1, "end_bar": 4},
                "transition_ids": ["tr-explore-suspense"],
            },
            {
                "id": "state-suspense",
                "name": "Suspense",
                "intensity": 0.4,
                "material": {"kind": "section", "section_id": "section-suspense"},
                "transition_ids": ["tr-suspense-combat"],
            },
            {
                "id": "state-combat",
                "name": "Combat",
                "intensity": 0.8,
                "material": {"kind": "section", "section_id": "section-combat"},
                "loop": {"enabled": True, "start_bar": 9, "end_bar": 12},
                "transition_ids": ["tr-combat-victory"],
            },
            {
                "id": "state-victory",
                "name": "Victory",
                "intensity": 0.6,
                "material": {"kind": "section", "section_id": "section-victory"},
                "transition_ids": [],
            },
        ],
        "transitions": [
            {
                "id": "tr-explore-suspense",
                "from_state_id": "state-exploration",
                "to_state_id": "state-suspense",
                "quantization": "loop_end",
                "priority": 0,
                "conditions": [],
                "realization": {"kind": "cut"},
            },
            {
                "id": "tr-suspense-combat",
                "from_state_id": "state-suspense",
                "to_state_id": "state-combat",
                "quantization": "next_exit",
                "priority": 0,
                "conditions": [],
                "realization": {
                    "kind": "phrase",
                    "phrase_material": {"kind": "bar_range", "start_bar": 9, "end_bar": 9},
                },
            },
            {
                "id": "tr-combat-victory",
                "from_state_id": "state-combat",
                "to_state_id": "state-victory",
                "quantization": "loop_end",
                "priority": 0,
                "conditions": [],
                "realization": {"kind": "stinger", "stinger_id": "stinger-victory"},
            },
        ],
        "layers": [
            {
                "id": layer_id,
                "name": layer_id,
                "state_id": None,
                "role": role,
                "material": {
                    "kind": "track_range",
                    "track_ids": [track_id],
                    "start_bar": 1,
                    "end_bar": 16,
                },
                "intensity_min": low,
                "intensity_max": high,
                "mix_hint": "bed",
                "exclusive_group": group,
                "priority": priority,
                "fade": {
                    "in_policy": in_policy,
                    "out_policy": out_policy,
                    "fade_in_ms": fade_in,
                    "fade_out_ms": fade_out,
                },
            }
            for (
                layer_id,
                role,
                track_id,
                low,
                high,
                group,
                priority,
                in_policy,
                out_policy,
                fade_in,
                fade_out,
            ) in layers
        ],
        "stingers": [
            {
                "id": "stinger-victory",
                "name": "Victory sting",
                "material": {
                    "kind": "track_range",
                    "track_ids": ["track-stinger"],
                    "start_bar": 13,
                    "end_bar": 13,
                },
                "interrupt_policy": "overlay",
                "quantization": "bar",
                "retrigger": "once",
            }
        ],
    }


def _layers() -> tuple[LayerProjection, ...]:
    spans = (0, 30720)
    rows = []
    for layer in scenario_score()["layers"]:
        fade = layer["fade"]
        rows.append(
            LayerProjection(
                id=layer["id"],
                state_id=layer["state_id"],
                role=layer["role"],
                intensity_min=layer["intensity_min"],
                intensity_max=layer["intensity_max"],
                exclusive_group=layer["exclusive_group"],
                priority=layer["priority"],
                in_policy=fade["in_policy"],
                out_policy=fade["out_policy"],
                fade_in_ms=fade["fade_in_ms"],
                fade_out_ms=fade["fade_out_ms"],
                span_start_tick=spans[0],
                span_end_tick=spans[1],
                track_ids=tuple(layer["material"]["track_ids"]),
                material_kind="track_range",
            )
        )
    return tuple(rows)


def _inputs(score: AdaptiveScoreV1 | None = None) -> PlaybackInputs:
    parsed = score or AdaptiveScoreV1.model_validate(scenario_score())
    return PlaybackInputs(
        score=parsed,
        timeline=_timeline(),
        sections=_sections(),
        markers=(),
        layers=_layers() if score is None else (),
    )


def _begin():
    return begin_adaptive_playback(
        _inputs(),
        playback_id=_PLAYBACK,
        mode="simulation",
        document_revision=3,
    )


def _cmd(clock, inputs, body: dict):
    return step_adaptive_playback(inputs, clock, parse_adaptive_playback_command(body))


def _gain(snapshot, track_id: str) -> dict:
    return next(row for row in snapshot.instructions.track_gains if row.track_id == track_id).model_dump()


def test_locked_scenario_ticks() -> None:
    inputs = _inputs()
    clock = _begin()
    snap = clock.snapshot
    assert snap is not None
    assert snap.position_tick == 0
    assert (snap.bar, snap.beat) == (1, 1)
    assert snap.runtime_state_id == "state-exploration"
    assert snap.active_layer_ids == ["layer-pad", "layer-piano"]
    assert snap.transport == "playing"
    assert snap.telemetry.last_event == "started"
    assert snap.instructions.stop is False
    assert "track-stinger" in {row.track_id for row in snap.instructions.track_gains}
    assert _gain(snap, "track-stinger")["target_gain"] == 0
    assert all(row.track_id != "section-exploration" for row in snap.instructions.track_gains)

    clock = _cmd(clock, inputs, {"op": "advance", "advance_ticks": 7680})
    snap = clock.snapshot
    assert snap.position_tick == 0
    assert snap.runtime_state_id == "state-exploration"
    assert snap.telemetry.last_event == "loop_wrapped"

    clock = _cmd(clock, inputs, {"op": "advance", "advance_ticks": 2400})
    snap = clock.snapshot
    assert snap.position_tick == 2400
    assert (snap.bar, snap.beat) == (2, 2)
    assert snap.horizon_end_tick == 4320

    clock = _cmd(clock, inputs, {"op": "request_state", "to_state_id": "state-missing"})
    snap = clock.snapshot
    assert snap.position_tick == 2400
    assert snap.transport == "playing"
    assert snap.instructions.stop is False
    assert snap.telemetry.rejected_request_count == 1
    assert snap.queue == []
    assert any(item.code == "dangling_state_ref" for item in snap.warnings)

    clock = _cmd(clock, inputs, {"op": "request_state", "to_state_id": "state-suspense"})
    clock = _cmd(clock, inputs, {"op": "request_state", "to_state_id": "state-combat"})
    clock = _cmd(clock, inputs, {"op": "request_state", "to_state_id": "state-victory"})
    snap = clock.snapshot
    assert snap.position_tick == 2400
    assert snap.pending_transition is not None
    assert snap.pending_transition.transition_id == "tr-explore-suspense"
    assert snap.pending_transition.boundary_tick == 7680
    assert snap.pending_transition.realization_kind == "cut"
    assert [item.to_state_id for item in snap.queue] == ["state-combat", "state-victory"]

    clock = _cmd(clock, inputs, {"op": "advance", "advance_ticks": 5280})
    snap = clock.snapshot
    assert snap.position_tick == 7680
    assert (snap.bar, snap.beat) == (5, 1)
    assert snap.runtime_state_id == "state-suspense"
    assert snap.pending_transition is not None
    assert snap.pending_transition.to_state_id == "state-combat"
    assert snap.pending_transition.boundary_tick == 15360
    assert snap.pending_transition.realization_kind == "phrase"

    clock = _cmd(clock, inputs, {"op": "advance", "advance_ticks": 480})
    snap = clock.snapshot
    assert snap.position_tick == 8160
    assert (snap.bar, snap.beat) == (5, 2)
    assert snap.intensity == 0
    assert snap.runtime_state_id == "state-suspense"
    assert "layer-bass" not in snap.active_layer_ids
    assert "layer-strings" not in snap.active_layer_ids

    clock = _cmd(clock, inputs, {"op": "set_intensity", "intensity": 0.5})
    snap = clock.snapshot
    assert snap.position_tick == 8160
    assert "layer-bass" in snap.active_layer_ids
    assert "layer-strings" in snap.active_layer_ids
    assert "layer-pad" in snap.active_layer_ids
    assert _gain(snap, "track-bass")["target_gain"] == 1
    assert _gain(snap, "track-bass")["fade_end_tick"] == 8544
    assert _gain(snap, "track-strings")["fade_end_tick"] == 8544

    clock = _cmd(clock, inputs, {"op": "advance", "advance_ticks": 7200})
    snap = clock.snapshot
    assert snap.position_tick == 15360
    assert snap.phase == "phrase"
    assert snap.phrase is not None
    assert (snap.phrase.start_tick, snap.phrase.end_tick) == (15360, 17280)
    assert snap.runtime_state_id == "state-suspense"
    assert snap.instructions.seek_tick is None

    clock = _cmd(clock, inputs, {"op": "advance", "advance_ticks": 1920})
    snap = clock.snapshot
    assert snap.position_tick == 17280
    assert (snap.bar, snap.beat) == (10, 1)
    assert snap.runtime_state_id == "state-combat"
    assert snap.phase == "bed"
    assert snap.loop.enabled is True
    assert (snap.loop.start_bar, snap.loop.end_bar) == (9, 12)
    assert snap.pending_transition is not None
    assert snap.pending_transition.boundary_tick == 23040

    clock = _cmd(clock, inputs, {"op": "advance", "advance_ticks": 720})
    snap = clock.snapshot
    assert snap.position_tick == 18000
    assert snap.runtime_state_id == "state-combat"
    assert snap.intensity == 0.5

    clock = _cmd(clock, inputs, {"op": "set_intensity", "intensity": 1})
    snap = clock.snapshot
    assert "layer-orch" in snap.active_layer_ids
    assert "layer-brass-hint" not in snap.active_layer_ids
    assert _gain(snap, "track-orch")["fade_end_tick"] == 19200

    clock = _cmd(clock, inputs, {"op": "advance", "advance_ticks": 5040})
    snap = clock.snapshot
    assert snap.position_tick == 23040
    assert (snap.bar, snap.beat) == (13, 1)
    assert snap.runtime_state_id == "state-victory"
    assert snap.active_stinger_id == "stinger-victory"
    assert _gain(snap, "track-stinger")["target_gain"] == 1
    assert snap.transport == "playing"

    before = _gain(snap, "track-stinger")
    assert before["target_gain"] == 1
    clock = _cmd(clock, inputs, {"op": "advance", "advance_ticks": 1920})
    snap = clock.snapshot
    assert snap.position_tick == 24960
    assert (snap.bar, snap.beat) == (14, 1)
    assert snap.active_stinger_id is None
    assert snap.phase == "bed"
    assert snap.runtime_state_id == "state-victory"
    assert snap.transport == "playing"
    assert _gain(snap, "track-stinger")["target_gain"] == 0
    assert not any(row.track_id.startswith("section-") for row in snap.instructions.track_gains)


def test_script_is_deterministic() -> None:
    first = _run_script()
    second = _run_script()
    assert first == second


def test_loop_disarms_when_boundary_is_inside_horizon() -> None:
    inputs = _inputs()
    clock = _begin()
    clock = _cmd(clock, inputs, {"op": "advance", "advance_ticks": 5760})
    clock = _cmd(clock, inputs, {"op": "request_state", "to_state_id": "state-suspense"})
    snap = clock.snapshot
    assert snap.position_tick == 5760
    assert snap.pending_transition is not None
    assert snap.pending_transition.boundary_tick == 7680
    assert snap.instructions.loop.enabled is False
    assert snap.transport == "playing"


def test_one_advance_wraps_before_a_later_boundary() -> None:
    score = AdaptiveScoreV1.model_validate(
        {
            "schema_version": "adaptive.score.v1",
            "name": "wrap first",
            "initial_state_id": "state-a",
            "default_state_id": "state-a",
            "states": [
                {
                    "id": "state-a",
                    "name": "A",
                    "intensity": 0,
                    "material": {"kind": "bar_range", "start_bar": 1, "end_bar": 8},
                    "loop": {"enabled": True, "start_bar": 1, "end_bar": 4},
                    "transition_ids": ["tr-cue"],
                },
                {
                    "id": "state-b",
                    "name": "B",
                    "intensity": 0,
                    "material": {"kind": "bar_range", "start_bar": 9, "end_bar": 12},
                    "transition_ids": [],
                },
            ],
            "transitions": [
                {
                    "id": "tr-cue",
                    "from_state_id": "state-a",
                    "to_state_id": "state-b",
                    "quantization": "cue",
                    "cue_label": "go",
                    "conditions": [],
                }
            ],
        }
    )
    inputs = PlaybackInputs(
        score=score,
        timeline=_timeline(),
        sections=_sections(),
        markers=(TransitionMarkerProjection(kind="rehearsal", label="go", tick=9120),),
        layers=(),
    )
    clock = begin_adaptive_playback(
        inputs, playback_id=_PLAYBACK, mode="simulation", document_revision=1
    )
    clock = _cmd(clock, inputs, {"op": "advance", "advance_ticks": 6000})
    clock = _cmd(clock, inputs, {"op": "request_state", "to_state_id": "state-b", "transition_id": "tr-cue"})
    clock = _cmd(clock, inputs, {"op": "advance", "advance_ticks": 4000})
    snap = clock.snapshot
    assert snap.runtime_state_id == "state-a"
    assert snap.position_tick == 2320
    assert snap.pending_transition is not None
    assert snap.pending_transition.boundary_tick == 9120
    assert snap.telemetry.last_event == "loop_wrapped"


def test_unsatisfied_transition_keeps_playing() -> None:
    payload = scenario_score()
    payload["states"][0]["transition_ids"] = ["tr-explore-suspense", "tr-blocked"]
    payload["transitions"].append(
        {
            "id": "tr-blocked",
            "from_state_id": "state-exploration",
            "to_state_id": "state-victory",
            "quantization": "bar",
            "conditions": [{"kind": "flag_equals", "flag": "boss", "value": True}],
            "realization": {"kind": "cut"},
        }
    )
    score = AdaptiveScoreV1.model_validate(payload)
    inputs = PlaybackInputs(
        score=score,
        timeline=_timeline(),
        sections=_sections(),
        markers=(),
        layers=_layers(),
    )
    clock = begin_adaptive_playback(
        inputs, playback_id=_PLAYBACK, mode="simulation", document_revision=1
    )
    clock = _cmd(
        clock,
        inputs,
        {"op": "request_state", "to_state_id": "state-victory", "transition_id": "tr-blocked"},
    )
    snap = clock.snapshot
    assert snap.transport == "playing"
    assert snap.runtime_state_id == "state-exploration"
    assert snap.instructions.stop is False
    assert any(item.code == "transition_unsatisfied" for item in snap.warnings)


def test_fifth_queued_request_stays_playing() -> None:
    inputs = _inputs()
    clock = _begin()
    for state_id in ("state-suspense", "state-combat", "state-victory", "state-exploration", "state-suspense"):
        clock = _cmd(clock, inputs, {"op": "request_state", "to_state_id": state_id})
    snap = clock.snapshot
    assert len(snap.queue) == 4
    clock = _cmd(clock, inputs, {"op": "request_state", "to_state_id": "state-combat"})
    snap = clock.snapshot
    assert len(snap.queue) == 4
    assert snap.transport == "playing"
    assert any(item.code == "playback_queue_full" for item in snap.warnings)


def test_stinger_is_silent_until_it_starts() -> None:
    inputs = _inputs()
    clock = _begin()
    script = [
        {"op": "advance", "advance_ticks": 7680},
        {"op": "advance", "advance_ticks": 2400},
        {"op": "request_state", "to_state_id": "state-suspense"},
        {"op": "request_state", "to_state_id": "state-combat"},
        {"op": "request_state", "to_state_id": "state-victory"},
        {"op": "advance", "advance_ticks": 5280},
        {"op": "advance", "advance_ticks": 480},
        {"op": "set_intensity", "intensity": 0.5},
        {"op": "advance", "advance_ticks": 7200},
        {"op": "advance", "advance_ticks": 1920},
        {"op": "advance", "advance_ticks": 720},
        {"op": "set_intensity", "intensity": 1},
    ]
    for body in script:
        clock = _cmd(clock, inputs, body)
    assert _gain(clock.snapshot, "track-stinger")["target_gain"] == 0
    clock = _cmd(clock, inputs, {"op": "advance", "advance_ticks": 5040})
    assert _gain(clock.snapshot, "track-stinger")["target_gain"] == 1


def test_debug_log_omits_material(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    _begin()
    text = " ".join(record.getMessage() for record in caplog.records)
    extras = " ".join(str(getattr(record, "runtime_state_id", "")) for record in caplog.records)
    assert "Exploration" not in text
    assert "events" not in text
    assert "state-exploration" in extras


def _run_script() -> dict:
    inputs = _inputs()
    clock = _begin()
    bodies = [
        {"op": "advance", "advance_ticks": 7680},
        {"op": "advance", "advance_ticks": 2400},
        {"op": "request_state", "to_state_id": "state-missing"},
        {"op": "request_state", "to_state_id": "state-suspense"},
        {"op": "request_state", "to_state_id": "state-combat"},
        {"op": "request_state", "to_state_id": "state-victory"},
        {"op": "advance", "advance_ticks": 5280},
        {"op": "advance", "advance_ticks": 480},
        {"op": "set_intensity", "intensity": 0.5},
        {"op": "advance", "advance_ticks": 7200},
        {"op": "advance", "advance_ticks": 1920},
        {"op": "advance", "advance_ticks": 720},
        {"op": "set_intensity", "intensity": 1},
        {"op": "advance", "advance_ticks": 5040},
        {"op": "advance", "advance_ticks": 1920},
    ]
    for body in bodies:
        clock = _cmd(clock, inputs, body)
    assert clock.snapshot is not None
    return clock.snapshot.model_dump(mode="json")
