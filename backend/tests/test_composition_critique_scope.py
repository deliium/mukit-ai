"""Tests for critique evaluation scope resolution."""

from __future__ import annotations

import pytest

from app.composition_schemas import CompositionV2
from app.critique_schemas import CritiqueError, CritiqueScopeBars
from app.services.composition_critique_scope import (
    event_in_resolved_scope,
    resolve_critique_scope,
)
from tests.test_composition_v2_schema import minimal_v2


def _comp(**overrides) -> CompositionV2:
    return CompositionV2.model_validate(minimal_v2(**overrides))


def test_composition_scope_full_window():
    composition = _comp()
    resolved = resolve_critique_scope(composition, {"kind": "composition"})
    assert resolved.kind == "composition"
    assert resolved.start_bar == 1
    assert resolved.end_bar == composition.bar_count
    assert resolved.start_tick == 0
    assert resolved.end_tick == composition.duration_ticks


def test_bars_scope_valid_and_rejects_oob():
    composition = _comp()
    resolved = resolve_critique_scope(
        composition, CritiqueScopeBars(start_bar=1, end_bar=2)
    )
    assert resolved.kind == "bars"
    assert resolved.start_bar == 1
    assert resolved.end_bar == 2
    assert resolved.digest.start_bar == 1

    with pytest.raises(CritiqueError) as exc:
        resolve_critique_scope(composition, {"kind": "bars", "start_bar": 1, "end_bar": 99})
    assert exc.value.code == "critique_invalid_scope"


def test_section_and_track_scopes():
    composition = _comp()
    section = resolve_critique_scope(composition, {"kind": "section", "section_index": 0})
    assert section.kind == "section"
    assert section.section_index == 0

    track = resolve_critique_scope(composition, {"kind": "track", "track_id": "piano-1"})
    assert track.track_id == "piano-1"

    with pytest.raises(CritiqueError):
        resolve_critique_scope(composition, {"kind": "track", "track_id": "missing"})


def test_event_in_resolved_scope_clip():
    composition = _comp()
    bars = resolve_critique_scope(composition, {"kind": "bars", "start_bar": 2, "end_bar": 2})
    assert event_in_resolved_scope(
        start_tick=1920, duration_ticks=480, track_id="piano-1", resolved=bars
    )
    assert not event_in_resolved_scope(
        start_tick=0, duration_ticks=100, track_id="piano-1", resolved=bars
    )

    track = resolve_critique_scope(composition, {"kind": "track", "track_id": "piano-1"})
    assert not event_in_resolved_scope(
        start_tick=0, duration_ticks=480, track_id="bass-1", resolved=track
    )
