"""Env parsing for AI scheduling settings."""

from __future__ import annotations

from app.scheduling_settings import load_scheduling_settings


def test_defaults_are_off() -> None:
    settings = load_scheduling_settings({})
    assert settings.enabled is False
    assert settings.default_mode == "prefer_local"
    assert settings.allow_public_cloud is False
    assert settings.max_attempts == 2
    assert settings.local_memory_available_mb is None


def test_truthy_flag_enables() -> None:
    settings = load_scheduling_settings({"AI_SCHEDULING_ENABLED": "yes"})
    assert settings.enabled is True


def test_unrecognized_flag_is_off() -> None:
    settings = load_scheduling_settings({"AI_SCHEDULING_ENABLED": "maybe"})
    assert settings.enabled is False


def test_max_attempts_clamped() -> None:
    low = load_scheduling_settings({"AI_SCHEDULING_MAX_ATTEMPTS": "0"})
    high = load_scheduling_settings({"AI_SCHEDULING_MAX_ATTEMPTS": "99"})
    assert low.max_attempts == 1
    assert high.max_attempts == 4


def test_local_resource_hints_parsed() -> None:
    settings = load_scheduling_settings(
        {
            "AI_SCHEDULING_LOCAL_MEMORY_AVAILABLE_MB": "4096",
            "AI_SCHEDULING_LOCAL_MEMORY_TOTAL_MB": "8192",
            "AI_SCHEDULING_LOCAL_DEVICE_CLASS": "igpu",
            "AI_SCHEDULING_LOCAL_ESTIMATED_LATENCY_MS": "40",
        }
    )
    assert settings.local_memory_available_mb == 4096
    assert settings.local_memory_total_mb == 8192
    assert settings.local_device_class == "igpu"
    assert settings.local_estimated_latency_ms == 40


def test_default_mode_and_allow_public() -> None:
    settings = load_scheduling_settings(
        {
            "AI_SCHEDULING_DEFAULT_MODE": "fastest_available",
            "AI_SCHEDULING_ALLOW_PUBLIC_CLOUD": "1",
            "AI_SCHEDULING_FIXED_NODE_ID": "node_0123456789abcdef",
        }
    )
    assert settings.default_mode == "fastest_available"
    assert settings.allow_public_cloud is True
    assert settings.fixed_node_id == "node_0123456789abcdef"
