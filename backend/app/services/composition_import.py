"""Parser-neutral source → strict composition.v2 canonicalization.

Parsers emit ``ParsedSourceScore`` dataclasses; this module applies shared
defaults, PPQ scaling, complete-bar padding, neutral sections, stable IDs,
instrument/role resolution, complexity checks, and final V2 validation.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal, Sequence

from pydantic import ValidationError

from app.composition_schemas import (
    COMPOSITION_SCHEMA_VERSION_V2,
    KEY_PATTERN,
    SUPPORTED_SECTION_TYPES,
    CompositionV2,
    bar_duration_ticks,
    midi_pitch_number,
    normalize_key,
    normalize_time_signature,
    round_half_away_from_zero,
)
from app.import_schemas import (
    CompositionImportError,
    ImportIssueAction,
    ImportIssueCode,
    ImportReport,
    ImportSourceLocator,
    ImportSourceSummary,
    empty_import_report,
)
from app.import_settings import (
    IMPORT_DEFAULT_KEY,
    IMPORT_DEFAULT_TEMPO_BPM,
    IMPORT_DEFAULT_TIME_SIGNATURE,
    IMPORT_NEUTRAL_SECTION_TYPE,
    ImportSettings,
    load_import_settings,
)
from app.services.composition_timeline import compile_timeline
from app.services.import_instruments import resolve_import_instrument


logger = logging.getLogger(__name__)

SourceFormat = Literal["midi", "musicxml", "mxl"]

_SHARP_PC = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_FLAT_PC = ("C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B")
_FLAT_MAJOR_ROOTS = frozenset({"F", "Bb", "Eb", "Ab", "Db", "Gb", "Cb"})
_FLAT_MINOR_ROOTS = frozenset({"D", "G", "C", "F", "Bb", "Eb", "Ab"})
_SECTION_TYPE_ALIASES = {
    "prechorus": "pre_chorus",
    "pre-chorus": "pre_chorus",
    "ending": "outro",
    "end": "outro",
    "coda": "outro",
}
_SAFE_ID_RE = re.compile(r"[^a-zA-Z0-9_-]+")


@dataclass
class SourceNoteEvent:
    onset_tick: int
    duration_ticks: int
    pitch_midi: int
    velocity: int
    source_ordinal: int
    pitch_spelling: str | None = None
    staff: Literal["treble", "bass", "grand"] | None = None
    voice: int | None = None
    articulations: tuple[str, ...] = ()
    tie_type: Literal["start", "continue", "stop"] | None = None
    tie_group_key: str | None = None


@dataclass
class SourceAutomationPoint:
    tick: int
    value: int


@dataclass
class SourceAutomationLane:
    parameter: Literal["volume", "pan", "expression"]
    interpolation: Literal["step", "linear"]
    points: list[SourceAutomationPoint] = field(default_factory=list)


@dataclass
class SourceSustainSpan:
    start_tick: int
    duration_ticks: int


@dataclass
class SourceDynamicMark:
    tick: int
    level: Literal["ppp", "pp", "p", "mp", "mf", "f", "ff", "fff"]


@dataclass
class SourceTrack:
    source_track_index: int
    channel: int | None = None
    program: int | None = None
    name: str | None = None
    instrument_name: str | None = None
    explicit_role: str | None = None
    is_drum: bool = False
    volume: int = 100
    pan: int = 0
    expression: int = 127
    notes: list[SourceNoteEvent] = field(default_factory=list)
    dynamic_marks: list[SourceDynamicMark] = field(default_factory=list)
    sustain_pedals: list[SourceSustainSpan] = field(default_factory=list)
    automation: list[SourceAutomationLane] = field(default_factory=list)
    program_segment: int = 0


@dataclass
class SourceTempoChange:
    tick: int
    bpm: float


@dataclass
class SourceMeterChange:
    tick: int
    time_signature: str


@dataclass
class SourceKeyChange:
    tick: int
    key: str


@dataclass
class SourceMarker:
    tick: int
    kind: Literal["rehearsal", "text"]
    label: str


@dataclass
class SourceSectionHint:
    start_tick: int
    type: str | None = None
    label: str | None = None
    bar_count: int | None = None


@dataclass
class ParsedSourceScore:
    format: SourceFormat
    source_ppq: int
    content_end_tick: int
    source_divisions: int | None = None
    root_tempo: float | None = None
    root_time_signature: str | None = None
    root_key: str | None = None
    tempo_changes: list[SourceTempoChange] = field(default_factory=list)
    time_signature_changes: list[SourceMeterChange] = field(default_factory=list)
    key_changes: list[SourceKeyChange] = field(default_factory=list)
    markers: list[SourceMarker] = field(default_factory=list)
    section_hints: list[SourceSectionHint] = field(default_factory=list)
    tracks: list[SourceTrack] = field(default_factory=list)


@dataclass(frozen=True)
class CanonicalImportResult:
    composition: CompositionV2
    import_report: ImportReport


def canonicalize_source_score(
    score: ParsedSourceScore,
    *,
    display_filename: str,
    input_bytes: int,
    settings: ImportSettings | None = None,
) -> CanonicalImportResult:
    """Convert a parsed source score into validated CompositionV2 + import report."""
    cfg = settings or load_import_settings()
    logger.debug(
        "Canonicalize source score start",
        extra={
            "format": score.format,
            "source_ppq": score.source_ppq,
            "source_track_count": len(score.tracks),
            "source_note_count": sum(len(track.notes) for track in score.tracks),
            "input_bytes": input_bytes,
        },
    )
    _enforce_source_complexity(score, cfg)

    target_ppq = _select_target_ppq(score, cfg)
    scale = target_ppq / float(score.source_ppq)

    summary = ImportSourceSummary(
        detected_format=score.format,
        display_filename=display_filename,
        input_bytes=input_bytes,
        source_ppq=score.source_ppq,
        source_divisions=score.source_divisions,
        target_ppq=target_ppq,
        source_track_count=len(score.tracks),
        result_track_count=0,
        source_note_count=sum(len(track.notes) for track in score.tracks),
        result_note_count=0,
        bar_count=0,
        duration_ticks=0,
    )
    report = empty_import_report(summary=summary)

    if target_ppq != score.source_ppq:
        report.add_issue(
            code="ppq_rescaled",
            action="normalized",
            severity="info",
            details={"source_ppq": score.source_ppq, "target_ppq": target_ppq},
        )

    root_tempo, tempo_changes = _canonicalize_tempo(score, scale, report)
    root_meter, meter_changes = _canonicalize_meter(score, scale, report)
    root_key, key_changes = _canonicalize_key(score, scale, report)

    content_end = max(0, _scale_tick(score.content_end_tick, scale))
    duration_ticks, bar_count, padded = _complete_bar_duration(
        content_end_tick=content_end,
        root_meter=root_meter,
        meter_changes=meter_changes,
        ticks_per_quarter=target_ppq,
        max_bars=cfg.max_bars,
    )
    if padded:
        report.add_issue(
            code="partial_measure_padded",
            action="normalized",
            severity="info",
            details={"content_end_tick": content_end, "duration_ticks": duration_ticks},
        )
    if content_end > 0 and content_end < _first_bar_ticks(root_meter, target_ppq):
        # Content entirely within an incomplete opening bar after scaling.
        report.add_issue(code="pickup_normalized", action="normalized", severity="info")

    markers = _canonicalize_markers(score.markers, scale, duration_ticks, report)
    sections = _canonicalize_sections(
        hints=score.section_hints,
        scale=scale,
        duration_ticks=duration_ticks,
        bar_count=bar_count,
        root_meter=root_meter,
        meter_changes=meter_changes,
        ticks_per_quarter=target_ppq,
        report=report,
    )

    tracks, note_count, id_collisions = _canonicalize_tracks(
        score=score,
        scale=scale,
        target_ppq=target_ppq,
        duration_ticks=duration_ticks,
        root_key=root_key,
        report=report,
        max_notes=cfg.max_notes,
    )
    if id_collisions:
        report.add_issue(
            code="source_id_collision",
            action="normalized",
            count=id_collisions,
            severity="warning",
        )

    payload: dict[str, Any] = {
        "schema_version": COMPOSITION_SCHEMA_VERSION_V2,
        "tempo": root_tempo,
        "key": root_key,
        "time_signature": root_meter,
        "ticks_per_quarter": target_ppq,
        "bar_count": bar_count,
        "duration_ticks": duration_ticks,
        "sections": sections,
        "tracks": tracks,
        "harmony": [],
        "tempo_changes": tempo_changes,
        "time_signature_changes": meter_changes,
        "key_changes": key_changes,
        "markers": markers,
    }

    try:
        composition = CompositionV2.model_validate(payload)
    except ValidationError as exc:
        logger.error(
            "Canonical import failed V2 validation",
            extra={"error_count": exc.error_count(), "format": score.format},
        )
        raise CompositionImportError(
            "import_non_representable",
            "Imported source could not be represented as composition.v2",
            http_status=422,
            details={"validation_errors": exc.error_count()},
        ) from exc

    try:
        timeline = compile_timeline(composition)
    except ValueError as exc:
        logger.error(
            "Canonical import failed timeline compile",
            extra={"error_type": type(exc).__name__, "format": score.format},
        )
        raise CompositionImportError(
            "import_non_representable",
            "Imported meter/tempo map is not representable in composition.v2",
            http_status=422,
            details={"reason": "timeline_compile_failed"},
        ) from exc

    report.summary.result_track_count = len(composition.tracks)
    report.summary.result_note_count = note_count
    report.summary.bar_count = composition.bar_count
    report.summary.duration_ticks = composition.duration_ticks
    report.summary.tempo_change_count = len(composition.tempo_changes)
    report.summary.time_signature_change_count = len(composition.time_signature_changes)
    report.summary.key_change_count = len(composition.key_changes)
    report.summary.marker_count = len(composition.markers)

    logger.info(
        "Canonical import succeeded",
        extra={
            "schema_version": composition.schema_version,
            "track_count": len(composition.tracks),
            "event_count": note_count,
            "bar_count": composition.bar_count,
            "status": report.status,
            "issue_count": len(report.issues),
            "timeline_bars": timeline.bar_count,
        },
    )
    if report.issues:
        logger.warning(
            "Canonical import approximations",
            extra={
                "codes": sorted({issue.code for issue in report.issues}),
                "issue_count": len(report.issues),
                "status": report.status,
            },
        )
    return CanonicalImportResult(composition=composition, import_report=report)


def midi_number_to_pitch(midi: int, *, key: str | None = None) -> str:
    """Deterministic MIDI pitch spelling; key-aware when a source key exists."""
    if midi < 0 or midi > 127:
        raise ValueError("MIDI pitch must be 0..127")
    pc = midi % 12
    octave = (midi // 12) - 1
    names = _FLAT_PC if key and _key_prefers_flats(key) else _SHARP_PC
    return f"{names[pc]}{octave}"


def allocate_stable_id(
    *,
    prefix: str,
    parts: Sequence[Any],
    used: set[str],
    digest_bytes: int = 8,
) -> tuple[str, bool]:
    """Allocate a deterministic ID; returns ``(id, collided)``."""
    digest = hashlib.sha256(
        "|".join(str(part) for part in parts).encode("utf-8")
    ).hexdigest()[:digest_bytes]
    base = _SAFE_ID_RE.sub("-", f"{prefix}-{digest}").strip("-") or f"{prefix}-id"
    candidate = base
    collided = False
    suffix = 2
    while candidate in used:
        collided = True
        candidate = f"{base}-{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate, collided


def _enforce_source_complexity(score: ParsedSourceScore, cfg: ImportSettings) -> None:
    note_count = sum(len(track.notes) for track in score.tracks)
    meta_count = (
        len(score.tempo_changes)
        + len(score.time_signature_changes)
        + len(score.key_changes)
        + len(score.markers)
    )
    if len(score.tracks) > cfg.max_tracks:
        raise CompositionImportError(
            "import_complexity_exceeded",
            "Source track count exceeds configured import limit",
            http_status=413,
            details={"limit": cfg.max_tracks, "count": len(score.tracks)},
        )
    if note_count > cfg.max_notes:
        raise CompositionImportError(
            "import_complexity_exceeded",
            "Source note count exceeds configured import limit",
            http_status=413,
            details={"limit": cfg.max_notes, "count": note_count},
        )
    if meta_count > cfg.max_metadata_changes:
        raise CompositionImportError(
            "import_complexity_exceeded",
            "Source metadata change count exceeds configured import limit",
            http_status=413,
            details={"limit": cfg.max_metadata_changes, "count": meta_count},
        )
    if score.source_ppq < 1 or score.source_ppq > cfg.max_ppq * 4:
        # Allow higher source PPQ before rescale; reject absurd values.
        raise CompositionImportError(
            "import_malformed_source",
            "Source timing resolution is unsupported",
            http_status=422,
            details={"source_ppq": score.source_ppq},
        )


def _select_target_ppq(score: ParsedSourceScore, cfg: ImportSettings) -> int:
    if score.format == "midi" and 1 <= score.source_ppq <= cfg.max_ppq:
        return score.source_ppq
    # MusicXML path may pass a precomputed exact PPQ via source_ppq.
    if 1 <= score.source_ppq <= cfg.max_ppq:
        return score.source_ppq
    return min(cfg.default_target_ppq, cfg.max_ppq)


def _scale_tick(tick: int, scale: float) -> int:
    if scale == 1.0:
        return int(tick)
    return max(0, round_half_away_from_zero(tick * scale))


def _scale_duration(duration: int, scale: float) -> int:
    if duration <= 0:
        return 0
    if scale == 1.0:
        return int(duration)
    return max(1, round_half_away_from_zero(duration * scale))


def _canonicalize_tempo(
    score: ParsedSourceScore,
    scale: float,
    report: ImportReport,
) -> tuple[int, list[dict[str, int]]]:
    root_raw = score.root_tempo
    if root_raw is None:
        root = IMPORT_DEFAULT_TEMPO_BPM
        report.add_issue(code="tempo_defaulted", action="defaulted", severity="info")
    else:
        root, rounded = _coerce_tempo(root_raw)
        if rounded:
            report.add_issue(code="tempo_rounded", action="quantized", severity="info")

    changes: list[dict[str, int]] = []
    seen: set[int] = set()
    for change in sorted(score.tempo_changes, key=lambda item: item.tick):
        tick = _scale_tick(change.tick, scale)
        if tick <= 0:
            continue
        bpm, rounded = _coerce_tempo(change.bpm)
        if rounded:
            report.add_issue(code="tempo_rounded", action="quantized", severity="info", count=1)
        if tick in seen:
            # Deterministic last-write-wins for duplicate same-tick declarations.
            changes = [item for item in changes if item["tick"] != tick]
        seen.add(tick)
        changes.append({"tick": tick, "bpm": bpm})
    return root, changes


def _coerce_tempo(value: float) -> tuple[int, bool]:
    rounded = round_half_away_from_zero(float(value))
    clamped = min(240, max(40, rounded))
    was_rounded = clamped != value
    return clamped, was_rounded


def _canonicalize_meter(
    score: ParsedSourceScore,
    scale: float,
    report: ImportReport,
) -> tuple[str, list[dict[str, Any]]]:
    if score.root_time_signature:
        try:
            root = normalize_time_signature(score.root_time_signature, model_name="import")
        except ValueError as exc:
            raise CompositionImportError(
                "import_non_representable",
                "Source root time signature is not representable",
                http_status=422,
            ) from exc
    else:
        root = IMPORT_DEFAULT_TIME_SIGNATURE
        report.add_issue(code="meter_defaulted", action="defaulted", severity="info")

    changes: list[dict[str, Any]] = []
    seen: set[int] = set()
    for change in sorted(score.time_signature_changes, key=lambda item: item.tick):
        tick = _scale_tick(change.tick, scale)
        if tick <= 0:
            continue
        try:
            meter = normalize_time_signature(change.time_signature, model_name="import")
        except ValueError as exc:
            raise CompositionImportError(
                "import_non_representable",
                "Source time signature change is not representable",
                http_status=422,
            ) from exc
        if tick in seen:
            changes = [item for item in changes if item["tick"] != tick]
        seen.add(tick)
        changes.append({"tick": tick, "time_signature": meter})
    return root, changes


def _canonicalize_key(
    score: ParsedSourceScore,
    scale: float,
    report: ImportReport,
) -> tuple[str, list[dict[str, Any]]]:
    if score.root_key:
        try:
            root = normalize_key(score.root_key, model_name="import")
        except ValueError as exc:
            raise CompositionImportError(
                "import_non_representable",
                "Source root key is not representable",
                http_status=422,
            ) from exc
    else:
        root = IMPORT_DEFAULT_KEY
        report.add_issue(code="key_defaulted", action="defaulted", severity="info")

    changes: list[dict[str, Any]] = []
    seen: set[int] = set()
    for change in sorted(score.key_changes, key=lambda item: item.tick):
        tick = _scale_tick(change.tick, scale)
        if tick <= 0:
            continue
        try:
            key = normalize_key(change.key, model_name="import")
        except ValueError as exc:
            raise CompositionImportError(
                "import_non_representable",
                "Source key change is not representable",
                http_status=422,
            ) from exc
        if tick in seen:
            changes = [item for item in changes if item["tick"] != tick]
        seen.add(tick)
        changes.append({"tick": tick, "key": key})
    return root, changes


def _first_bar_ticks(root_meter: str, ticks_per_quarter: int) -> int:
    return bar_duration_ticks(root_meter, ticks_per_quarter)


def _complete_bar_duration(
    *,
    content_end_tick: int,
    root_meter: str,
    meter_changes: list[dict[str, Any]],
    ticks_per_quarter: int,
    max_bars: int,
) -> tuple[int, int, bool]:
    change_by_tick = {int(item["tick"]): str(item["time_signature"]) for item in meter_changes}
    boundaries = [0]
    active = root_meter
    target = max(content_end_tick, 0)
    while boundaries[-1] < target or len(boundaries) == 1:
        if len(boundaries) - 1 >= max_bars:
            raise CompositionImportError(
                "import_complexity_exceeded",
                "Derived bar count exceeds configured import limit",
                http_status=413,
                details={"limit": max_bars},
            )
        start = boundaries[-1]
        if start in change_by_tick and start != 0:
            active = change_by_tick[start]
        bar_ticks = bar_duration_ticks(active, ticks_per_quarter)
        boundaries.append(start + bar_ticks)

    leftover = sorted(tick for tick in change_by_tick if tick not in boundaries[:-1])
    if leftover:
        raise CompositionImportError(
            "import_non_representable",
            "Meter changes cannot be represented without relocating events",
            http_status=422,
            details={"unaligned_change_count": len(leftover)},
        )

    duration_ticks = boundaries[-1]
    bar_count = len(boundaries) - 1
    padded = duration_ticks != content_end_tick
    return duration_ticks, bar_count, padded


def _canonicalize_markers(
    markers: Sequence[SourceMarker],
    scale: float,
    duration_ticks: int,
    report: ImportReport,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[int, str, str]] = set()
    used_ids: set[str] = set()
    for index, marker in enumerate(sorted(markers, key=lambda item: (item.tick, item.kind, item.label))):
        tick = _scale_tick(marker.tick, scale)
        if tick < 0 or tick > duration_ticks:
            continue
        label = " ".join(marker.label.split())[:200]
        if not label:
            continue
        key = (tick, marker.kind, label)
        if key in seen:
            continue
        seen.add(key)
        marker_id, collided = allocate_stable_id(
            prefix="marker",
            parts=["marker", index, tick, marker.kind, label],
            used=used_ids,
        )
        if collided:
            report.add_issue(code="source_id_collision", action="normalized", count=1)
        result.append({"id": marker_id, "tick": tick, "kind": marker.kind, "label": label})
    return result


def _canonicalize_sections(
    *,
    hints: Sequence[SourceSectionHint],
    scale: float,
    duration_ticks: int,
    bar_count: int,
    root_meter: str,
    meter_changes: list[dict[str, Any]],
    ticks_per_quarter: int,
    report: ImportReport,
) -> list[dict[str, Any]]:
    mapped = _try_map_section_hints(
        hints=hints,
        scale=scale,
        duration_ticks=duration_ticks,
        bar_count=bar_count,
        root_meter=root_meter,
        meter_changes=meter_changes,
        ticks_per_quarter=ticks_per_quarter,
    )
    if mapped is None:
        report.add_issue(code="section_defaulted", action="defaulted", severity="info")
        section_id, _ = allocate_stable_id(
            prefix="section",
            parts=["unsectioned", duration_ticks, bar_count],
            used=set(),
        )
        return [
            {
                "id": section_id,
                "type": IMPORT_NEUTRAL_SECTION_TYPE,
                "label": None,
                "start_bar": 1,
                "bar_count": bar_count,
                "start_tick": 0,
                "duration_ticks": duration_ticks,
            }
        ]
    return mapped


def _try_map_section_hints(
    *,
    hints: Sequence[SourceSectionHint],
    scale: float,
    duration_ticks: int,
    bar_count: int,
    root_meter: str,
    meter_changes: list[dict[str, Any]],
    ticks_per_quarter: int,
) -> list[dict[str, Any]] | None:
    if not hints:
        return None
    # Build bar starts for mapping ticks → bars.
    change_by_tick = {int(item["tick"]): str(item["time_signature"]) for item in meter_changes}
    boundaries = [0]
    active = root_meter
    while len(boundaries) - 1 < bar_count:
        start = boundaries[-1]
        if start in change_by_tick and start != 0:
            active = change_by_tick[start]
        boundaries.append(start + bar_duration_ticks(active, ticks_per_quarter))
    if boundaries[-1] != duration_ticks:
        return None

    ordered = sorted(hints, key=lambda item: item.start_tick)
    sections: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, hint in enumerate(ordered):
        start_tick = _scale_tick(hint.start_tick, scale)
        if start_tick not in boundaries[:-1]:
            return None
        start_bar = boundaries.index(start_tick) + 1
        if index + 1 < len(ordered):
            next_tick = _scale_tick(ordered[index + 1].start_tick, scale)
        else:
            next_tick = duration_ticks
        if next_tick not in boundaries:
            return None
        end_bar_index = boundaries.index(next_tick)
        section_bar_count = end_bar_index - (start_bar - 1)
        if section_bar_count <= 0:
            return None
        section_type = _normalize_section_type(hint.type)
        if section_type is None:
            return None
        section_id, _ = allocate_stable_id(
            prefix="section",
            parts=["hint", index, start_tick, section_type, hint.label or ""],
            used=used_ids,
        )
        sections.append(
            {
                "id": section_id,
                "type": section_type,
                "label": (hint.label.strip()[:200] if hint.label and hint.label.strip() else None),
                "start_bar": start_bar,
                "bar_count": section_bar_count,
                "start_tick": start_tick,
                "duration_ticks": next_tick - start_tick,
            }
        )
    if not sections or sections[0]["start_tick"] != 0:
        return None
    if sections[-1]["start_tick"] + sections[-1]["duration_ticks"] != duration_ticks:
        return None
    return sections


def _normalize_section_type(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    normalized = _SECTION_TYPE_ALIASES.get(normalized, normalized)
    if normalized in SUPPORTED_SECTION_TYPES and normalized != IMPORT_NEUTRAL_SECTION_TYPE:
        return normalized
    return None


def _canonicalize_tracks(
    *,
    score: ParsedSourceScore,
    scale: float,
    target_ppq: int,
    duration_ticks: int,
    root_key: str,
    report: ImportReport,
    max_notes: int,
) -> tuple[list[dict[str, Any]], int, int]:
    tracks_out: list[dict[str, Any]] = []
    used_track_ids: set[str] = set()
    used_event_ids: set[str] = set()
    used_tie_ids: set[str] = set()
    note_count = 0
    id_collisions = 0
    spelling_inferred = 0

    # Stable order: authored track order, then channel/program segment.
    ordered_tracks = list(score.tracks)

    for track in ordered_tracks:
        resolution = resolve_import_instrument(
            source_program=track.program,
            instrument_name=track.instrument_name or track.name,
            explicit_role=track.explicit_role,
            channel=track.channel,
            is_drum=track.is_drum,
        )
        if resolution.instrument_defaulted:
            report.add_issue(
                code="instrument_defaulted",
                action="defaulted",
                severity="info",
                locator=ImportSourceLocator(track_index=track.source_track_index, channel=track.channel),
            )
        if resolution.role_source == "default":
            report.add_issue(
                code="role_defaulted",
                action="defaulted",
                severity="info",
                locator=ImportSourceLocator(track_index=track.source_track_index, channel=track.channel),
            )
        elif resolution.role_source in {"explicit_role", "channel_percussion", "instrument_identity"}:
            if resolution.role_source != "explicit_role":
                report.add_issue(
                    code="role_inferred",
                    action="normalized",
                    severity="info",
                    locator=ImportSourceLocator(track_index=track.source_track_index, channel=track.channel),
                )

        track_id, collided = allocate_stable_id(
            prefix="track",
            parts=[
                score.format,
                track.source_track_index,
                track.channel if track.channel is not None else -1,
                resolution.midi_program,
                track.program_segment,
            ],
            used=used_track_ids,
        )
        if collided:
            id_collisions += 1

        channel = track.channel if track.channel is not None else (10 if resolution.is_drum else 1)
        channel = min(16, max(1, channel))

        events: list[dict[str, Any]] = []
        tie_group_ids: dict[tuple[str, str], str] = {}
        # Sort notes before ID allocation for stability.
        notes = sorted(
            track.notes,
            key=lambda note: (note.onset_tick, note.pitch_midi, note.source_ordinal),
        )
        for note in notes:
            if note_count >= max_notes:
                raise CompositionImportError(
                    "import_complexity_exceeded",
                    "Result note count exceeds configured import limit",
                    http_status=413,
                    details={"limit": max_notes},
                )
            start = _scale_tick(note.onset_tick, scale)
            duration = _scale_duration(note.duration_ticks, scale)
            if duration <= 0:
                continue
            if start >= duration_ticks:
                continue
            if start + duration > duration_ticks:
                duration = duration_ticks - start
            if duration <= 0:
                continue

            if note.pitch_spelling:
                pitch = note.pitch_spelling.strip()
                try:
                    if midi_pitch_number(pitch) != note.pitch_midi:
                        pitch = midi_number_to_pitch(note.pitch_midi, key=root_key)
                        spelling_inferred += 1
                except ValueError:
                    pitch = midi_number_to_pitch(note.pitch_midi, key=root_key)
                    spelling_inferred += 1
            else:
                pitch = midi_number_to_pitch(note.pitch_midi, key=root_key)
                spelling_inferred += 1

            event_id, event_collided = allocate_stable_id(
                prefix="n",
                parts=[
                    score.format,
                    track_id,
                    note.source_ordinal,
                    note.onset_tick,
                    note.pitch_midi,
                ],
                used=used_event_ids,
            )
            if event_collided:
                id_collisions += 1

            event: dict[str, Any] = {
                "id": event_id,
                "type": "note",
                "pitch": pitch,
                "start_tick": start,
                "duration_ticks": duration,
                "velocity": min(127, max(1, int(note.velocity))),
            }
            if note.staff:
                event["staff"] = note.staff
            if note.voice is not None:
                event["voice"] = note.voice
            if note.articulations:
                event["articulations"] = list(dict.fromkeys(note.articulations))
            if note.tie_type and note.tie_group_key:
                # Reuse one group_id for every note in the same source tie chain.
                tie_cache_key = (track_id, note.tie_group_key)
                if tie_cache_key not in tie_group_ids:
                    tie_id, tie_collided = allocate_stable_id(
                        prefix="tie",
                        parts=[track_id, note.tie_group_key],
                        used=used_tie_ids,
                    )
                    if tie_collided:
                        id_collisions += 1
                    tie_group_ids[tie_cache_key] = tie_id
                event["tie"] = {"group_id": tie_group_ids[tie_cache_key], "type": note.tie_type}
            events.append(event)
            note_count += 1

        dynamic_marks = []
        for mark in sorted(track.dynamic_marks, key=lambda item: item.tick):
            tick = _scale_tick(mark.tick, scale)
            if 0 <= tick < duration_ticks:
                dynamic_marks.append({"tick": tick, "level": mark.level})

        sustain_pedals = []
        for pedal in sorted(track.sustain_pedals, key=lambda item: item.start_tick):
            start = _scale_tick(pedal.start_tick, scale)
            duration = _scale_duration(pedal.duration_ticks, scale)
            if duration <= 0 or start >= duration_ticks:
                continue
            if start + duration > duration_ticks:
                duration = duration_ticks - start
            if duration > 0:
                sustain_pedals.append({"start_tick": start, "duration_ticks": duration})

        automation = []
        for lane in track.automation:
            points = []
            for point in sorted(lane.points, key=lambda item: item.tick):
                tick = _scale_tick(point.tick, scale)
                if tick <= 0 or tick >= duration_ticks:
                    continue
                points.append({"tick": tick, "value": int(point.value)})
            # Deduplicate consecutive identical values while keeping strictly increasing ticks.
            deduped: list[dict[str, int]] = []
            for point in points:
                if deduped and deduped[-1]["tick"] == point["tick"]:
                    deduped[-1] = point
                else:
                    deduped.append(point)
            if deduped:
                automation.append(
                    {
                        "parameter": lane.parameter,
                        "interpolation": lane.interpolation,
                        "points": deduped,
                    }
                )

        display_name = (track.name or resolution.instrument or "Track").strip()[:120] or "Track"
        tracks_out.append(
            {
                "id": track_id,
                "name": display_name,
                "instrument": resolution.instrument,
                "role": resolution.role,
                "midi_program": resolution.midi_program,
                "channel": channel,
                "is_drum": resolution.is_drum,
                "volume": min(127, max(0, int(track.volume))),
                "pan": min(63, max(-64, int(track.pan))),
                "expression": min(127, max(0, int(track.expression))),
                "events": events,
                "dynamic_marks": dynamic_marks,
                "sustain_pedals": sustain_pedals,
                "automation": automation,
            }
        )

    if spelling_inferred:
        report.add_issue(
            code="pitch_spelling_inferred",
            action="normalized",
            count=spelling_inferred,
            severity="info",
        )

    return tracks_out, note_count, id_collisions


def _key_prefers_flats(key: str) -> bool:
    if not KEY_PATTERN.match(key):
        return False
    root, mode = key.split()
    if mode == "major":
        return root in _FLAT_MAJOR_ROOTS
    return root in _FLAT_MINOR_ROOTS
