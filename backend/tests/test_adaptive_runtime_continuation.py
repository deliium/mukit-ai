"""Deterministic window and fallback tests for adaptive runtime continuation."""

from __future__ import annotations

import logging

import pytest

from app.adaptive_runtime_continuation_schemas import AdaptiveRuntimeContextV1
from app.composition_plan_schemas import CompositionPlan
from app.services.adaptive_runtime_continuation import (
    FALLBACK_ACCOMPANIMENT,
    FALLBACK_MOTIF,
    FALLBACK_REUSE_LOOP,
    WARNING_METER,
    WARNING_VIRTUAL_TIMELINE,
    WARNING_WINDOW_PAST_END,
    plan_continuous_window,
    plan_runtime_window,
    prefix_window,
    result_applicable,
    update_runtime_context,
    virtual_bar_deadline_tick,
)
from app.services.adaptive_runtime_continuation_fallback import (
    place_local_events,
    realize_fallback,
)
from app.services.composition_planner import ComposerFormPlan, ComposerFormSection, ValidationDiagnostic
from app.services.composition_timing import bar_duration_ticks
from app.services.composition_validator import CompositionValidationResult
from app.services.fake_symbolic_composer import generate_fake_symbolic_composition


def _context(*, repetition: int = 0, themes: list[str] | None = None) -> AdaptiveRuntimeContextV1:
    return update_runtime_context(
        None,
        intensity=0.4,
        theme_ids=themes or [],
        harmony_tail=None,
        harmony_chord_count=0,
        recent_start_bar=1,
        recent_end_bar=1,
        repetition_count=repetition,
    )


def _plan_form(bar_count: int = 8) -> CompositionPlan:
    return CompositionPlan(
        form=ComposerFormPlan(
            tempo=120,
            key="C major",
            time_signature="4/4",
            bar_count=bar_count,
            sections=[
                ComposerFormSection(type="verse", start_bar=1, bar_count=bar_count),
            ],
            instrumentation=["piano"],
        )
    )


def _ticks() -> int:
    return bar_duration_ticks("4/4", 480)


def test_locked_window_and_short_prefix_clip(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    deadline = 5 * _ticks()
    window = plan_runtime_window(
        bar=1,
        bar_count=16,
        loop_start_bar=1,
        loop_end_bar=4,
        loop_enabled=True,
        intensity=0.4,
        state_id="state-exploration",
        context=_context(),
        deadline_tick=deadline,
    )
    assert (window.reserved_start_bar, window.reserved_end_bar) == (1, 5)
    assert (window.target_start_bar, window.target_end_bar) == (6, 13)
    assert window.fallback_kind == FALLBACK_REUSE_LOOP
    assert prefix_window(anchor_bar=4, bar_count=4, prefix_bars=8) == (1, 4)
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "fallback_kind" in messages or any(
        getattr(record, "fallback_kind", None) == FALLBACK_REUSE_LOOP for record in caplog.records
    )


def test_anchor_near_the_end_is_past_the_target() -> None:
    window = plan_runtime_window(
        bar=14,
        bar_count=16,
        loop_start_bar=None,
        loop_end_bar=None,
        loop_enabled=False,
        intensity=0.2,
        state_id="state-exploration",
        context=_context(),
        deadline_tick=0,
    )
    assert window.target_start_bar is None
    assert window.target_end_bar is None
    assert window.job_status == "idle"
    assert window.fallback_kind == FALLBACK_REUSE_LOOP
    assert "continuation_window_past_end" in window.warnings
    assert window.reserved_start_bar == 14
    assert window.reserved_end_bar == 16


def test_repetition_switches_the_fallback_order() -> None:
    low = plan_runtime_window(
        bar=1,
        bar_count=16,
        loop_start_bar=None,
        loop_end_bar=None,
        loop_enabled=False,
        intensity=0.4,
        state_id="state-exploration",
        context=_context(repetition=2),
        deadline_tick=5 * _ticks(),
    )
    high = plan_runtime_window(
        bar=1,
        bar_count=16,
        loop_start_bar=None,
        loop_end_bar=None,
        loop_enabled=False,
        intensity=0.4,
        state_id="state-exploration",
        context=_context(repetition=3, themes=["motif-a"]),
        deadline_tick=5 * _ticks(),
    )
    assert low.fallback_order[0] == FALLBACK_REUSE_LOOP
    assert high.fallback_order[0] == FALLBACK_MOTIF
    assert high.fallback_kind == FALLBACK_MOTIF


def test_empty_motif_list_skips_to_accompaniment(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[object] = []
    real = generate_fake_symbolic_composition

    def spy(plan, *, seed=0, prefix_composition=None):
        seen.append(prefix_composition)
        return real(plan, seed=seed, prefix_composition=prefix_composition)

    monkeypatch.setattr(
        "app.services.adaptive_runtime_continuation_fallback.generate_fake_symbolic_composition",
        spy,
    )
    realized = realize_fallback(
        (FALLBACK_MOTIF, FALLBACK_ACCOMPANIMENT),
        loop_enabled=False,
        loop_start_bar=None,
        loop_end_bar=None,
        plan=_plan_form(),
        seed=7,
        relative_notes=None,
        anchor_midi=60,
        destination_track=None,
        composition=None,
        job_id="arcj_0123abcd",
        deadline_tick=5 * _ticks(),
        ticks_per_bar=_ticks(),
        generate_bars=8,
        target_span_ticks=8 * _ticks(),
    )
    assert realized.fallback_kind == FALLBACK_ACCOMPANIMENT
    assert seen == [None]


def test_same_seed_repeats_accompaniment_pitches(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    kwargs = dict(
        loop_enabled=False,
        loop_start_bar=None,
        loop_end_bar=None,
        plan=_plan_form(),
        seed=99,
        relative_notes=None,
        anchor_midi=60,
        destination_track=None,
        composition=None,
        job_id="arcj_0123abcd",
        deadline_tick=5 * _ticks(),
        ticks_per_bar=_ticks(),
        generate_bars=8,
        target_span_ticks=8 * _ticks(),
    )
    first = realize_fallback((FALLBACK_ACCOMPANIMENT,), **kwargs)
    second = realize_fallback((FALLBACK_ACCOMPANIMENT,), **kwargs)
    pitches = [event.pitch for event in first.events]
    assert pitches
    assert pitches == [event.pitch for event in second.events]
    blob = " ".join(
        f"{record.getMessage()} {record.__dict__}" for record in caplog.records
    )
    assert "fallback_kind" in blob
    for pitch in set(pitches):
        assert pitch not in blob


def test_applicability_rejects_bar_intensity_harmony_and_digest() -> None:
    shared = dict(
        target_start_bar=6,
        deadline_tick=5 * _ticks(),
        armed_state_id="state-exploration",
        playback_state_id="state-exploration",
        armed_revision=1,
        playback_revision=1,
        armed_intensity=0.2,
        playback_intensity=0.2,
        armed_harmony_tail="Cmaj7",
        playback_harmony_tail="Cmaj7",
        armed_prefix_digest="abc123",
        playback_prefix_digest="abc123",
    )
    late = result_applicable(playback_bar=6, position_tick=0, **shared)
    assert late.applicable is False
    assert late.warning_code == "continuation_late"
    jump = result_applicable(
        playback_bar=1,
        position_tick=0,
        **{**shared, "playback_intensity": 0.45},
    )
    assert jump.warning_code == "continuation_stale"
    harmony = result_applicable(
        playback_bar=1,
        position_tick=0,
        **{**shared, "playback_harmony_tail": "G7"},
    )
    assert harmony.warning_code == "continuation_stale"
    digest = result_applicable(
        playback_bar=1,
        position_tick=0,
        **{**shared, "playback_prefix_digest": "def456"},
    )
    assert digest.warning_code == "continuation_stale"
    held = result_applicable(playback_bar=1, position_tick=0, **shared)
    assert held.applicable is True


def test_validator_failure_walks_to_reuse_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args, **_kwargs):
        return CompositionValidationResult(
            ok=False,
            errors=[ValidationDiagnostic(code="schema_invalid", message="rejected")],
        )

    monkeypatch.setattr(
        "app.services.adaptive_runtime_continuation_fallback.validate_composition_integrity",
        fail,
    )
    realized = realize_fallback(
        (FALLBACK_ACCOMPANIMENT,),
        loop_enabled=False,
        loop_start_bar=None,
        loop_end_bar=None,
        plan=_plan_form(),
        seed=1,
        relative_notes=None,
        anchor_midi=60,
        destination_track=None,
        composition=None,
        job_id="arcj_0123abcd",
        deadline_tick=5 * _ticks(),
        ticks_per_bar=_ticks(),
        generate_bars=8,
        target_span_ticks=8 * _ticks(),
    )
    assert realized.fallback_kind == FALLBACK_REUSE_LOOP
    assert realized.events == ()
    assert "continuation_invalid" in realized.warnings


def test_place_local_events_puts_bar_one_on_bar_six() -> None:
    music, _report = generate_fake_symbolic_composition(
        _plan_form(),
        seed=3,
        prefix_composition=None,
    )
    deadline = 5 * _ticks()
    placed = place_local_events(
        music,
        deadline_tick=deadline,
        ticks_per_bar=_ticks(),
        generate_bars=8,
    )
    original = next(event for track in music.tracks for event in track.events if event.start_tick == 0)
    shifted = next(event for event in placed.events if event.id == original.id)
    assert shifted.start_tick == deadline


def test_legacy_past_end_idle_unchanged_when_continuous_false() -> None:
    window = plan_runtime_window(
        bar=14,
        bar_count=16,
        loop_start_bar=None,
        loop_end_bar=None,
        loop_enabled=False,
        intensity=0.2,
        state_id="state-exploration",
        context=_context(),
        deadline_tick=0,
        continuous=False,
    )
    assert window.job_status == "idle"
    assert window.target_start_bar is None
    assert WARNING_WINDOW_PAST_END in window.warnings
    assert WARNING_VIRTUAL_TIMELINE not in window.warnings


def test_continuous_virtual_non_idle_uses_virtual_bar_anchor(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    tpb = _ticks()
    last_end = 16 * tpb
    window = plan_runtime_window(
        bar=16,
        bar_count=16,
        loop_start_bar=1,
        loop_end_bar=16,
        loop_enabled=True,
        intensity=0.4,
        state_id="state-exploration",
        context=_context(),
        deadline_tick=last_end,
        continuous=True,
        virtual_bar=17,
        playback_at_end=True,
        last_compiled_bar_end_tick=last_end,
        ticks_per_bar_assumed=tpb,
    )
    assert window.job_status == "pending"
    assert window.anchor_bar == 17
    assert (window.reserved_start_bar, window.reserved_end_bar) == (17, 21)
    assert (window.target_start_bar, window.target_end_bar) == (22, 29)
    assert WARNING_VIRTUAL_TIMELINE in window.warnings
    assert WARNING_METER in window.warnings
    assert WARNING_WINDOW_PAST_END not in window.warnings
    expected_deadline = virtual_bar_deadline_tick(
        target_start_bar=22,
        bar_count=16,
        last_compiled_bar_end_tick=last_end,
        ticks_per_bar_assumed=tpb,
    )
    assert window.deadline_tick == expected_deadline
    assert any(getattr(record, "continuous", None) is True for record in caplog.records)
    assert any(getattr(record, "virtual_bar", None) == 17 for record in caplog.records)


def test_continuous_anchor_when_legacy_target_start_past_bar_count() -> None:
    tpb = _ticks()
    direct = plan_continuous_window(
        bar_count=8,
        virtual_bar=9,
        intensity=0.5,
        state_id="state-combat",
        context=_context(repetition=3),
        last_compiled_bar_end_tick=8 * tpb,
        ticks_per_bar_assumed=tpb,
        play_bars=5,
        generate_bars=8,
    )
    via_runtime = plan_runtime_window(
        bar=8,
        bar_count=8,
        loop_start_bar=None,
        loop_end_bar=None,
        loop_enabled=False,
        intensity=0.5,
        state_id="state-combat",
        context=_context(repetition=3),
        deadline_tick=8 * tpb,
        continuous=True,
        virtual_bar=9,
        last_compiled_bar_end_tick=8 * tpb,
        ticks_per_bar_assumed=tpb,
    )
    assert via_runtime.anchor_bar == 9
    assert via_runtime.target_start_bar == 14
    assert via_runtime.target_end_bar == 21
    assert via_runtime.target_start_bar > 8
    assert via_runtime.fallback_kind == FALLBACK_MOTIF
    assert direct.anchor_bar == via_runtime.anchor_bar
    assert direct.target_start_bar == via_runtime.target_start_bar


def test_continuous_in_score_keeps_playback_bar_anchor() -> None:
    window = plan_runtime_window(
        bar=1,
        bar_count=16,
        loop_start_bar=None,
        loop_end_bar=None,
        loop_enabled=False,
        intensity=0.4,
        state_id="state-exploration",
        context=_context(),
        deadline_tick=5 * _ticks(),
        continuous=True,
        virtual_bar=99,
    )
    assert window.anchor_bar == 1
    assert window.target_start_bar == 6
    assert WARNING_VIRTUAL_TIMELINE not in window.warnings
