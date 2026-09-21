"""Pytest configuration for backend tests."""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "eval_example: deterministic embedding nearest-neighbor fixture ranking "
        "(affinity only; musical_quality_claim=false)",
    )
