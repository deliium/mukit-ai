"""Preserve-outside-targets checks for scoped revision patches."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Iterable

from app.composition_schemas import CompositionV2

logger = logging.getLogger(__name__)


def _bar_from_tick(tick: int, *, ticks_per_beat: int, beats_per_bar: int) -> int:
    tpb = max(1, int(ticks_per_beat))
    bpb = max(1, int(beats_per_bar))
    bar_ticks = tpb * bpb
    return int(tick) // bar_ticks + 1


def _beats_per_bar(composition: CompositionV2) -> int:
    ts = getattr(composition, "time_signature", None) or "4/4"
    try:
        num = str(ts).split("/", 1)[0]
        return max(1, int(num))
    except (TypeError, ValueError):
        return 4


def _event_identity(event: Any) -> dict[str, Any]:
    return {
        "pitch": getattr(event, "pitch", None),
        "start_tick": getattr(event, "start_tick", None),
        "duration_ticks": getattr(event, "duration_ticks", None),
        "velocity": getattr(event, "velocity", None),
    }


def _in_any_range(bar: int, ranges: Iterable[dict[str, int] | Any]) -> bool:
    for raw in ranges:
        if isinstance(raw, dict):
            start = int(raw.get("start_bar") or 0)
            end = int(raw.get("end_bar") or 0)
        else:
            start = int(getattr(raw, "start_bar", 0) or 0)
            end = int(getattr(raw, "end_bar", 0) or 0)
        if start <= bar <= end:
            return True
    return False


def events_outside_targets_fingerprint(
    composition: CompositionV2,
    *,
    affected_ranges: list[Any] | None = None,
    affected_tracks: list[str] | None = None,
) -> str:
    """Fingerprint events outside targeted bar ranges / tracks.

    When ranges are empty, all events are considered "outside" only if tracks
    filter applies; with both empty, fingerprint covers all events (full preserve).
    """
    ranges = list(affected_ranges or [])
    track_filter = {str(t).strip() for t in (affected_tracks or []) if str(t).strip()}
    tpb = int(
        getattr(composition, "ticks_per_beat", None)
        or getattr(composition, "ticks_per_quarter", None)
        or 480
    )
    bpb = _beats_per_bar(composition)
    rows: list[dict[str, Any]] = []
    for track in composition.tracks:
        tid = str(getattr(track, "id", "") or "")
        # When track filter present, events on targeted tracks inside ranges are
        # excluded from the preserve fingerprint; events on non-targeted tracks
        # are always included.
        for event in track.events:
            bar = _bar_from_tick(
                int(getattr(event, "start_tick", 0) or 0),
                ticks_per_beat=tpb,
                beats_per_bar=bpb,
            )
            targeted_track = (not track_filter) or (tid in track_filter)
            if ranges and targeted_track and _in_any_range(bar, ranges):
                continue
            if track_filter and not ranges and tid in track_filter:
                # Track-only targeting: exclude all events on targeted tracks.
                continue
            rows.append({"track_id": tid, **_event_identity(event)})
    rows.sort(
        key=lambda r: (
            r.get("track_id") or "",
            r.get("start_tick") or 0,
            str(r.get("pitch") or ""),
        )
    )
    blob = json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def assert_preserve_outside_targets(
    before: CompositionV2,
    after: CompositionV2,
    *,
    affected_ranges: list[Any] | None = None,
    affected_tracks: list[str] | None = None,
    preserve_outside_targets: bool = True,
) -> bool:
    """Return True when outside-target events are stable; False on violation."""
    if not preserve_outside_targets:
        logger.debug("Preserve-outside-targets skipped by plan flag")
        return True
    if not affected_ranges and not affected_tracks:
        # No targeting → full-score rewrite allowed; preserve check N/A.
        logger.debug("Preserve-outside-targets N/A (no ranges/tracks)")
        return True
    before_fp = events_outside_targets_fingerprint(
        before, affected_ranges=affected_ranges, affected_tracks=affected_tracks
    )
    after_fp = events_outside_targets_fingerprint(
        after, affected_ranges=affected_ranges, affected_tracks=affected_tracks
    )
    ok = before_fp == after_fp
    if ok:
        logger.info(
            "Preserve-outside-targets ok",
            extra={
                "preserve_ok": True,
                "fingerprint_prefix": before_fp[:12],
                "range_count": len(affected_ranges or []),
                "track_count": len(affected_tracks or []),
            },
        )
    else:
        logger.warning(
            "Preserve-outside-targets violation",
            extra={
                "preserve_ok": False,
                "before_prefix": before_fp[:12],
                "after_prefix": after_fp[:12],
                "range_count": len(affected_ranges or []),
                "track_count": len(affected_tracks or []),
            },
        )
    return ok
