"""Partition Composition V2 tracks into neural egress stem roles.

Pure helpers — never mutate the caller's composition dict in place for
filter helpers that return a new view. Never invent playable notes.
"""

from __future__ import annotations

import copy
import logging
import re
from typing import Any

from app.neural_audio_schemas import NeuralAudioBarRange, NeuralAudioStemRole


logger = logging.getLogger(__name__)

_PIANO_RE = re.compile(r"piano|keys|keyboard|rhodes|epiano|harpsichord", re.I)
_STRINGS_RE = re.compile(
    r"string|violin|viola|cello|contrabass|orchestra|ensemble|pizz",
    re.I,
)
_VOCALS_RE = re.compile(r"vocal|voice|choir|singer|aria", re.I)
_BASS_RE = re.compile(r"bass|contrabass|upright", re.I)


def infer_stem_role(track: dict[str, Any]) -> NeuralAudioStemRole:
    """Map a V2 track dict to an egress stem role."""
    if not isinstance(track, dict):
        return "other"
    if track.get("is_drum") is True:
        return "drums"
    role = str(track.get("role") or "").strip().lower()
    if role in {"drums", "percussion"}:
        return "drums"
    if role == "bass":
        return "bass"
    instrument = str(track.get("instrument") or track.get("name") or "")
    if _VOCALS_RE.search(instrument):
        return "vocals"
    if _PIANO_RE.search(instrument):
        return "piano"
    if _STRINGS_RE.search(instrument):
        return "strings"
    if role == "bass" or _BASS_RE.search(instrument):
        return "bass"
    if role in {"melody", "lead", "countermelody"} and _PIANO_RE.search(instrument):
        return "piano"
    return "other"


def partition_tracks_to_stems(
    composition: dict[str, Any],
    *,
    explicit: list[dict[str, Any]] | None = None,
    stem_roles_filter: list[str] | None = None,
) -> list[tuple[NeuralAudioStemRole, list[str]]]:
    """Return ordered (stem_role, track_ids) groups. Empty groups omitted."""
    if explicit:
        groups: list[tuple[NeuralAudioStemRole, list[str]]] = []
        for item in explicit:
            role = str(item.get("stem_role") or "other")
            track_ids = [str(t) for t in (item.get("track_ids") or []) if str(t).strip()]
            if not track_ids:
                continue
            if stem_roles_filter and role not in stem_roles_filter:
                continue
            groups.append((role, track_ids))  # type: ignore[arg-type]
        logger.info(
            "Stem partition from explicit request",
            extra={"group_count": len(groups), "roles": [g[0] for g in groups]},
        )
        return groups

    tracks = composition.get("tracks") if isinstance(composition, dict) else None
    if not isinstance(tracks, list):
        return []

    buckets: dict[str, list[str]] = {}
    order: list[str] = []
    for track in tracks:
        if not isinstance(track, dict):
            continue
        track_id = str(track.get("id") or "").strip()
        if not track_id:
            continue
        role = infer_stem_role(track)
        if stem_roles_filter and role not in stem_roles_filter:
            continue
        if role not in buckets:
            buckets[role] = []
            order.append(role)
        buckets[role].append(track_id)

    groups = [(role, buckets[role]) for role in order if buckets[role]]  # type: ignore[misc]
    logger.info(
        "Stem partition from heuristic",
        extra={
            "group_count": len(groups),
            "roles": [g[0] for g in groups],
            "track_count": sum(len(g[1]) for g in groups),
        },
    )
    return groups


def filter_composition_tracks(
    composition: dict[str, Any],
    track_ids: list[str],
) -> dict[str, Any]:
    """Deep-copy composition keeping only the listed track ids (order preserved)."""
    wanted = {str(t) for t in track_ids}
    view = copy.deepcopy(composition)
    tracks = view.get("tracks")
    if not isinstance(tracks, list):
        view["tracks"] = []
        return view
    filtered = [
        track
        for track in tracks
        if isinstance(track, dict) and str(track.get("id") or "") in wanted
    ]
    view["tracks"] = filtered
    logger.debug(
        "Filtered composition tracks for stem",
        extra={"requested": len(wanted), "kept": len(filtered)},
    )
    return view


def filter_composition_bar_range(
    composition: dict[str, Any],
    bar_range: NeuralAudioBarRange | dict[str, int],
) -> dict[str, Any]:
    """Keep note events whose start_tick falls inside the bar window.

    Symbolic filter only — not sample-accurate audio punch-in.
    """
    from app.composition_schemas import CompositionV2
    from app.services.composition_timeline import compile_timeline

    if isinstance(bar_range, dict):
        start_bar = int(bar_range["start_bar"])
        end_bar = int(bar_range["end_bar"])
    else:
        start_bar = int(bar_range.start_bar)
        end_bar = int(bar_range.end_bar)

    parsed = CompositionV2.model_validate(composition)
    timeline = compile_timeline(parsed)
    start_tick, end_tick = timeline.bar_range_ticks(start_bar, end_bar)

    view = copy.deepcopy(composition)
    tracks = view.get("tracks")
    if not isinstance(tracks, list):
        return view
    kept_events = 0
    for track in tracks:
        if not isinstance(track, dict):
            continue
        events = track.get("events")
        if not isinstance(events, list):
            continue
        filtered_events = []
        for event in events:
            if not isinstance(event, dict):
                continue
            start = int(event.get("start_tick") or 0)
            if start_tick <= start < end_tick:
                filtered_events.append(event)
                kept_events += 1
        track["events"] = filtered_events
    logger.info(
        "Filtered composition to symbolic bar range",
        extra={
            "start_bar": start_bar,
            "end_bar": end_bar,
            "start_tick": start_tick,
            "end_tick": end_tick,
            "kept_events": kept_events,
        },
    )
    return view


def composition_duration_ticks(composition: dict[str, Any]) -> int:
    raw = composition.get("duration_ticks") if isinstance(composition, dict) else None
    if isinstance(raw, int) and raw >= 0:
        return raw
    if isinstance(raw, float) and raw >= 0:
        return int(raw)
    return 0


def stem_capabilities_for_engine(
    *,
    model_id: str | None,
    engine: str,
    fidelity_class: str | None = None,
) -> frozenset[str]:
    """Advertise stem capabilities for the selected engine/model (honesty matrix)."""
    if engine == "fluidsynth" or (fidelity_class == "deterministic" and engine != "neural"):
        return frozenset(
            {
                "per_track",
                "grouped_tracks",
                "section_symbolic_filter",
                "fluidsynth_deterministic",
            }
        )
    mid = str(model_id or "")
    if mid == "fake:neural-audio" or mid.startswith("fake:"):
        return frozenset(
            {
                "direct_stems",
                "per_track",
                "grouped_tracks",
                "section_symbolic_filter",
            }
        )
    if mid.startswith("sidecar:") or mid.startswith("local:midi"):
        return frozenset({"per_track", "grouped_tracks", "section_symbolic_filter"})
    # Default neural path: per/group only (no direct multi-stem claim).
    return frozenset({"per_track", "grouped_tracks", "section_symbolic_filter"})


def resolve_capability_for_partition(
    *,
    capabilities: frozenset[str],
    track_ids: list[str],
    bar_range: NeuralAudioBarRange | None,
    engine: str,
    prefer_direct: bool = False,
) -> str:
    """Pick the capability used for one stem member."""
    if engine == "fluidsynth":
        if "fluidsynth_deterministic" not in capabilities:
            return ""
        if bar_range is not None and "section_symbolic_filter" not in capabilities:
            return ""
        return (
            "section_symbolic_filter"
            if bar_range is not None
            else "fluidsynth_deterministic"
        )
    if prefer_direct and "direct_stems" in capabilities and bar_range is None:
        return "direct_stems"
    if bar_range is not None:
        if "section_symbolic_filter" not in capabilities:
            return ""
        return "section_symbolic_filter"
    if len(track_ids) <= 1 and "per_track" in capabilities:
        return "per_track"
    if len(track_ids) > 1 and "grouped_tracks" in capabilities:
        return "grouped_tracks"
    if len(track_ids) == 1 and "grouped_tracks" in capabilities:
        return "grouped_tracks"
    return ""
