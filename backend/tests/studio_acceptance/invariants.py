"""Playable-source checks for studio scenarios."""

from __future__ import annotations

import json
from typing import Any

from app.composition_schemas import CompositionV2
from app.services.composition_revision_preserve import events_outside_targets_fingerprint


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
