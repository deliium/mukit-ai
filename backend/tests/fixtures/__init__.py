"""Shared Composition V1 JSON fixtures and load helpers for tests."""

from app.services.fixture_compositions import (
    FIXTURE_16BAR_MULTITRACK,
    FIXTURE_EXPORT_FIDELITY,
    FIXTURE_MINIMAL,
    FIXTURE_UNSUPPORTED_INSTRUMENT,
    FixtureCompositionError,
    load_composition_fixture,
)

__all__ = [
    "FIXTURE_16BAR_MULTITRACK",
    "FIXTURE_EXPORT_FIDELITY",
    "FIXTURE_MINIMAL",
    "FIXTURE_UNSUPPORTED_INSTRUMENT",
    "FixtureCompositionError",
    "load_composition_fixture",
]
