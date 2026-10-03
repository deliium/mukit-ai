"""Bearer gate for ExecutionNode controller and worker peers.

Pure compare. No FastAPI, SQLite, or adaptive-engine imports. When the
feature is enabled, every peer (including loopback) must present the shared
bearer. Query-string tokens never authorize. The raw token and Authorization
header are never logged.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

_LOOPBACK = frozenset({"127.0.0.1", "::1"})
_UNAUTHORIZED = "execution_node_unauthorized"

ExecutionPeerClass = Literal["loopback", "other"]
ExecutionAuthVerdict = Literal["allow", "unauthorized"]


@dataclass(frozen=True)
class ExecutionAuthDecision:
    verdict: ExecutionAuthVerdict
    peer_class: ExecutionPeerClass
    code: str | None = None


def peer_class_for_host(host: str | None) -> ExecutionPeerClass:
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


def authorize_execution_node_request(
    *,
    configured_token: str | None,
    authorization_header: str | None,
    peer_host: str | None,
    query_token_present: bool,
) -> ExecutionAuthDecision:
    """Require a matching bearer for every peer when a token is configured.

    Empty/missing configured token refuses all peers (callers should map
    enabled+empty to ``execution_node_token_missing`` before auth). Query
    tokens and missing peer hosts never authorize.
    """
    peer_class = peer_class_for_host(peer_host)
    refuse = ExecutionAuthDecision(
        verdict="unauthorized",
        peer_class=peer_class,
        code=_UNAUTHORIZED,
    )
    if query_token_present or peer_host is None:
        logger.info(
            "Execution node auth refused",
            extra={"peer_class": peer_class, "code": _UNAUTHORIZED},
        )
        return refuse
    token = None if configured_token is None or configured_token == "" else configured_token
    if token is None:
        logger.info(
            "Execution node auth refused",
            extra={"peer_class": peer_class, "code": _UNAUTHORIZED},
        )
        return refuse
    presented = _presented_bearer(authorization_header)
    if presented is not None and _token_matches(token, presented):
        logger.debug(
            "Execution node auth allowed",
            extra={"peer_class": peer_class},
        )
        return ExecutionAuthDecision(verdict="allow", peer_class=peer_class)
    logger.info(
        "Execution node auth refused",
        extra={"peer_class": peer_class, "code": _UNAUTHORIZED},
    )
    return refuse
