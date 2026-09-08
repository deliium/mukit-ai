"""Test-facing helpers for composition fixtures (normalized to CompositionV2)."""

from __future__ import annotations

import logging

from app.composition_schemas import CompositionV2
from app.services.fixture_compositions import (
    FIXTURE_16BAR_MULTITRACK,
    FIXTURE_EXPORT_FIDELITY,
    FIXTURE_MINIMAL,
    FIXTURE_UNSUPPORTED_INSTRUMENT,
    FIXTURE_V2_EXPRESSIVE,
    FixtureCompositionError,
    load_composition_fixture,
)

logger = logging.getLogger(__name__)

__all__ = [
    "FIXTURE_16BAR_MULTITRACK",
    "FIXTURE_EXPORT_FIDELITY",
    "FIXTURE_MINIMAL",
    "FIXTURE_UNSUPPORTED_INSTRUMENT",
    "FIXTURE_V2_EXPRESSIVE",
    "FixtureCompositionError",
    "load_composition_fixture",
    "load_16bar_multitrack",
    "load_export_fidelity",
    "load_minimal",
    "load_unsupported_instrument",
    "load_v2_expressive",
]


def load_16bar_multitrack() -> CompositionV2:
    logger.debug("Test loading 16-bar multitrack fixture")
    return load_composition_fixture(FIXTURE_16BAR_MULTITRACK)


def load_export_fidelity() -> CompositionV2:
    logger.debug("Test loading export fidelity fixture")
    return load_composition_fixture(FIXTURE_EXPORT_FIDELITY)


def load_unsupported_instrument() -> CompositionV2:
    logger.debug("Test loading unsupported-instrument fixture")
    return load_composition_fixture(FIXTURE_UNSUPPORTED_INSTRUMENT)


def load_minimal() -> CompositionV2:
    logger.debug("Test loading minimal composition fixture")
    return load_composition_fixture(FIXTURE_MINIMAL)


def load_v2_expressive() -> CompositionV2:
    logger.debug("Test loading native V2 expressive fixture")
    return load_composition_fixture(FIXTURE_V2_EXPRESSIVE)
