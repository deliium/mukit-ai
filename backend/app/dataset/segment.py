"""Segmentation of normalized items into ``dataset.example.v1`` windows."""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from app.composition_schemas import CompositionV2, compile_bar_boundaries
from app.dataset.schemas import (
    DatasetExampleV1,
    DatasetItemV1,
    DatasetSegmentationConfig,
    SegmentationMode,
    TokenProxyFormula,
)
from app.services.composition_timeline import compile_timeline


logger = logging.getLogger(__name__)

_PHRASE_MARKER_HINTS = frozenset({"phrase", "ph", "motif", "theme"})
_MUSICAL_SECTION_TYPES = frozenset(
    {
        "intro",
        "verse",
        "chorus",
        "bridge",
        "outro",
        "solo",
        "break",
        "pre_chorus",
        "instrumental",
        "coda",
        "development",
        "exposition",
        "recapitulation",
        "transition",
        "interlude",
        "hook",
        "refrain",
        "counterphrase",
    }
)


def segment_item(
    item: DatasetItemV1,
    *,
    config: DatasetSegmentationConfig,
    composition: CompositionV2 | None = None,
) -> list[DatasetExampleV1]:
    """Emit examples for configured modes; phrase mode degrades via fallback."""
    doc = composition or item.composition
    if doc is None:
        raise ValueError(f"item {item.item_id} has no composition payload")

    examples: list[DatasetExampleV1] = []
    modes = list(config.modes) or ["bars"]
    for mode in modes:
        if mode == "phrases":
            phrase_examples, warnings = _segment_phrases(item, doc, config)
            if phrase_examples:
                examples.extend(phrase_examples)
            else:
                logger.warning(
                    "phrase_unavailable",
                    extra={
                        "item_id": item.item_id,
                        "fallback": config.phrase_fallback,
                    },
                )
                if config.phrase_fallback == "skip":
                    continue
                fallback_mode: SegmentationMode = (
                    "bars" if config.phrase_fallback == "bars" else "sections"
                )
                examples.extend(
                    _segment_mode(
                        item,
                        doc,
                        fallback_mode,
                        config,
                        extra_warnings=warnings + ["phrase_unavailable"],
                    )
                )
            continue
        examples.extend(_segment_mode(item, doc, mode, config))

    by_mode: dict[str, int] = {}
    for example in examples:
        by_mode[example.segmentation_mode] = by_mode.get(example.segmentation_mode, 0) + 1
    logger.info(
        "Examples emitted",
        extra={"item_id": item.item_id, "examples_emitted": by_mode, "total": len(examples)},
    )
    return examples


def _segment_mode(
    item: DatasetItemV1,
    doc: CompositionV2,
    mode: SegmentationMode,
    config: DatasetSegmentationConfig,
    *,
    extra_warnings: list[str] | None = None,
) -> list[DatasetExampleV1]:
    warnings = list(extra_warnings or [])
    windows: list[tuple[int, int, SegmentationMode]]
    if mode == "bars":
        windows = _bar_windows(doc)
    elif mode == "sections":
        windows = _section_windows(doc, skip_trivial=config.skip_trivial_unsectioned)
    elif mode == "token_limit":
        windows = _token_limit_windows(
            doc,
            token_limit=config.token_limit,
            proxy=config.token_proxy,
        )
    else:
        windows = []

    examples: list[DatasetExampleV1] = []
    for start_tick, end_tick, window_mode in windows:
        sliced = slice_composition_window(doc, start_tick=start_tick, end_tick=end_tick)
        example_id = _example_id(item.item_id, window_mode, start_tick, end_tick)
        examples.append(
            DatasetExampleV1(
                example_id=example_id,
                parent_item_id=item.item_id,
                segmentation_mode=window_mode,
                start_tick=start_tick,
                end_tick=end_tick,
                composition=sliced,
                warnings=list(warnings),
            )
        )
        logger.debug(
            "Example window",
            extra={
                "example_id": example_id,
                "mode": window_mode,
                "start_tick": start_tick,
                "end_tick": end_tick,
            },
        )
    return examples


def _segment_phrases(
    item: DatasetItemV1,
    doc: CompositionV2,
    config: DatasetSegmentationConfig,
) -> tuple[list[DatasetExampleV1], list[str]]:
    windows = _phrase_windows(doc)
    if not windows:
        return [], ["phrase_unavailable"]
    examples = _segment_mode(
        item,
        doc,
        "phrases",
        config,
        extra_warnings=[],
    )
    # Rebuild from phrase windows explicitly
    out: list[DatasetExampleV1] = []
    for start_tick, end_tick, mode in windows:
        sliced = slice_composition_window(doc, start_tick=start_tick, end_tick=end_tick)
        out.append(
            DatasetExampleV1(
                example_id=_example_id(item.item_id, mode, start_tick, end_tick),
                parent_item_id=item.item_id,
                segmentation_mode=mode,
                start_tick=start_tick,
                end_tick=end_tick,
                composition=sliced,
                warnings=[],
            )
        )
    return out, []


def _bar_windows(doc: CompositionV2) -> list[tuple[int, int, SegmentationMode]]:
    timeline = compile_timeline(doc)
    windows: list[tuple[int, int, SegmentationMode]] = []
    for bar in range(1, doc.bar_count + 1):
        start = timeline.bar_start_tick(bar)
        end = timeline.bar_end_tick(bar)
        if end > start:
            windows.append((start, end, "bars"))
    return windows


def _section_windows(
    doc: CompositionV2,
    *,
    skip_trivial: bool,
) -> list[tuple[int, int, SegmentationMode]]:
    sections = list(doc.sections)
    if not sections:
        return [(0, doc.duration_ticks, "sections")]
    trivial = (
        len(sections) == 1
        and sections[0].type == "unsectioned"
        and sections[0].start_tick == 0
        and sections[0].duration_ticks == doc.duration_ticks
    )
    if trivial and skip_trivial:
        return []
    return [
        (section.start_tick, section.start_tick + section.duration_ticks, "sections")
        for section in sections
        if section.duration_ticks > 0
    ]


def _token_limit_windows(
    doc: CompositionV2,
    *,
    token_limit: int,
    proxy: TokenProxyFormula,
) -> list[tuple[int, int, SegmentationMode]]:
    """Pack contiguous bars until projected token proxy >= limit.

    Token proxy is **not** a real BPE tokenizer — note_events or notes+control only.
    """
    timeline = compile_timeline(doc)
    windows: list[tuple[int, int, SegmentationMode]] = []
    pack_start = 0
    pack_proxy = 0
    for bar in range(1, doc.bar_count + 1):
        bar_start = timeline.bar_start_tick(bar)
        bar_end = timeline.bar_end_tick(bar)
        bar_proxy = _window_token_proxy(doc, bar_start, bar_end, proxy)
        if pack_proxy > 0 and pack_proxy + bar_proxy > token_limit:
            windows.append((pack_start, bar_start, "token_limit"))
            pack_start = bar_start
            pack_proxy = 0
        pack_proxy += bar_proxy
        if pack_proxy >= token_limit:
            windows.append((pack_start, bar_end, "token_limit"))
            pack_start = bar_end
            pack_proxy = 0
    if pack_start < doc.duration_ticks and pack_proxy > 0:
        windows.append((pack_start, doc.duration_ticks, "token_limit"))
    if not windows and doc.duration_ticks > 0:
        windows.append((0, doc.duration_ticks, "token_limit"))
    return windows


def _phrase_windows(doc: CompositionV2) -> list[tuple[int, int, SegmentationMode]]:
    # Motif spans when present
    motif_windows: list[tuple[int, int]] = []
    for motif in doc.motifs or []:
        span = _motif_span(doc, motif)
        if span is not None:
            motif_windows.append(span)
    if motif_windows:
        return [(s, e, "phrases") for s, e in _merge_windows(motif_windows)]

    # Phrase-like markers
    phrase_ticks = sorted(
        {
            marker.tick
            for marker in doc.markers
            if marker.kind == "rehearsal"
            or any(hint in marker.label.lower() for hint in _PHRASE_MARKER_HINTS)
        }
    )
    if phrase_ticks:
        ends = phrase_ticks[1:] + [doc.duration_ticks]
        return [
            (start, end, "phrases")
            for start, end in zip(phrase_ticks, ends, strict=True)
            if end > start
        ]

    # Musically typed sections
    musical = [
        (s.start_tick, s.start_tick + s.duration_ticks)
        for s in doc.sections
        if s.type in _MUSICAL_SECTION_TYPES
    ]
    if musical:
        return [(s, e, "phrases") for s, e in musical if e > s]
    return []


def _motif_span(doc: CompositionV2, motif: Any) -> tuple[int, int] | None:
    member_ids: set[str] = set()
    for occurrence in getattr(motif, "occurrences", []) or []:
        for event_id in getattr(occurrence, "event_ids", []) or []:
            member_ids.add(event_id)
    if not member_ids:
        return None
    starts: list[int] = []
    ends: list[int] = []
    for track in doc.tracks:
        for event in track.events:
            if event.id and event.id in member_ids:
                starts.append(event.start_tick)
                ends.append(event.start_tick + event.duration_ticks)
    if not starts:
        return None
    return min(starts), max(ends)


def _merge_windows(windows: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not windows:
        return []
    ordered = sorted(windows)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def _window_token_proxy(
    doc: CompositionV2,
    start_tick: int,
    end_tick: int,
    proxy: TokenProxyFormula,
) -> int:
    notes = 0
    control = 0
    for track in doc.tracks:
        for event in track.events:
            if event.start_tick >= start_tick and event.start_tick < end_tick:
                notes += 1
        if proxy == "notes_plus_control":
            control += sum(
                1
                for mark in track.dynamic_marks
                if start_tick <= mark.tick < end_tick
            )
            control += sum(
                1
                for pedal in track.sustain_pedals
                if pedal.start_tick < end_tick
                and pedal.start_tick + pedal.duration_ticks > start_tick
            )
    return notes if proxy == "note_events" else notes + control


def slice_composition_window(
    composition: CompositionV2,
    *,
    start_tick: int,
    end_tick: int,
) -> CompositionV2:
    """Return a valid mini CompositionV2 for [start_tick, end_tick)."""
    if end_tick <= start_tick:
        raise ValueError("invalid slice window")
    data = composition.model_dump(mode="json")
    window_ticks = end_tick - start_tick

    def shift_tick(tick: int) -> int:
        return max(0, tick - start_tick)

    tracks_out: list[dict[str, Any]] = []
    for track in data["tracks"]:
        events = []
        for event in track.get("events", []):
            ev_start = event["start_tick"]
            ev_end = ev_start + event["duration_ticks"]
            if ev_end <= start_tick or ev_start >= end_tick:
                continue
            clipped_start = max(ev_start, start_tick)
            clipped_end = min(ev_end, end_tick)
            duration = clipped_end - clipped_start
            if duration <= 0:
                continue
            events.append(
                {
                    **event,
                    "start_tick": shift_tick(clipped_start),
                    "duration_ticks": duration,
                    # Drop ties — partial windows break start/stop pair invariants.
                    "tie": None,
                }
            )
        tracks_out.append(
            {
                **track,
                "events": events,
                "dynamic_marks": [
                    {**m, "tick": shift_tick(m["tick"])}
                    for m in track.get("dynamic_marks", [])
                    if start_tick <= m["tick"] < end_tick
                ],
                "sustain_pedals": [
                    {
                        **p,
                        "start_tick": shift_tick(max(p["start_tick"], start_tick)),
                        "duration_ticks": min(p["start_tick"] + p["duration_ticks"], end_tick)
                        - max(p["start_tick"], start_tick),
                    }
                    for p in track.get("sustain_pedals", [])
                    if p["start_tick"] < end_tick
                    and p["start_tick"] + p["duration_ticks"] > start_tick
                ],
                "automation": [],
            }
        )

    # Rebuild a single unsectioned section covering the window on a complete-bar grid.
    meter = data["time_signature"]
    tpq = data["ticks_per_quarter"]
    # Prefer keeping the window duration; pad to complete bars under root meter.
    from app.composition_schemas import bar_duration_ticks

    bar_ticks = bar_duration_ticks(meter, tpq)
    bar_count = max(1, (window_ticks + bar_ticks - 1) // bar_ticks)
    duration_ticks = bar_count * bar_ticks

    data["duration_ticks"] = duration_ticks
    data["bar_count"] = bar_count
    data["tempo_changes"] = [
        {**c, "tick": shift_tick(c["tick"])}
        for c in data.get("tempo_changes", [])
        if start_tick < c["tick"] < end_tick
    ]
    data["time_signature_changes"] = [
        {**c, "tick": shift_tick(c["tick"])}
        for c in data.get("time_signature_changes", [])
        if start_tick < c["tick"] < end_tick
    ]
    data["key_changes"] = [
        {**c, "tick": shift_tick(c["tick"])}
        for c in data.get("key_changes", [])
        if start_tick < c["tick"] < end_tick
    ]
    data["markers"] = [
        {**m, "tick": shift_tick(m["tick"])}
        for m in data.get("markers", [])
        if start_tick <= m["tick"] < end_tick
    ]
    data["harmony"] = [
        {
            **h,
            "start_tick": shift_tick(max(h["start_tick"], start_tick)),
            "duration_ticks": min(h["start_tick"] + h["duration_ticks"], end_tick)
            - max(h["start_tick"], start_tick),
        }
        for h in data.get("harmony", [])
        if h["start_tick"] < end_tick and h["start_tick"] + h["duration_ticks"] > start_tick
    ]
    data["motifs"] = []
    data["sections"] = [
        {
            "id": "slice",
            "type": "unsectioned",
            "label": None,
            "start_bar": 1,
            "bar_count": bar_count,
            "start_tick": 0,
            "duration_ticks": duration_ticks,
        }
    ]
    data["tracks"] = tracks_out
    # Drop empty sustain pedals with non-positive duration after clip
    for track in data["tracks"]:
        track["sustain_pedals"] = [
            p for p in track["sustain_pedals"] if p["duration_ticks"] > 0
        ]
    # Ensure bar boundaries compile (may raise if meter changes invalid)
    compile_bar_boundaries(
        time_signature=meter,
        ticks_per_quarter=tpq,
        bar_count=bar_count,
        duration_ticks=duration_ticks,
        time_signature_changes=[],
    )
    data["time_signature_changes"] = []
    return CompositionV2.model_validate(data)


def _example_id(item_id: str, mode: str, start_tick: int, end_tick: int) -> str:
    material = f"{item_id}|{mode}|{start_tick}|{end_tick}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
