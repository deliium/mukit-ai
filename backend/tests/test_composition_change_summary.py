"""Tests for null-aware composition change summaries and scope enforcement."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.composition_schemas import CompositionV2
from app.project_history_schemas import AffectedBarRange, DeclaredScope
from app.services.composition_change_summary import (
    CompositionScopeError,
    enforce_declared_scope,
    summarize_composition_changes,
)

FIXTURE_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "fixtures" / "composition_v2_expressive.json"
)


@pytest.fixture
def expressive() -> CompositionV2:
    return CompositionV2.model_validate(json.loads(FIXTURE_PATH.read_text(encoding="utf-8")))


def test_identical_null_snapshots():
    summary = summarize_composition_changes(None, None)
    assert summary.identical is True
    assert summary.affected_ranges == ()
    assert summary.affected_track_ids == ()
    assert summary.source_event_count == 0
    assert summary.target_event_count == 0


def test_null_to_composition_is_full_document(expressive):
    summary = summarize_composition_changes(None, expressive)
    assert summary.identical is False
    assert summary.affected_ranges == (AffectedBarRange(start_bar=1, end_bar=expressive.bar_count),)
    assert set(summary.affected_track_ids) == {track.id for track in expressive.tracks}


def test_pitch_change_affects_track_and_bars(expressive):
    target = CompositionV2.model_validate(deepcopy(expressive.model_dump(mode="json")))
    payload = target.model_dump(mode="json")
    payload["tracks"][0]["events"][0]["pitch"] = "D4"
    target = CompositionV2.model_validate(payload)
    summary = summarize_composition_changes(expressive, target)
    assert summary.identical is False
    assert "melody-1" in summary.affected_track_ids
    assert summary.affected_ranges
    assert all(item.start_bar >= 1 for item in summary.affected_ranges)


def test_metadata_only_tempo_change_is_full_scope(expressive):
    payload = expressive.model_dump(mode="json")
    payload["tempo"] = expressive.tempo + 10
    target = CompositionV2.model_validate(payload)
    summary = summarize_composition_changes(expressive, target)
    assert summary.identical is False
    assert summary.affected_ranges == (AffectedBarRange(start_bar=1, end_bar=expressive.bar_count),)


def test_enforce_declared_scope_rejects_escaped_tracks(expressive):
    target_payload = expressive.model_dump(mode="json")
    target_payload["tracks"][0]["events"][0]["pitch"] = "D4"
    target = CompositionV2.model_validate(target_payload)
    summary = summarize_composition_changes(expressive, target)
    declared = DeclaredScope(track_ids=["bass-1"], ranges=[AffectedBarRange(start_bar=1, end_bar=4)])
    with pytest.raises(CompositionScopeError) as exc:
        enforce_declared_scope(summary, declared)
    assert exc.value.code == "scope_escape_tracks"


def test_enforce_declared_scope_allows_matching_scope(expressive):
    target_payload = expressive.model_dump(mode="json")
    target_payload["tracks"][0]["events"][0]["pitch"] = "D4"
    target = CompositionV2.model_validate(target_payload)
    summary = summarize_composition_changes(expressive, target)
    declared = DeclaredScope(
        track_ids=list(summary.affected_track_ids),
        ranges=list(summary.affected_ranges) or [AffectedBarRange(start_bar=1, end_bar=4)],
    )
    enforce_declared_scope(summary, declared)


def test_identical_summary_skips_scope_check(expressive):
    summary = summarize_composition_changes(expressive, expressive)
    enforce_declared_scope(
        summary,
        DeclaredScope(track_ids=["missing"], ranges=[AffectedBarRange(start_bar=99, end_bar=99)]),
    )
