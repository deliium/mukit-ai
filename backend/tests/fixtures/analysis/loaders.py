"""Load analysis fixtures and extract stable semantic vectors."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ANALYSIS_FIXTURE_DIR = Path(__file__).resolve().parent

ANALYSIS_FIXTURE_NAMES = (
    "major_tonality",
    "minor_tonality",
    "modulation",
    "chord_inversions_nct",
    "cadences",
    "motifs",
    "mixed_meter",
    "polyphony_dissonance",
    "range_violation",
    "duplicate_notes",
    "empty_section_track",
    "percussion_only",
    "hygiene_sentinel",
)


def load_analysis_fixture(name: str) -> dict[str, Any]:
    """Return a deep JSON object for an analysis fixture (mutable copy)."""
    path = ANALYSIS_FIXTURE_DIR / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def load_analysis_expected_vectors() -> dict[str, Any]:
    path = ANALYSIS_FIXTURE_DIR / "expected_vectors.json"
    return json.loads(path.read_text(encoding="utf-8"))


def semantic_vector(report: Any) -> dict[str, Any]:
    """Stable semantic projection of an analysis report (not brittle prose)."""
    tonality = report.tonality
    global_key = tonality.global_key.key if tonality.global_key else None
    candidates: list[dict[str, Any]] = []
    if tonality.global_key and tonality.global_key.candidates:
        candidates = [
            {"key": item.key, "score": item.score}
            for item in tonality.global_key.candidates[:4]
        ]
    return {
        "status": report.status,
        "algorithm_version": report.algorithm_version,
        "schema_version": report.schema_version,
        "scope_kind": report.resolved_scope.kind,
        "scope_start_tick": report.resolved_scope.start_tick,
        "scope_end_tick": report.resolved_scope.end_tick,
        "warning_codes": [warning.code for warning in report.warnings],
        "tonality_status": tonality.inference.status,
        "global_key": global_key,
        "key_candidates": candidates,
        "harmony_symbols": [
            span.symbol
            for span in report.harmony.spans
            if span.inference.status == "ok" and span.symbol
        ][:8],
        "harmony_bass_pcs": [
            span.bass_pc
            for span in report.harmony.spans
            if span.inference.status == "ok" and span.symbol
        ][:8],
        "cadence_kinds": [cadence.kind for cadence in report.melody.cadences],
        "motif_kinds": [motif.kind for motif in report.repetition.motifs],
        "motif_transpositions": [
            motif.transposition_semitones
            for motif in report.repetition.motifs
            if motif.kind == "transposed"
        ][:8],
        "max_simultaneity": report.density.metrics.max_simultaneity,
        "tension_status": report.tension.inference.status,
        "fingerprint": report.source_fingerprint,
    }


def permute_object_keys(value: Any) -> Any:
    """Recursively rebuild dicts with reversed key insertion order."""
    if isinstance(value, dict):
        items = list(value.items())
        items.reverse()
        return {key: permute_object_keys(item) for key, item in items}
    if isinstance(value, list):
        return [permute_object_keys(item) for item in value]
    return value
