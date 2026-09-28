"""Pure intensity map for the locked adaptive layer fixture."""

from __future__ import annotations

import json
import logging

import pytest

from app.adaptive_score_schemas import AdaptiveScoreError
from app.services.adaptive_score_layers import (
    LayerProjection,
    LayerTimelineProjection,
    map_adaptive_layers,
)

_STATE = "state-exploration"
_FIXTURE = json.loads(
    """
    {
      "layers": [
        {"id": "layer-pad", "role": "ambient", "min": 0, "max": 1, "group": null, "priority": 0},
        {"id": "layer-piano", "role": "harmony", "min": 0, "max": 1, "group": null, "priority": 0},
        {"id": "layer-bass", "role": "bass", "min": 0.5, "max": 1, "group": null, "priority": 0},
        {"id": "layer-strings", "role": "strings", "min": 0.5, "max": 1, "group": null, "priority": 0},
        {"id": "layer-perc", "role": "percussion", "min": 0.8, "max": 1, "group": null, "priority": 0},
        {"id": "layer-brass-hint", "role": "brass", "min": 0.9, "max": 1, "group": "orchestration", "priority": 0},
        {"id": "layer-orch", "role": "brass", "min": 1, "max": 1, "group": "orchestration", "priority": 1}
      ]
    }
    """
)


def _timeline(
    *,
    duration_ticks: int = 7680,
    boundaries: tuple[int, ...] | None = None,
) -> LayerTimelineProjection:
    return LayerTimelineProjection(
        bar_boundaries=boundaries or (0, 1920, 3840, 5760, 7680),
        duration_ticks=duration_ticks,
        ticks_per_quarter=480,
        tempo_bpm=120,
    )


def _layer(
    spec: dict,
    *,
    state_id: str | None = _STATE,
    in_policy: str = "cut",
    out_policy: str = "cut",
    fade_in_ms: int = 0,
    fade_out_ms: int = 0,
    span_start_tick: int | None = 0,
    span_end_tick: int | None = 7680,
    material_kind: str = "track_range",
    track_ids: tuple[str, ...] = (),
) -> LayerProjection:
    return LayerProjection(
        id=spec["id"],
        state_id=state_id,
        role=spec["role"],
        intensity_min=spec["min"],
        intensity_max=spec["max"],
        exclusive_group=spec["group"],
        priority=spec["priority"],
        in_policy=in_policy,
        out_policy=out_policy,
        fade_in_ms=fade_in_ms,
        fade_out_ms=fade_out_ms,
        span_start_tick=span_start_tick,
        span_end_tick=span_end_tick,
        track_ids=track_ids,
        material_kind=material_kind,
    )


def _fixture_layers() -> tuple[LayerProjection, ...]:
    return tuple(
        _layer(spec, in_policy="bar" if spec["id"] == "layer-orch" else "cut")
        for spec in _FIXTURE["layers"]
    )


def _active_ids(intensity: float, **kwargs: object) -> tuple[str, ...]:
    result = map_adaptive_layers(
        _fixture_layers(),
        state_id=_STATE,
        intensity=intensity,
        position_tick=0,
        previous_intensity=None,
        timeline=_timeline(),
    )
    return tuple(row.layer_id for row in result.layers if row.active)


def test_locked_fixture_active_ids() -> None:
    window = json.loads('{"included": 0.8, "outside": 0.79}')
    assert _active_ids(0) == ("layer-pad", "layer-piano")
    assert _active_ids(0.5) == (
        "layer-pad",
        "layer-piano",
        "layer-bass",
        "layer-strings",
    )
    perc = next(spec for spec in _FIXTURE["layers"] if spec["id"] == "layer-perc")
    assert perc["min"] == window["included"]
    assert _active_ids(window["included"]) == (
        "layer-pad",
        "layer-piano",
        "layer-bass",
        "layer-strings",
        "layer-perc",
    )
    assert window["outside"] < perc["min"]
    assert "layer-perc" not in _active_ids(window["outside"])
    nine = _active_ids(0.9)
    assert nine == (
        "layer-pad",
        "layer-piano",
        "layer-bass",
        "layer-strings",
        "layer-perc",
        "layer-brass-hint",
    )
    full = map_adaptive_layers(
        _fixture_layers(),
        state_id=_STATE,
        intensity=1,
        position_tick=0,
        previous_intensity=None,
        timeline=_timeline(),
    )
    active = tuple(row.layer_id for row in full.layers if row.active)
    assert active == (
        "layer-pad",
        "layer-piano",
        "layer-bass",
        "layer-strings",
        "layer-perc",
        "layer-orch",
    )
    brass = next(row for row in full.layers if row.layer_id == "layer-brass-hint")
    assert brass.active is False
    assert brass.reason == "suppressed"
    assert brass.suppressed_by == "layer-orch"
    assert brass.target_gain == 0
    winner = next(row for row in full.layers if row.layer_id == "layer-orch")
    assert winner.reason == "in_window"
    assert winner.target_gain == 1


def test_repeated_call_returns_the_same_rows() -> None:
    kwargs = {
        "state_id": _STATE,
        "intensity": 1.0,
        "position_tick": 2400,
        "previous_intensity": None,
        "timeline": _timeline(),
    }
    first = map_adaptive_layers(_fixture_layers(), **kwargs)
    second = map_adaptive_layers(_fixture_layers(), **kwargs)
    assert first == second


def test_other_state_is_out_of_state_and_null_state_stays_candidate() -> None:
    layers = (
        _layer(_FIXTURE["layers"][0], state_id="state-combat"),
        _layer(_FIXTURE["layers"][1], state_id=None),
    )
    result = map_adaptive_layers(
        layers,
        state_id=_STATE,
        intensity=0,
        position_tick=0,
        previous_intensity=None,
        timeline=_timeline(),
    )
    foreign, global_layer = result.layers
    assert foreign.reason == "out_of_state"
    assert foreign.active is False
    assert foreign.audible is False
    assert foreign.target_gain == 0
    assert global_layer.active is True
    assert global_layer.reason == "in_window"


def test_global_and_state_layer_share_one_exclusive_winner() -> None:
    hint = _FIXTURE["layers"][5]
    orch = _FIXTURE["layers"][6]
    layers = (
        _layer(hint, state_id=None),
        _layer(orch, state_id=_STATE),
    )
    result = map_adaptive_layers(
        layers,
        state_id=_STATE,
        intensity=1,
        position_tick=0,
        previous_intensity=None,
        timeline=_timeline(),
    )
    by_id = {row.layer_id: row for row in result.layers}
    assert by_id["layer-orch"].active is True
    assert by_id["layer-brass-hint"].reason == "suppressed"
    assert by_id["layer-brass-hint"].suppressed_by == "layer-orch"


def test_map_log_counts_without_layer_names(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    map_adaptive_layers(
        _fixture_layers(),
        state_id=_STATE,
        intensity=0.5,
        position_tick=2400,
        previous_intensity=None,
        timeline=_timeline(),
    )
    debugs = [record for record in caplog.records if record.levelno == logging.DEBUG]
    assert debugs
    assert any(getattr(record, "active_count", None) == 4 for record in debugs)
    blob = " ".join(record.getMessage() for record in caplog.records)
    assert "layer-bass" not in blob
    assert "pad" not in blob


def test_fade_end_ticks_for_bar_linear_and_cut() -> None:
    window = json.loads('{"at": 0.0}')
    timeline = _timeline()

    def one(policy: str, fade_ms: int, position: int) -> int:
        layer = LayerProjection(
            id="layer-one",
            state_id=_STATE,
            role="ambient",
            intensity_min=window["at"],
            intensity_max=1,
            exclusive_group=None,
            priority=0,
            in_policy=policy,
            out_policy="cut",
            fade_in_ms=fade_ms,
            fade_out_ms=0,
            span_start_tick=0,
            span_end_tick=7680,
            track_ids=(),
            material_kind="section",
        )
        result = map_adaptive_layers(
            (layer,),
            state_id=_STATE,
            intensity=window["at"],
            position_tick=position,
            previous_intensity=None,
            timeline=timeline,
        )
        return result.layers[0].fade_end_tick

    assert one("bar", 999, 2400) == 3840
    assert one("linear", 400, 2400) == 2784
    assert one("cut", 400, 2400) == 2400
    assert one("bar", 400, 1920) == 1920


def test_bass_span_misses_playhead_without_clearing_active() -> None:
    bass = next(spec for spec in _FIXTURE["layers"] if spec["id"] == "layer-bass")
    layer = _layer(
        bass,
        span_start_tick=0,
        span_end_tick=7680,
        material_kind="bar_range",
    )
    result = map_adaptive_layers(
        (layer,),
        state_id=_STATE,
        intensity=0.5,
        position_tick=8000,
        previous_intensity=None,
        timeline=_timeline(duration_ticks=9600, boundaries=(0, 1920, 3840, 5760, 7680, 9600)),
    )
    row = result.layers[0]
    assert row.active is True
    assert row.audible is False
    assert row.reason == "in_window"
    assert result.warnings == ()


def test_previous_intensity_selects_fade_edge() -> None:
    bass = next(spec for spec in _FIXTURE["layers"] if spec["id"] == "layer-bass")
    layer = _layer(bass, in_policy="linear", out_policy="bar", fade_in_ms=400, fade_out_ms=250)
    rising = map_adaptive_layers(
        (layer,),
        state_id=_STATE,
        intensity=0.5,
        position_tick=2400,
        previous_intensity=0.4,
        timeline=_timeline(),
    )
    falling = map_adaptive_layers(
        (layer,),
        state_id=_STATE,
        intensity=0.4,
        position_tick=2400,
        previous_intensity=0.5,
        timeline=_timeline(),
    )
    assert rising.layers[0].active is True
    assert rising.layers[0].fade_ms == 400
    assert rising.layers[0].fade_end_tick == 2784
    assert falling.layers[0].active is False
    assert falling.layers[0].fade_ms == 250
    assert falling.layers[0].fade_end_tick == 3840


def test_suppression_uses_out_policy_and_winner_uses_in_policy() -> None:
    layers = tuple(
        _layer(
            spec,
            in_policy="linear" if spec["id"] == "layer-orch" else "cut",
            out_policy="bar" if spec["id"] == "layer-brass-hint" else "cut",
            fade_in_ms=400 if spec["id"] == "layer-orch" else 0,
            fade_out_ms=100 if spec["id"] == "layer-brass-hint" else 0,
        )
        for spec in _FIXTURE["layers"]
        if spec["id"] in {"layer-brass-hint", "layer-orch"}
    )
    result = map_adaptive_layers(
        layers,
        state_id=_STATE,
        intensity=1,
        position_tick=2400,
        previous_intensity=0.9,
        timeline=_timeline(),
    )
    by_id = {row.layer_id: row for row in result.layers}
    assert by_id["layer-brass-hint"].active is False
    assert by_id["layer-brass-hint"].fade_ms == 100
    assert by_id["layer-brass-hint"].fade_end_tick == 3840
    assert by_id["layer-orch"].active is True
    assert by_id["layer-orch"].fade_ms == 400
    assert by_id["layer-orch"].fade_end_tick == 2784


def test_unknown_span_warns_once_when_active() -> None:
    pad = _FIXTURE["layers"][0]
    layer = _layer(
        pad,
        span_start_tick=None,
        span_end_tick=None,
        material_kind="motif",
    )
    result = map_adaptive_layers(
        (layer,),
        state_id=_STATE,
        intensity=0,
        position_tick=10,
        previous_intensity=None,
        timeline=_timeline(),
    )
    assert result.layers[0].active is True
    assert result.layers[0].audible is True
    assert len(result.warnings) == 1
    assert result.warnings[0].code == "span_unchecked"
    assert result.warnings[0].target_id == "layer-pad"


def test_position_past_duration_raises_without_rows() -> None:
    with pytest.raises(AdaptiveScoreError) as captured:
        map_adaptive_layers(
            _fixture_layers(),
            state_id=_STATE,
            intensity=0,
            position_tick=7681,
            previous_intensity=None,
            timeline=_timeline(),
        )
    assert captured.value.code == "position_outside"


def test_position_equal_to_duration_is_legal() -> None:
    result = map_adaptive_layers(
        _fixture_layers(),
        state_id=_STATE,
        intensity=0,
        position_tick=7680,
        previous_intensity=None,
        timeline=_timeline(),
    )
    assert result.layers[0].fade_end_tick == 7680


def test_fade_clamped_when_no_bar_remains(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    pad = _layer(_FIXTURE["layers"][0], in_policy="bar")
    result = map_adaptive_layers(
        (pad,),
        state_id=_STATE,
        intensity=0,
        position_tick=8000,
        previous_intensity=None,
        timeline=_timeline(duration_ticks=9000, boundaries=(0, 1920, 3840, 5760, 7680)),
    )
    assert result.layers[0].fade_end_tick == 9000
    assert result.warnings[0].code == "fade_clamped"
    assert any(
        "fade_clamped" in (getattr(record, "warning_codes", None) or [])
        for record in caplog.records
    )
