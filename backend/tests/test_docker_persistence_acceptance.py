"""Opt-in Docker Compose persistence acceptance (delegates to shell script).

Skipped unless ``RUN_DOCKER_ACCEPTANCE=1``. Requires Docker CLI and a working
Compose stack; uses ``LLM_FAKE_MODE`` only (no API credits).
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

import pytest


logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "v1_docker_acceptance.sh"


def _enabled() -> bool:
    return os.getenv("RUN_DOCKER_ACCEPTANCE", "").strip() == "1"


pytestmark = pytest.mark.skipif(
    not _enabled(),
    reason="Opt-in only: set RUN_DOCKER_ACCEPTANCE=1 to run Docker persistence acceptance",
)


def test_docker_compose_persistence_acceptance():
    assert SCRIPT.is_file(), f"Missing acceptance script: {SCRIPT}"
    env = os.environ.copy()
    env["RUN_DOCKER_ACCEPTANCE"] = "1"
    env["LLM_FAKE_MODE"] = "1"
    env.setdefault("COMPOSE_PROJECT_NAME", "mukit-v1-accept-pytest")
    logger.info(
        "Starting Docker persistence acceptance",
        extra={"script": str(SCRIPT), "compose_project": env["COMPOSE_PROJECT_NAME"]},
    )
    completed = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        logger.error(
            "Docker persistence acceptance failed",
            extra={
                "returncode": completed.returncode,
                "stdout_tail": completed.stdout[-2000:],
                "stderr_tail": completed.stderr[-2000:],
            },
        )
        pytest.fail(
            f"Docker acceptance failed ({completed.returncode}):\n"
            f"stdout:\n{completed.stdout[-4000:]}\nstderr:\n{completed.stderr[-4000:]}"
        )
    logger.info("Docker persistence acceptance passed")
    assert "PASS:" in completed.stdout
