"""Deployment settings for capability-aware AI job scheduling.

Feature flag defaults off. Optional ``AI_SCHEDULING_LOCAL_*`` hints fill
controller-local candidate resources when descriptor limits omit them; they
never override ExecutionNode heartbeat resources.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Literal, Mapping

logger = logging.getLogger(__name__)

ENABLED_ENV = "AI_SCHEDULING_ENABLED"
DEFAULT_MODE_ENV = "AI_SCHEDULING_DEFAULT_MODE"
ALLOW_PUBLIC_CLOUD_ENV = "AI_SCHEDULING_ALLOW_PUBLIC_CLOUD"
MAX_ATTEMPTS_ENV = "AI_SCHEDULING_MAX_ATTEMPTS"
FIXED_NODE_ID_ENV = "AI_SCHEDULING_FIXED_NODE_ID"
LOCAL_MEMORY_AVAILABLE_ENV = "AI_SCHEDULING_LOCAL_MEMORY_AVAILABLE_MB"
LOCAL_MEMORY_TOTAL_ENV = "AI_SCHEDULING_LOCAL_MEMORY_TOTAL_MB"
LOCAL_DEVICE_CLASS_ENV = "AI_SCHEDULING_LOCAL_DEVICE_CLASS"
LOCAL_LATENCY_ENV = "AI_SCHEDULING_LOCAL_ESTIMATED_LATENCY_MS"

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_RECOGNIZED_OFF = frozenset({"", "0"})
_MODES = frozenset({"prefer_local", "fastest_available", "memory_safe", "fixed_node"})
_DEVICE_CLASSES = frozenset({"cpu", "igpu", "dgpu", "unknown"})

SchedulingModeSetting = Literal[
    "prefer_local",
    "fastest_available",
    "memory_safe",
    "fixed_node",
]
LocalDeviceClassSetting = Literal["cpu", "igpu", "dgpu", "unknown"]

_default_logged = False


@dataclass(frozen=True)
class SchedulingSettings:
    """Parsed ``AI_SCHEDULING_*`` environment values."""

    enabled: bool
    default_mode: SchedulingModeSetting
    allow_public_cloud: bool
    max_attempts: int
    fixed_node_id: str | None
    local_memory_available_mb: int | None
    local_memory_total_mb: int | None
    local_device_class: LocalDeviceClassSetting | None
    local_estimated_latency_ms: int | None


def _parse_truthy(raw: str | None) -> tuple[bool, bool]:
    text = "" if raw is None else str(raw).strip().lower()
    if text in _TRUTHY:
        return True, False
    if text in _RECOGNIZED_OFF:
        return False, False
    return False, True


def _clamp_int(raw: str | None, *, default: int, minimum: int, maximum: int) -> int:
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = int(str(raw).strip())
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def _optional_nonneg_int(raw: str | None) -> int | None:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        value = int(str(raw).strip())
    except ValueError:
        return None
    if value < 0:
        return None
    return value


def load_scheduling_settings(env: Mapping[str, str] | None = None) -> SchedulingSettings:
    """Load scheduling settings from ``env`` or the process environment."""
    source = env if env is not None else os.environ

    enabled, unrecognized_enabled = _parse_truthy(source.get(ENABLED_ENV))
    if unrecognized_enabled:
        logger.warning(
            "AI scheduling flag unrecognized; treating as off",
            extra={"code": "scheduling_flag_unrecognized"},
        )

    allow_public, unrecognized_public = _parse_truthy(source.get(ALLOW_PUBLIC_CLOUD_ENV))
    if unrecognized_public:
        logger.warning(
            "AI scheduling allow_public_cloud unrecognized; treating as off",
            extra={"code": "scheduling_allow_public_unrecognized"},
        )

    mode_raw = (source.get(DEFAULT_MODE_ENV) or "").strip().lower()
    if mode_raw in _MODES:
        default_mode: SchedulingModeSetting = mode_raw  # type: ignore[assignment]
    elif mode_raw == "":
        default_mode = "prefer_local"
    else:
        logger.warning(
            "AI scheduling default mode unrecognized; using prefer_local",
            extra={"code": "scheduling_mode_unrecognized"},
        )
        default_mode = "prefer_local"

    max_attempts = _clamp_int(
        source.get(MAX_ATTEMPTS_ENV),
        default=2,
        minimum=1,
        maximum=4,
    )

    fixed_raw = (source.get(FIXED_NODE_ID_ENV) or "").strip()
    fixed_node_id = fixed_raw or None
    if fixed_node_id is not None and not fixed_node_id.startswith("node_"):
        logger.warning(
            "AI scheduling fixed_node_id unrecognized; ignoring",
            extra={"code": "scheduling_fixed_node_unrecognized"},
        )
        fixed_node_id = None

    device_raw = (source.get(LOCAL_DEVICE_CLASS_ENV) or "").strip().lower()
    local_device: LocalDeviceClassSetting | None
    if device_raw in _DEVICE_CLASSES:
        local_device = device_raw  # type: ignore[assignment]
    elif device_raw == "":
        local_device = None
    else:
        logger.warning(
            "AI scheduling local device class unrecognized; ignoring",
            extra={"code": "scheduling_local_device_unrecognized"},
        )
        local_device = None

    settings = SchedulingSettings(
        enabled=enabled,
        default_mode=default_mode,
        allow_public_cloud=allow_public,
        max_attempts=max_attempts,
        fixed_node_id=fixed_node_id,
        local_memory_available_mb=_optional_nonneg_int(source.get(LOCAL_MEMORY_AVAILABLE_ENV)),
        local_memory_total_mb=_optional_nonneg_int(source.get(LOCAL_MEMORY_TOTAL_ENV)),
        local_device_class=local_device,
        local_estimated_latency_ms=_optional_nonneg_int(source.get(LOCAL_LATENCY_ENV)),
    )

    payload = {
        "enabled": settings.enabled,
        "default_mode": settings.default_mode,
        "allow_public_cloud": settings.allow_public_cloud,
        "max_attempts": settings.max_attempts,
        "fixed_node_configured": settings.fixed_node_id is not None,
        "local_memory_hint": settings.local_memory_available_mb is not None,
        "local_device_class": settings.local_device_class,
        "local_latency_hint": settings.local_estimated_latency_ms is not None,
    }
    global _default_logged
    if env is None and not _default_logged:
        logger.info("AI scheduling settings loaded", extra=payload)
        _default_logged = True
    else:
        logger.debug("AI scheduling settings loaded", extra=payload)

    return settings


def scheduling_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Return whether the feature flag is on."""
    return load_scheduling_settings(env).enabled


def ai_scheduling_readiness_block(env: Mapping[str, str] | None = None) -> dict:
    """Soft ``/ready`` block. Never fails overall readiness."""
    settings = load_scheduling_settings(env)
    mode = settings.default_mode
    allow_public = settings.allow_public_cloud
    if settings.enabled:
        try:
            from app.services.scheduling_policy_store import get_policy

            policy = get_policy(env=env)
            mode = policy.mode
            allow_public = policy.allow_public_cloud
        except Exception:  # noqa: BLE001
            pass
    return {
        "enabled": settings.enabled,
        "mode": mode,
        "allow_public_cloud": allow_public,
    }
