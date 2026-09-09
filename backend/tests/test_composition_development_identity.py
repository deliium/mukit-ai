"""Tests for composition development identity and seam diagnostics."""

from __future__ import annotations

from app.composition_development_schemas import CompositionDevelopmentDraft
from app.composition_schemas import CompositionV2
from app.services.composition_development_context import build_development_source_context
from app.services.composition_development_identity import (
    STRENGTH_PROFILES,
    evaluate_development_identity,
    strength_profile,
)
from tests.test_composition_development_context import _four_bar_piece, _request


def _related_draft() -> CompositionDevelopmentDraft:
    return CompositionDevelopmentDraft.model_validate(
        {
            "tracks": [
                {
                    "track_id": "melody-1",
                    "events": [
                        {"pitch": "C4", "relative_start_tick": 0, "duration_ticks": 480},
                        {"pitch": "E4", "relative_start_tick": 480, "duration_ticks": 480},
                        {"pitch": "G4", "relative_start_tick": 960, "duration_ticks": 480},
                    ],
                },
                {
                    "track_id": "bass-1",
                    "events": [
                        {"pitch": "C2", "relative_start_tick": 0, "duration_ticks": 1920},
                    ],
                },
            ],
            "harmony": [{"relative_start_tick": 0, "duration_ticks": 1920, "chord": "C"}],
        }
    )


def _restart_draft() -> CompositionDevelopmentDraft:
    return CompositionDevelopmentDraft.model_validate(
        {
            "tracks": [
                {
                    "track_id": "melody-1",
                    "events": [
                        {"pitch": "C6", "relative_start_tick": 3000, "duration_ticks": 120},
                    ],
                },
                {"track_id": "bass-1", "events": []},
            ],
            "harmony": [],
        }
    )


def test_strength_profiles_are_versioned_and_ordered():
    conservative = strength_profile("conservative")
    balanced = strength_profile("balanced")
    experimental = strength_profile("experimental")
    assert conservative.max_rhythm_divergence < balanced.max_rhythm_divergence
    assert balanced.max_rhythm_divergence < experimental.max_rhythm_divergence
    assert set(STRENGTH_PROFILES) == {"conservative", "balanced", "experimental"}


def test_related_continuation_passes_balanced():
    composition = _four_bar_piece()
    context = build_development_source_context(_request(composition, variation_strength="balanced"))
    result = evaluate_development_identity(
        context=context,
        draft=_related_draft(),
        strength="balanced",
        development_intent="continue",
    )
    assert result.passed is True
    assert "identity_anchor_satisfied" in {item.code for item in result.diagnostics}


def test_missing_track_draft_fails():
    composition = _four_bar_piece()
    context = build_development_source_context(_request(composition))
    draft = CompositionDevelopmentDraft.model_validate(
        {
            "tracks": [
                {
                    "track_id": "melody-1",
                    "events": [{"pitch": "C4", "relative_start_tick": 0, "duration_ticks": 480}],
                }
            ]
        }
    )
    result = evaluate_development_identity(
        context=context,
        draft=draft,
        strength="balanced",
    )
    assert result.passed is False
    assert "missing_track_draft" in result.error_codes


def test_conservative_rejects_large_seam_leap_and_gap():
    composition = _four_bar_piece()
    context = build_development_source_context(
        _request(composition, variation_strength="conservative")
    )
    result = evaluate_development_identity(
        context=context,
        draft=_restart_draft(),
        strength="conservative",
        development_intent="continue",
    )
    assert result.passed is False
    codes = {item.code for item in result.diagnostics}
    assert "seam_leap" in codes or "seam_gap" in codes or "identity_anchor_failed" in codes


def test_contrast_mode_allows_greater_divergence_with_one_anchor():
    composition = _four_bar_piece()
    context = build_development_source_context(
        _request(
            composition,
            development_intent="contrast",
            variation_strength="experimental",
        )
    )
    draft = CompositionDevelopmentDraft.model_validate(
        {
            "tracks": [
                {
                    "track_id": "melody-1",
                    "events": [
                        {"pitch": "A4", "relative_start_tick": 0, "duration_ticks": 240},
                        {"pitch": "B4", "relative_start_tick": 240, "duration_ticks": 240},
                    ],
                },
                {
                    "track_id": "bass-1",
                    "events": [
                        {"pitch": "A1", "relative_start_tick": 0, "duration_ticks": 960},
                    ],
                },
            ],
            "harmony": [{"relative_start_tick": 0, "duration_ticks": 960, "chord": "Am"}],
        }
    )
    result = evaluate_development_identity(
        context=context,
        draft=draft,
        strength="experimental",
        development_intent="contrast",
    )
    assert result.scores["identity_anchor_count"] >= 1
    # Experimental contrast should not hard-fail solely on moderate divergence.
    assert "orchestration_anchor_missing" not in result.error_codes


def test_identity_evaluation_is_deterministic():
    composition = _four_bar_piece()
    context = build_development_source_context(_request(composition))
    draft = _related_draft()
    first = evaluate_development_identity(context=context, draft=draft, strength="balanced")
    second = evaluate_development_identity(context=context, draft=draft, strength="balanced")
    assert first.scores == second.scores
    assert [item.code for item in first.diagnostics] == [item.code for item in second.diagnostics]
