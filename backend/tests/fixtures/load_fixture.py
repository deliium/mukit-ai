"""Test-facing helpers for Composition V1 fixtures."""

from __future__ import annotations

import logging

from app.schemas import Composition
from app.services.fixture_compositions import (
    FIXTURE_16BAR_MULTITRACK,
    FIXTURE_EXPORT_FIDELITY,
    FIXTURE_MINIMAL,
    FIXTURE_UNSUPPORTED_INSTRUMENT,
    FixtureCompositionError,
    load_composition_fixture,
)

logger = logging.getLogger(__name__)

__all__ = [
    "FIXTURE_16BAR_MULTITRACK",
    "FIXTURE_EXPORT_FIDELITY",
    "FIXTURE_MINIMAL",
    "FIXTURE_UNSUPPORTED_INSTRUMENT",
    "FixtureCompositionError",
    "load_composition_fixture",
    "load_16bar_multitrack",
    "load_export_fidelity",
    "load_minimal",
    "load_unsupported_instrument",
]


def load_16bar_multitrack() -> Composition:
    logger.debug("Test loading 16-bar multitrack fixture")
    return load_composition_fixture(FIXTURE_16BAR_MULTITRACK)


def load_export_fidelity() -> Composition:
    logger.debug("Test loading export fidelity fixture")
    return load_composition_fixture(FIXTURE_EXPORT_FIDELITY)


def load_unsupported_instrument() -> Composition:
    logger.debug("Test loading unsupported-instrument fixture")
    return load_composition_fixture(FIXTURE_UNSUPPORTED_INSTRUMENT)


def load_minimal() -> Composition:
    logger.debug("Test loading minimal composition fixture")
    return load_composition_fixture(FIXTURE_MINIMAL)
