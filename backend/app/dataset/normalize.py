"""Idempotent dataset post-pass on top of import canonicalize."""

from __future__ import annotations

import logging
from typing import Any

from app.composition_schemas import CompositionV2, midi_pitch_number


logger = logging.getLogger(__name__)


def normalize_dataset_composition(
    composition: CompositionV2,
    *,
    target_ppq: int = 480,
    collapse_dup_notes: bool = True,
) -> tuple[CompositionV2, list[str], dict[str, int]]:
    """Apply deterministic dataset IR policies; second call is a no-op."""
    codes: list[str] = []
    counts: dict[str, int] = {}
    doc = composition.model_copy(deep=True)

    if doc.ticks_per_quarter != target_ppq:
        scale = target_ppq / float(doc.ticks_per_quarter)
        doc = _rescale_ppq(doc, target_ppq=target_ppq, scale=scale)
        codes.append("ppq_rescaled")
        counts["ppq_rescaled"] = 1
        logger.info(
            "Dataset PPQ rescaled",
            extra={"target_ppq": target_ppq, "scale": round(scale, 6)},
        )

    if collapse_dup_notes:
        doc, collapsed = _collapse_duplicate_notes(doc)
        if collapsed:
            codes.append("duplicate_notes_collapsed")
            counts["duplicate_notes_collapsed"] = collapsed
            logger.info(
                "Duplicate notes collapsed",
                extra={"duplicate_notes_collapsed": collapsed},
            )

    # Re-validate through CompositionV2 for contract safety.
    validated = CompositionV2.model_validate(doc.model_dump(mode="json"))
    return validated, codes, counts


def _rescale_ppq(composition: CompositionV2, *, target_ppq: int, scale: float) -> CompositionV2:
    data = composition.model_dump(mode="json")
    data["ticks_per_quarter"] = target_ppq
    data["duration_ticks"] = _scale_tick(data["duration_ticks"], scale)
    for change in data.get("tempo_changes", []):
        change["tick"] = _scale_tick(change["tick"], scale)
    for change in data.get("time_signature_changes", []):
        change["tick"] = _scale_tick(change["tick"], scale)
    for change in data.get("key_changes", []):
        change["tick"] = _scale_tick(change["tick"], scale)
    for section in data.get("sections", []):
        section["start_tick"] = _scale_tick(section["start_tick"], scale)
        section["duration_ticks"] = _scale_tick(section["duration_ticks"], scale)
    for marker in data.get("markers", []):
        marker["tick"] = _scale_tick(marker["tick"], scale)
    for item in data.get("harmony", []):
        item["start_tick"] = _scale_tick(item["start_tick"], scale)
        item["duration_ticks"] = _scale_tick(item["duration_ticks"], scale)
    for motif in data.get("motifs", []):
        if "anchor_tick" in motif and motif["anchor_tick"] is not None:
            motif["anchor_tick"] = _scale_tick(motif["anchor_tick"], scale)
    for track in data.get("tracks", []):
        for event in track.get("events", []):
            event["start_tick"] = _scale_tick(event["start_tick"], scale)
            event["duration_ticks"] = max(1, _scale_tick(event["duration_ticks"], scale))
        for lane in track.get("automation", []) or []:
            for point in lane.get("points", []):
                point["tick"] = _scale_tick(point["tick"], scale)
        for span in track.get("sustain_pedals", []) or []:
            span["start_tick"] = _scale_tick(span["start_tick"], scale)
            span["duration_ticks"] = max(1, _scale_tick(span["duration_ticks"], scale))
        for mark in track.get("dynamic_marks", []) or []:
            mark["tick"] = _scale_tick(mark["tick"], scale)
    return CompositionV2.model_validate(data)


def _collapse_duplicate_notes(composition: CompositionV2) -> tuple[CompositionV2, int]:
    data = composition.model_dump(mode="json")
    collapsed = 0
    for track in data.get("tracks", []):
        seen: set[tuple[Any, ...]] = set()
        unique: list[dict[str, Any]] = []
        for event in track.get("events", []):
            key = (
                event["start_tick"],
                event["duration_ticks"],
                midi_pitch_number(event["pitch"]),
                event.get("velocity"),
            )
            if key in seen:
                collapsed += 1
                continue
            seen.add(key)
            unique.append(event)
        track["events"] = unique
    if collapsed == 0:
        return composition, 0
    return CompositionV2.model_validate(data), collapsed


def _scale_tick(value: int, scale: float) -> int:
    # Half-away-from-zero to align with import projection policy.
    scaled = value * scale
    if scaled >= 0:
        return int(scaled + 0.5)
    return int(scaled - 0.5)
