"""Best-effort git SHA / project version for checkpoint cards."""

from __future__ import annotations

import logging
import os
import subprocess
from typing import Mapping


logger = logging.getLogger(__name__)


def capture_git_commit(*, cwd: str | None = None) -> str | None:
    """Return short git SHA or None when unavailable (offline / no git)."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug(
            "git rev-parse unavailable",
            extra={"error_type": type(exc).__name__},
        )
        return None
    if result.returncode != 0:
        logger.debug("git rev-parse failed", extra={"returncode": result.returncode})
        return None
    sha = (result.stdout or "").strip()
    return sha or None


def capture_project_version(env: Mapping[str, str] | None = None) -> str | None:
    source = env if env is not None else os.environ
    raw = source.get("MUKIT_VERSION")
    if raw is not None and raw.strip():
        return raw.strip()
    return None
