"""Plugin API version. Host accepts major 1 only."""

from __future__ import annotations

import re

PLUGIN_API_VERSION = "1.0.0"
ACCEPTED_API_MAJOR = 1

_API_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)(?:\.(?P<minor>0|[1-9]\d*))?(?:\.(?P<patch>0|[1-9]\d*))?$"
)


def api_major(value: str) -> int | None:
    """Return the major component of ``1``, ``1.0``, or ``1.0.0``. None if unparsable."""
    if not isinstance(value, str):
        return None
    match = _API_RE.fullmatch(value.strip())
    if match is None:
        return None
    return int(match.group("major"))


def accepts_api_compatibility(value: str) -> bool:
    """True when the manifest API major matches this SDK."""
    return api_major(value) == ACCEPTED_API_MAJOR
