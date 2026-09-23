"""Unit tests for AI Jam live performance contracts (Task 1)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.live_performance_schemas import (
    JAM_BELIEF_INSIDE_FEATURES,
    JAM_ROLE_MATRIX,
    LIVE_PERFORMANCE_FEATURES_SCHEMA,
    LiveAccompanimentPredictRequestV1,
    LiveHarmonyBelief,
    LivePerformanceError,
    LivePerformanceFeaturesV1,
    LivePredictFeatures,
    resolve_jam_role_partition,
    validate_predict_request_domain,
)
from app.live_performance_settings import load_live_performance_settings


def _base_request(**overrides):
    body = {
        "schema_version": "live.accompaniment.predict.request.v1",
        "session_id": "sess-jam",
        "request_id": "req-jam-1",
        "clock": {"tick": 0, "bar": 1, "beat": 1, "tempo": 120},
        "active_harmony": {"symbol": "Cmaj7"},
        "features": {"density": 0.4},
        "horizon": {"bars": 1, "ms": 2000},
    }
    body.update(overrides)
    return body


def test_jam_role_matrix_locked() -> None:
    assert JAM_ROLE_MATRIX["user_melody"]["user_roles"] == ("melody",)
    assert JAM_ROLE_MATRIX["user_melody"]["ai_roles"] == ("bass", "accompaniment")
    assert JAM_ROLE_MATRIX["user_melody"]["optional_ai"] == ("texture",)
    assert JAM_ROLE_MATRIX["user_chords"]["ai_roles"] == ("melody", "bass", "texture")

    low = resolve_jam_role_partition("user_melody", "low")
    assert low["ai_roles"] == ("bass", "accompaniment")
    med = resolve_jam_role_partition("user_melody", "medium")
    assert med["ai_roles"] == ("bass", "accompaniment", "texture")


def test_features_reject_nested_belief() -> None:
    with pytest.raises(ValidationError) as exc:
        LivePredictFeatures.model_validate(
            {"density": 0.2, "belief": {"symbol": "C", "confidence": 0.9, "held": False}}
        )
    assert JAM_BELIEF_INSIDE_FEATURES in str(exc.value)

    with pytest.raises(ValidationError):
        LivePerformanceFeaturesV1.model_validate(
            {
                "schema_version": LIVE_PERFORMANCE_FEATURES_SCHEMA,
                "belief": {"symbol": "Am", "confidence": 0.5, "held": True},
            }
        )


def test_belief_is_separate_from_features() -> None:
    belief = LiveHarmonyBelief(symbol="Dm7", confidence=0.7, held=True, reason_code="jam_harmony_hold")
    features = LivePerformanceFeaturesV1()
    assert features.schema_version == LIVE_PERFORMANCE_FEATURES_SCHEMA
    assert "belief" not in features.model_dump()
    assert belief.held is True


def test_predict_accepts_jam_fields_and_structured_features() -> None:
    settings = load_live_performance_settings({})
    body = _base_request(
        jam_mode="user_melody",
        controls={
            "complexity": "high",
            "density": "medium",
            "style": "arp",
            "responsiveness": "high",
        },
        belief={"symbol": "G7", "confidence": 0.8, "held": False},
        role_mask=["bass", "accompaniment", "texture"],
        features={
            "schema_version": LIVE_PERFORMANCE_FEATURES_SCHEMA,
            "pitch_activity": {
                "note_on_rate": 2.0,
                "pc_histogram_12": [0.1] * 12,
                "register_mean": 64,
                "register_var": 4,
            },
            "beat": {"tick": 480, "bar": 2, "beat_in_bar": 1, "tick_in_bar": 0},
            "probable_key": {"tonic_pc": 7, "mode": "major", "confidence": 0.6},
            "probable_harmony": {
                "symbol": "G",
                "root_pc": 7,
                "quality": "maj",
                "confidence": 0.55,
            },
            "phrase": {
                "boundary_likely": False,
                "bars_since_boundary": 1,
                "confidence": 0.4,
            },
        },
        active_harmony={"symbol": "G7"},
    )
    req = LiveAccompanimentPredictRequestV1.model_validate(body)
    validate_predict_request_domain(req, settings)
    assert req.jam_mode == "user_melody"
    assert req.controls is not None
    assert req.controls.style == "arp"
    assert req.belief is not None
    assert req.belief.symbol == "G7"
    assert req.features.probable_harmony is not None
    assert req.features.probable_harmony.symbol == "G"


def test_jam_settings_defaults() -> None:
    settings = load_live_performance_settings({})
    assert settings.jam_harmony_confidence_min == 0.55
    assert settings.jam_harmony_dwell_ms == 400
    assert settings.jam_harmony_dwell_ms_floor == 150
    assert settings.jam_harmony_hysteresis == 0.15
    assert settings.jam_analysis_max_events == 48

    custom = load_live_performance_settings(
        {
            "LIVE_JAM_HARMONY_CONFIDENCE_MIN": "0.7",
            "LIVE_JAM_HARMONY_DWELL_MS": "600",
            "LIVE_JAM_HARMONY_HYSTERESIS": "0.2",
        }
    )
    assert custom.jam_harmony_confidence_min == 0.7
    assert custom.jam_harmony_dwell_ms == 600
    assert custom.jam_harmony_hysteresis == 0.2


def test_assert_features_belief_domain_error() -> None:
    from app.live_performance_schemas import assert_features_within_size

    with pytest.raises(LivePerformanceError) as exc:
        assert_features_within_size({"density": 0.1, "belief": {"symbol": "C"}})
    assert exc.value.code == JAM_BELIEF_INSIDE_FEATURES
