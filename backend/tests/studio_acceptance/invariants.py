"""Playable-source checks for studio scenarios."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.ardour_exchange_schemas import ArdourExchangeManifestV1
from app.composition_schemas import CompositionV2
from app.services.composition_revision_preserve import events_outside_targets_fingerprint

# V5 product sidecars that must exist (or open empty-safe) after upgrade to head.
# MusicState is session-only — never an Alembic table.
V5_SIDECAR_TABLES: frozenset[str] = frozenset(
    {
        "adaptive_scores",
        "rights_registry_entries",
        "content_provenance_records",
        "execution_nodes",
        "musical_universes",
        "asset_packs",
        "spatial_scenes",
        "performance_plans",
        "model_lab_experiments",
    }
)


def assert_playable_v2(composition: dict[str, Any]) -> None:
    """The working copy is composition.v2 and has at least one note."""
    assert composition.get("schema_version") == "composition.v2"
    notes = [
        event
        for track in composition.get("tracks") or []
        for event in track.get("events") or []
        if event.get("pitch")
    ]
    assert notes
    dumped = json.dumps(composition)
    assert "composition.v3" not in dumped
    assert "composition.v4" not in dumped
    assert "composition.v5" not in dumped


def assert_composition_source_of_truth(
    composition: dict[str, Any] | CompositionV2,
    *,
    session_labeled: bool = False,
) -> None:
    """Audible notes come from composition.v2 events (or an explicitly labeled session).

    Never invents ``composition.v5``. Harmony / plans / MusicState alone are not
    a playable source.
    """
    if isinstance(composition, CompositionV2):
        body = composition.model_dump(mode="json")
    else:
        body = composition
    assert body.get("schema_version") == "composition.v2"
    dumped = json.dumps(body)
    assert "composition.v5" not in dumped
    assert "composition.v4" not in dumped
    assert "adaptive.score.v2" not in dumped
    notes = [
        event
        for track in body.get("tracks") or []
        for event in track.get("events") or []
        if event.get("pitch")
    ]
    if session_labeled:
        return
    assert notes, "composition.v2 must carry note events as the playable source"


def event_fingerprint(
    composition: dict[str, Any] | CompositionV2,
    *,
    ranges: list[dict[str, int]] | None = None,
) -> str:
    """Ordered-event fingerprint. Empty ranges cover the whole score."""
    model = composition if isinstance(composition, CompositionV2) else CompositionV2.model_validate(composition)
    return events_outside_targets_fingerprint(model, affected_ranges=ranges or [])


def melody_pitches(composition: dict[str, Any]) -> list[str]:
    """Ordered pitches of the first track whose role or id names a melody."""
    tracks = composition.get("tracks") or []
    melody = next(
        (
            track
            for track in tracks
            if track.get("role") == "melody" or "melody" in str(track.get("id", ""))
        ),
        tracks[0] if tracks else None,
    )
    assert melody is not None
    return [str(event["pitch"]) for event in melody.get("events") or [] if event.get("pitch")]


def assert_melody_not_copied(generated: dict[str, Any], reference: dict[str, Any]) -> None:
    """Exact ordered pitch lists must differ. A canned collision fails this check."""
    assert melody_pitches(generated) != melody_pitches(reference)


def assert_score_fingerprints_unchanged(
    db_path: Path | str,
    project_id: str,
    *,
    composition_before: str,
    adaptive_body_before: str | None = None,
) -> None:
    """Composition row (and optional adaptive-score body) must match preimages."""
    path = Path(db_path)
    with sqlite3.connect(path) as conn:
        composition = conn.execute(
            "SELECT composition_json FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
        assert composition is not None
        assert composition[0] == composition_before
        if adaptive_body_before is not None:
            adaptive = conn.execute(
                "SELECT body_json FROM adaptive_scores WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            assert adaptive is not None
            assert adaptive[0] == adaptive_body_before


def assert_v5_sidecar_tables(db_path: Path | str) -> None:
    """Head schema exposes V5 sidecar tables. No MusicState / composition.v5 table."""
    path = Path(db_path)
    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    names = {row[0] for row in rows}
    missing = sorted(V5_SIDECAR_TABLES - names)
    assert not missing, f"missing V5 sidecar tables: {missing}"
    assert "music_state" not in names
    assert "adaptive_runtime_music_state" not in names
    assert "composition_v5" not in names


def assert_ardour_corruption_guards() -> None:
    """Schema refuse of session_xml / path escapes — never edits ``.ardour`` XML."""
    base = {
        "schema_version": "ardour.exchange.manifest.v1",
        "package_id": "aex_0123456789abcdef",
        "direction": "inbound",
        "track_name": "Idea",
        "tempo_bpm": 120,
        "time_signature": "4/4",
        "ticks_per_quarter": 480,
        "start_bar": 1,
        "bar_count": 8,
        "start_samples": 0,
        "sample_rate": 48000,
        "material_relpath": "material.mid",
        "source_fingerprint": "abcdef0123456789",
        "created_at": "2026-10-03T12:00:00Z",
    }
    with pytest.raises(ValidationError) as session_xml:
        ArdourExchangeManifestV1.model_validate({**base, "session_xml": "<Session/>"})
    assert "ardour_exchange_forbidden_payload" in str(session_xml.value)
    with pytest.raises(ValidationError) as escape:
        ArdourExchangeManifestV1.model_validate(
            {**base, "audio_relpaths": ["../secret.ardour"]}
        )
    blob = str(escape.value).lower()
    assert "path escape" in blob or "audio_relpaths" in blob or "audio/*.wav" in blob
