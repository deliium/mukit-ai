"""Dynamics and velocity only. Pitch, start, and duration stay put."""

from __future__ import annotations

import hashlib
import logging

from app.composition_schemas import CompositionV2, CompositionV2DynamicMark
from app.services.autonomous_constraints import section_start_tick

logger = logging.getLogger(__name__)

_VELOCITY_SCALE = {"sparse": 0.70, "moderate": 1.00, "dense": 1.15}
_DYNAMIC_LEVEL = {"sparse": "pp", "moderate": "mf", "dense": "ff"}


def pitch_timing_fingerprint(composition: CompositionV2) -> str:
    rows = []
    for track in composition.tracks:
        for event in track.events:
            if getattr(event, "pitch", None) is None:
                continue
            rows.append(
                (
                    track.id,
                    event.pitch,
                    int(event.start_tick),
                    int(event.duration_ticks),
                )
            )
    rows.sort()
    blob = repr(rows).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def realize_expression_marks(
    composition: CompositionV2,
    section_bands: dict[int, str],
) -> CompositionV2:
    """Scale velocity by section density and write one dynamic mark per section start."""
    bands = {
        int(start_bar): band if band in _VELOCITY_SCALE else "moderate"
        for start_bar, band in section_bands.items()
    }
    for section in composition.sections:
        band = bands.get(section.start_bar, "moderate")
        logger.debug(
            "Expression section density",
            extra={"section_id": section.id, "density": band},
        )
    changed = 0
    updated_tracks = []
    for track in composition.tracks:
        events = []
        for note in track.events:
            bar = _bar_for_tick(composition, note.start_tick)
            band = _band_for_bar(composition, bar, bands)
            scale = _VELOCITY_SCALE[band]
            velocity = int(round(int(note.velocity) * scale))
            velocity = max(1, min(127, velocity))
            if velocity != note.velocity:
                changed += 1
            events.append(note.model_copy(update={"velocity": velocity}))
        marks = []
        for section in composition.sections:
            band = bands.get(section.start_bar, "moderate")
            marks.append(
                CompositionV2DynamicMark(
                    tick=section_start_tick(composition, section.start_bar),
                    level=_DYNAMIC_LEVEL[band],
                )
            )
        updated_tracks.append(
            track.model_copy(update={"events": events, "dynamic_marks": marks})
        )
    logger.info(
        "Expression marks realized",
        extra={
            "stage_id": "expression",
            "changed_note_count": changed,
            "dynamic_mark_count": len(composition.sections) * len(composition.tracks),
        },
    )
    return composition.model_copy(update={"tracks": updated_tracks})


def _bar_for_tick(composition: CompositionV2, tick: int) -> int:
    for section in composition.sections:
        start = section_start_tick(composition, section.start_bar)
        if start <= tick < start + section.duration_ticks:
            return section.start_bar
    return composition.sections[-1].start_bar


def _band_for_bar(composition: CompositionV2, bar: int, bands: dict[int, str]) -> str:
    current = "moderate"
    for section in composition.sections:
        if section.start_bar <= bar:
            current = bands.get(section.start_bar, "moderate")
    return current
