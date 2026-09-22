"""Tests for COMPOSER_PROFILE_* settings caps."""

from __future__ import annotations

import logging

from app.composer_profile_settings import load_composer_profile_settings


def test_load_composer_profile_settings_defaults():
    settings = load_composer_profile_settings({})
    assert settings.max_profiles == 50
    assert settings.max_source_projects == 16
    assert settings.max_export_bytes == 256 * 1024
    assert settings.fragment_max_chars == 1_200
    assert settings.max_list_items == 16


def test_load_composer_profile_settings_env_overrides():
    settings = load_composer_profile_settings(
        {
            "COMPOSER_PROFILE_MAX_PROFILES": "10",
            "COMPOSER_PROFILE_MAX_SOURCE_PROJECTS": "4",
            "COMPOSER_PROFILE_MAX_EXPORT_BYTES": "8192",
            "COMPOSER_PROFILE_FRAGMENT_MAX_CHARS": "400",
            "COMPOSER_PROFILE_MAX_LIST_ITEMS": "8",
        }
    )
    assert settings.max_profiles == 10
    assert settings.max_source_projects == 4
    assert settings.max_export_bytes == 8192
    assert settings.fragment_max_chars == 400
    assert settings.max_list_items == 8


def test_invalid_and_clamped_env(caplog):
    with caplog.at_level(logging.WARNING):
        settings = load_composer_profile_settings(
            {
                "COMPOSER_PROFILE_MAX_PROFILES": "not-a-number",
                "COMPOSER_PROFILE_MAX_SOURCE_PROJECTS": "9999",
                "COMPOSER_PROFILE_FRAGMENT_MAX_CHARS": "1",
            }
        )
    assert settings.max_profiles == 50
    assert settings.max_source_projects == 64  # clamped
    assert settings.fragment_max_chars == 64  # clamped to minimum
    assert any("composer profile" in r.message.lower() for r in caplog.records)
