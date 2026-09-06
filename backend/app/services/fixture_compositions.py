"""Load and validate packaged Composition V1 JSON fixtures.

Used by the deterministic fake LLM provider and by tests. Fixtures live under
``app/fixtures/`` (runtime) with mirrors under ``tests/fixtures/``.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from pydantic import ValidationError

from ..schemas import Composition


logger = logging.getLogger(__name__)

FIXTURE_16BAR_MULTITRACK = "composition_v1_16bar_multitrack.json"
FIXTURE_EXPORT_FIDELITY = "composition_v1_export_fidelity.json"
FIXTURE_UNSUPPORTED_INSTRUMENT = "composition_v1_unsupported_instrument.json"
FIXTURE_MINIMAL = "composition_v1_minimal.json"

KNOWN_FIXTURES = (
    FIXTURE_16BAR_MULTITRACK,
    FIXTURE_EXPORT_FIDELITY,
    FIXTURE_UNSUPPORTED_INSTRUMENT,
    FIXTURE_MINIMAL,
)


class FixtureCompositionError(ValueError):
    """Raised when a composition fixture cannot be loaded or validated."""


def fixture_search_roots() -> tuple[Path, ...]:
    """Return fixture directories in preference order (app first, then tests)."""
    backend_root = Path(__file__).resolve().parents[2]
    app_fixtures = Path(__file__).resolve().parents[1] / "fixtures"
    test_fixtures = backend_root / "tests" / "fixtures"
    roots = (app_fixtures, test_fixtures)
    logger.debug(
        "Composition fixture search roots",
        extra={"roots": [str(path) for path in roots]},
    )
    return roots


def resolve_fixture_path(name: str) -> Path:
    """Resolve a fixture filename against known search roots."""
    normalized = Path(name).name
    if not normalized.endswith(".json"):
        raise FixtureCompositionError(f"Fixture name must be a .json file: {name}")

    logger.debug("Resolving composition fixture path", extra={"fixture_name": normalized})
    for root in fixture_search_roots():
        candidate = root / normalized
        if candidate.is_file():
            logger.debug(
                "Resolved composition fixture path",
                extra={"fixture_name": normalized, "path": str(candidate)},
            )
            return candidate

    searched = [str(root / normalized) for root in fixture_search_roots()]
    logger.error(
        "Composition fixture not found",
        extra={"fixture_name": normalized, "searched_paths": searched},
    )
    raise FixtureCompositionError(f"Composition fixture not found: {normalized}")


def load_composition_fixture(name: str) -> Composition:
    """Load a fixture JSON file and validate it as Composition V1."""
    path = resolve_fixture_path(name)
    logger.debug(
        "Loading composition fixture",
        extra={"fixture_name": path.name, "path": str(path)},
    )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.error(
            "Failed to read composition fixture",
            extra={"fixture_name": path.name, "path": str(path), "error_type": type(exc).__name__},
        )
        raise FixtureCompositionError(f"Failed to read fixture {path.name}: {exc}") from exc

    try:
        composition = Composition.model_validate(raw)
    except ValidationError as exc:
        logger.error(
            "Composition fixture failed schema validation",
            extra={"fixture_name": path.name, "error_count": exc.error_count()},
        )
        raise FixtureCompositionError(f"Fixture {path.name} is not valid Composition V1") from exc

    event_count = sum(len(track.events) for track in composition.tracks)
    logger.debug(
        "Loaded composition fixture",
        extra={
            "fixture_name": path.name,
            "path": str(path),
            "bar_count": composition.bar_count,
            "track_count": len(composition.tracks),
            "event_count": event_count,
            "tempo": composition.tempo,
            "time_signature": composition.time_signature,
        },
    )
    return composition


@lru_cache(maxsize=16)
def load_composition_fixture_cached(name: str) -> Composition:
    """Cached fixture load for hot paths (fake LLM generate/edit)."""
    logger.debug("Cached composition fixture load", extra={"fixture_name": name})
    return load_composition_fixture(name)


def clear_fixture_cache() -> None:
    """Clear the fixture LRU cache (tests / hot-reload)."""
    load_composition_fixture_cached.cache_clear()
    logger.debug("Cleared composition fixture cache")
