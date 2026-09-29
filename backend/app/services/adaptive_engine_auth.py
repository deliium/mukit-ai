"""Bearer-or-loopback gate for the external adaptive engine.

Pure compare. No FastAPI, SQLite, playback, or model client. The raw token
and the Authorization header are never logged.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

_LOOPBACK = frozenset({"127.0.0.1", "::1"})
_UNAUTHORIZED = "engine_unauthorized"

EnginePeerClass = Literal["loopback", "other"]
EngineAuthVerdict = Literal["allow", "unauthorized"]


@dataclass(frozen=True)
class EngineAuthDecision:
    verdict: EngineAuthVerdict
    peer_class: EnginePeerClass
    code: str | None = None


def peer_class_for_host(host: str | None) -> EnginePeerClass:
    """Classify a direct peer. ``testclient`` and every other host are ``other``."""
    if host in _LOOPBACK:
        return "loopback"
    return "other"


def _presented_bearer(authorization_header: str | None) -> str | None:
    if authorization_header is None:
        return None
    scheme, separator, rest = authorization_header.partition(" ")
    if separator != " " or scheme != "Bearer" or rest == "" or " " in rest:
        return None
    return rest


def _token_matches(expected: str, presented: str) -> bool:
    left = hashlib.sha256(expected.encode("utf-8")).digest()
    right = hashlib.sha256(presented.encode("utf-8")).digest()
    return hmac.compare_digest(left, right)


def authorize_engine_request(
    *,
    configured_token: str | None,
    authorization_header: str | None,
    peer_host: str | None,
    query_token_present: bool,
) -> EngineAuthDecision:
    """Allow loopback only when no token is configured. A query token never matches."""
    peer_class = peer_class_for_host(peer_host)
    token = None if configured_token is None or configured_token == "" else configured_token
    refuse = _refuse(peer_class)
    if query_token_present or peer_host is None:
        logger.info(
            "Adaptive engine auth refused",
            extra={"peer_class": peer_class, "code": _UNAUTHORIZED},
        )
        return refuse
    if token is None:
        if peer_class == "loopback":
            logger.debug("Adaptive engine auth allowed", extra={"peer_class": peer_class})
            return EngineAuthDecision(verdict="allow", peer_class=peer_class)
        logger.info(
            "Adaptive engine auth refused",
            extra={"peer_class": peer_class, "code": _UNAUTHORIZED},
        )
        return refuse
    presented = _presented_bearer(authorization_header)
    if presented is not None and _token_matches(token, presented):
        logger.debug("Adaptive engine auth allowed", extra={"peer_class": peer_class})
        return EngineAuthDecision(verdict="allow", peer_class=peer_class)
    logger.info(
        "Adaptive engine auth refused",
        extra={"peer_class": peer_class, "code": _UNAUTHORIZED},
    )
    return refuse


def _refuse(peer_class: EnginePeerClass) -> EngineAuthDecision:
    return EngineAuthDecision(verdict="unauthorized", peer_class=peer_class, code=_UNAUTHORIZED)
