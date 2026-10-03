"""Deployment settings for trusted-LAN ExecutionNode peers.

Feature flag defaults off. When enabled, a non-empty shared bearer token is
required. Raw token values are never logged.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Literal, Mapping

from app.execution_node_schemas import ExecutionNodeError

logger = logging.getLogger(__name__)

ENABLED_ENV = "AI_EXECUTION_NODES_ENABLED"
TOKEN_ENV = "AI_EXECUTION_NODE_TOKEN"
ROLE_ENV = "AI_EXECUTION_NODE_ROLE"
CONTROLLER_URL_ENV = "AI_EXECUTION_NODE_CONTROLLER_URL"
HEARTBEAT_INTERVAL_ENV = "AI_EXECUTION_NODE_HEARTBEAT_INTERVAL_SECONDS"
HEARTBEAT_TTL_ENV = "AI_EXECUTION_NODE_HEARTBEAT_TTL_SECONDS"
FAKE_ENV = "AI_EXECUTION_NODE_FAKE"
MAX_NODES_ENV = "AI_EXECUTION_NODE_MAX_NODES"
MAX_CONCURRENCY_ENV = "AI_EXECUTION_NODE_MAX_CONCURRENCY"
ADDRESS_CIDRS_ENV = "AI_EXECUTION_NODE_ADDRESS_ALLOW_CIDRS"
ALLOW_HOSTNAME_ENV = "AI_EXECUTION_NODE_ALLOW_HOSTNAME"

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_RECOGNIZED_OFF = frozenset({"", "0"})
ExecutionNodeRoleSetting = Literal["controller", "worker", "both"]

_default_logged = False


@dataclass(frozen=True)
class ExecutionNodeSettings:
    enabled: bool
    token: str | None
    role: ExecutionNodeRoleSetting
    controller_url: str | None
    heartbeat_interval_seconds: int
    heartbeat_ttl_seconds: int
    fake: bool
    max_nodes: int
    max_concurrency: int
    address_allow_cidrs: tuple[str, ...]
    allow_hostname: bool

    @property
    def token_present(self) -> bool:
        return bool(self.token)


def _parse_truthy(raw: str | None, *, code: str) -> tuple[bool, bool]:
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


def load_execution_node_settings(env: Mapping[str, str] | None = None) -> ExecutionNodeSettings:
    """Load settings from ``env`` or process environment.

    When enabled and the token is empty, raises
    ``execution_node_token_missing``. Unknown flag strings resolve to off and
    are logged by code only.
    """
    source = env if env is not None else os.environ
    enabled, unrecognized_enabled = _parse_truthy(
        source.get(ENABLED_ENV),
        code="execution_nodes_flag_unrecognized",
    )
    if unrecognized_enabled:
        logger.warning(
            "Execution nodes flag unrecognized; treating as off",
            extra={"code": "execution_nodes_flag_unrecognized"},
        )

    fake, unrecognized_fake = _parse_truthy(
        source.get(FAKE_ENV),
        code="execution_nodes_fake_unrecognized",
    )
    if unrecognized_fake:
        logger.warning(
            "Execution nodes fake flag unrecognized; treating as off",
            extra={"code": "execution_nodes_fake_unrecognized"},
        )

    allow_hostname, unrecognized_host = _parse_truthy(
        source.get(ALLOW_HOSTNAME_ENV),
        code="execution_nodes_hostname_unrecognized",
    )
    if unrecognized_host:
        logger.warning(
            "Execution nodes hostname flag unrecognized; treating as off",
            extra={"code": "execution_nodes_hostname_unrecognized"},
        )

    role_raw = (source.get(ROLE_ENV) or "").strip().lower()
    if role_raw in {"controller", "worker", "both"}:
        role: ExecutionNodeRoleSetting = role_raw  # type: ignore[assignment]
    elif role_raw == "":
        role = "controller"
    else:
        logger.warning(
            "Execution node role unrecognized; using controller",
            extra={"code": "execution_nodes_role_unrecognized"},
        )
        role = "controller"

    token_raw = source.get(TOKEN_ENV)
    token = None if token_raw is None else str(token_raw)
    if token is not None and token.strip() == "":
        token = None

    if enabled and token is None:
        logger.error(
            "Execution nodes enabled without token",
            extra={"code": "execution_node_token_missing"},
        )
        raise ExecutionNodeError(
            "execution_node_token_missing",
            "AI_EXECUTION_NODE_TOKEN is required when execution nodes are enabled.",
        )

    interval = _clamp_int(
        source.get(HEARTBEAT_INTERVAL_ENV),
        default=10,
        minimum=5,
        maximum=60,
    )
    ttl = _clamp_int(
        source.get(HEARTBEAT_TTL_ENV),
        default=30,
        minimum=15,
        maximum=120,
    )
    if ttl <= interval:
        ttl = min(120, interval + 5)

    max_nodes = _clamp_int(
        source.get(MAX_NODES_ENV),
        default=8,
        minimum=1,
        maximum=32,
    )
    max_concurrency = _clamp_int(
        source.get(MAX_CONCURRENCY_ENV),
        default=2,
        minimum=1,
        maximum=64,
    )

    cidrs_raw = (source.get(ADDRESS_CIDRS_ENV) or "").strip()
    cidrs = tuple(part.strip() for part in cidrs_raw.split(",") if part.strip())

    controller_raw = (source.get(CONTROLLER_URL_ENV) or "").strip()
    controller_url = controller_raw or None

    settings = ExecutionNodeSettings(
        enabled=enabled,
        token=token,
        role=role,
        controller_url=controller_url,
        heartbeat_interval_seconds=interval,
        heartbeat_ttl_seconds=ttl,
        fake=fake,
        max_nodes=max_nodes,
        max_concurrency=max_concurrency,
        address_allow_cidrs=cidrs,
        allow_hostname=allow_hostname,
    )

    payload = {
        "enabled": settings.enabled,
        "role": settings.role,
        "heartbeat_ttl": settings.heartbeat_ttl_seconds,
        "fake": settings.fake,
        "max_nodes": settings.max_nodes,
        "max_concurrency": settings.max_concurrency,
        "token_present": settings.token_present,
        "allow_hostname": settings.allow_hostname,
    }
    global _default_logged
    if env is None and not _default_logged:
        logger.debug("Execution node settings loaded", extra=payload)
        _default_logged = True
    else:
        logger.debug("Execution node settings loaded", extra=payload)

    return settings


def execution_nodes_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Return whether the feature flag is on without requiring a token."""
    source = env if env is not None else os.environ
    enabled, _ = _parse_truthy(source.get(ENABLED_ENV), code="execution_nodes_flag_unrecognized")
    return enabled


def execution_node_role(env: Mapping[str, str] | None = None) -> ExecutionNodeRoleSetting:
    """Return the configured role without requiring a token."""
    source = env if env is not None else os.environ
    role_raw = (source.get(ROLE_ENV) or "").strip().lower()
    if role_raw in {"controller", "worker", "both"}:
        return role_raw  # type: ignore[return-value]
    return "controller"


def try_load_execution_node_settings(
    env: Mapping[str, str] | None = None,
) -> ExecutionNodeSettings | ExecutionNodeError:
    """Load settings or return the domain error (does not raise)."""
    try:
        return load_execution_node_settings(env)
    except ExecutionNodeError as exc:
        return exc
