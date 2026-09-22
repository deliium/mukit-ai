"""Tests for REFERENCE_FEATURES_* settings caps."""

from __future__ import annotations

import logging

from app.reference_feature_settings import load_reference_feature_settings


def test_load_reference_feature_settings_defaults():
    settings = load_reference_feature_settings({})
    assert settings.max_references == 3
    assert settings.summary_max_chars == 280
    assert settings.soft_fragment_max_chars == 1_600
    assert settings.analyze_lru_size == 0


def test_load_reference_feature_settings_env_overrides():
    settings = load_reference_feature_settings(
        {
            "REFERENCE_FEATURES_MAX_REFERENCES": "2",
            "REFERENCE_FEATURES_SUMMARY_MAX_CHARS": "120",
            "REFERENCE_FEATURES_SOFT_FRAGMENT_MAX_CHARS": "800",
            "REFERENCE_FEATURES_ANALYZE_LRU_SIZE": "16",
        }
    )
    assert settings.max_references == 2
    assert settings.summary_max_chars == 120
    assert settings.soft_fragment_max_chars == 800
    assert settings.analyze_lru_size == 16


def test_invalid_and_clamped_env(caplog):
    with caplog.at_level(logging.WARNING):
        clamped = load_reference_feature_settings(
            {
                "REFERENCE_FEATURES_MAX_REFERENCES": "99",
                "REFERENCE_FEATURES_SUMMARY_MAX_CHARS": "1",
            }
        )
        bad = load_reference_feature_settings(
            {"REFERENCE_FEATURES_SOFT_FRAGMENT_MAX_CHARS": "abc"}
        )
    assert clamped.max_references == 8
    assert clamped.summary_max_chars == 64
    assert bad.soft_fragment_max_chars == 1_600
    assert any("reference feature" in r.message.lower() for r in caplog.records)
