"""Env parsing for Ardour companion settings."""

from __future__ import annotations

from app.ardour_companion_settings import load_ardour_companion_settings


def test_defaults_are_off() -> None:
    settings = load_ardour_companion_settings({})
    assert settings.enabled is False
    assert settings.fake is False
    assert settings.allow_public_hosts is False
    assert settings.default_host == "127.0.0.1"
    assert settings.default_osc_port == 3819
    assert settings.default_feedback_port == 8000
    assert settings.feedback_stale_ms == 3000


def test_truthy_flag_enables() -> None:
    settings = load_ardour_companion_settings({"ARDOUR_COMPANION_ENABLED": "yes"})
    assert settings.enabled is True


def test_unrecognized_flag_is_off() -> None:
    settings = load_ardour_companion_settings({"ARDOUR_COMPANION_ENABLED": "maybe"})
    assert settings.enabled is False


def test_fake_and_public_flags() -> None:
    settings = load_ardour_companion_settings(
        {
            "ARDOUR_COMPANION_FAKE": "1",
            "ARDOUR_COMPANION_ALLOW_PUBLIC_HOSTS": "true",
        }
    )
    assert settings.fake is True
    assert settings.allow_public_hosts is True


def test_ports_and_timeouts_clamped() -> None:
    settings = load_ardour_companion_settings(
        {
            "ARDOUR_COMPANION_DEFAULT_OSC_PORT": "99999",
            "ARDOUR_COMPANION_DEFAULT_FEEDBACK_PORT": "3819",
            "ARDOUR_COMPANION_FEEDBACK_STALE_MS": "10",
            "ARDOUR_COMPANION_COMMAND_TIMEOUT_MS": "5",
        }
    )
    assert settings.default_osc_port == 65535
    assert settings.default_feedback_port == 8000
    assert settings.feedback_stale_ms == 500
    assert settings.command_timeout_ms == 100


def test_custom_default_host() -> None:
    settings = load_ardour_companion_settings(
        {"ARDOUR_COMPANION_DEFAULT_HOST": "10.0.0.2"}
    )
    assert settings.default_host == "10.0.0.2"
