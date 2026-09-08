"""Deterministic Standard MIDI File → ParsedSourceScore conversion.

Uses ``mido`` for SMF format 0/1 with PPQ division. Limits are enforced during
traversal. Unsupported MIDI constructs are counted into import issues via the
shared canonicalizer after parse.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

import mido

from app.import_schemas import CompositionImportError, ImportReport
from app.import_settings import (
    MIDI_HEADER_SIGNATURE,
    ImportSettings,
    load_import_settings,
)
from app.services.composition_import import (
    CanonicalImportResult,
    ParsedSourceScore,
    SourceAutomationLane,
    SourceAutomationPoint,
    SourceKeyChange,
    SourceMarker,
    SourceMeterChange,
    SourceNoteEvent,
    SourceSustainSpan,
    SourceTempoChange,
    SourceTrack,
    canonicalize_source_score,
)
from app.services.import_instruments import gm_program_name


logger = logging.getLogger(__name__)

CC_VOLUME = 7
CC_PAN = 10
CC_EXPRESSION = 11
CC_SUSTAIN = 64

# MIDI key-signature sharps/flats → tonic for major; minor uses relative mapping via mido key strings.
_SHARPS_TO_MAJOR = {
    0: "C",
    1: "G",
    2: "D",
    3: "A",
    4: "E",
    5: "B",
    6: "F#",
    7: "C#",
    -1: "F",
    -2: "Bb",
    -3: "Eb",
    -4: "Ab",
    -5: "Db",
    -6: "Gb",
    -7: "Cb",
}


@dataclass
class _ActiveNote:
    start_tick: int
    velocity: int
    ordinal: int
    segment: int


@dataclass
class _ChannelState:
    program: int | None = None
    segment: int = 0
    volume: int = 100
    pan: int = 0  # V2 pan -64..63; MIDI CC10 stored separately until flush
    pan_midi: int = 64
    expression: int = 127
    sustain_on: bool = False
    sustain_start: int | None = None
    active: dict[int, deque[_ActiveNote]] = field(default_factory=lambda: defaultdict(deque))
    volume_points: list[tuple[int, int]] = field(default_factory=list)
    pan_points: list[tuple[int, int]] = field(default_factory=list)
    expression_points: list[tuple[int, int]] = field(default_factory=list)
    sustains: list[SourceSustainSpan] = field(default_factory=list)
    notes_by_segment: dict[int, list[SourceNoteEvent]] = field(default_factory=lambda: defaultdict(list))
    programs_by_segment: dict[int, int | None] = field(default_factory=dict)
    note_ordinal: int = 0


@dataclass
class _TrackBuild:
    source_track_index: int
    name: str | None = None
    channels: dict[int, _ChannelState] = field(default_factory=dict)


@dataclass(frozen=True)
class MidiParseResult:
    score: ParsedSourceScore
    unsupported_event_count: int
    unmatched_note_off_count: int
    dangling_note_on_count: int
    parse_ms: float


def import_midi_bytes(
    data: bytes,
    *,
    display_filename: str = "upload.mid",
    settings: ImportSettings | None = None,
) -> CanonicalImportResult:
    """Parse MIDI bytes and canonicalize into composition.v2."""
    cfg = settings or load_import_settings()
    parsed = parse_midi_bytes(data, settings=cfg)
    result = canonicalize_source_score(
        parsed.score,
        display_filename=display_filename,
        input_bytes=len(data),
        settings=cfg,
    )
    _attach_midi_parse_issues(result.import_report, parsed)
    logger.info(
        "MIDI import completed",
        extra={
            "status": result.import_report.status,
            "track_count": len(result.composition.tracks),
            "note_count": sum(len(track.events) for track in result.composition.tracks),
            "unsupported_event_count": parsed.unsupported_event_count,
            "parse_ms": round(parsed.parse_ms, 3),
        },
    )
    return result


def parse_midi_bytes(data: bytes, *, settings: ImportSettings | None = None) -> MidiParseResult:
    """Convert SMF bytes into a parser-neutral ``ParsedSourceScore``."""
    cfg = settings or load_import_settings()
    started = time.perf_counter()
    if not data:
        raise CompositionImportError(
            "import_malformed_source",
            "MIDI upload is empty",
            http_status=422,
        )
    if len(data) > cfg.max_upload_bytes:
        raise CompositionImportError(
            "import_payload_too_large",
            "MIDI upload exceeds configured byte limit",
            http_status=413,
            details={"limit_bytes": cfg.max_upload_bytes, "input_bytes": len(data)},
        )
    if not data.startswith(MIDI_HEADER_SIGNATURE):
        raise CompositionImportError(
            "import_unsupported_media_type",
            "Content is not a Standard MIDI File",
            http_status=415,
        )

    try:
        mid = mido.MidiFile(file=BytesIO(data))
    except Exception as exc:  # noqa: BLE001 — mido raises varied parse errors
        logger.error(
            "MIDI parser failed",
            extra={"error_type": type(exc).__name__, "error_code": "import_malformed_source"},
        )
        raise CompositionImportError(
            "import_malformed_source",
            "MIDI file is malformed or truncated",
            http_status=422,
            details={"parser": type(exc).__name__},
        ) from exc

    if not isinstance(mid.ticks_per_beat, int) or mid.ticks_per_beat <= 0:
        raise CompositionImportError(
            "import_malformed_source",
            "MIDI SMPTE division is not supported",
            http_status=422,
            details={"division": "smpte"},
        )
    if mid.type not in {0, 1}:
        raise CompositionImportError(
            "import_malformed_source",
            "Only Standard MIDI File formats 0 and 1 are supported",
            http_status=422,
            details={"smf_type": mid.type},
        )

    source_ppq = int(mid.ticks_per_beat)
    logger.debug(
        "MIDI parse begin",
        extra={
            "smf_type": mid.type,
            "source_ppq": source_ppq,
            "source_track_count": len(mid.tracks),
            "input_bytes": len(data),
        },
    )

    root_tempo: float | None = None
    root_meter: str | None = None
    root_key: str | None = None
    tempo_changes: list[SourceTempoChange] = []
    meter_changes: list[SourceMeterChange] = []
    key_changes: list[SourceKeyChange] = []
    markers: list[SourceMarker] = []
    unsupported = 0
    unmatched_offs = 0
    track_builds: list[_TrackBuild] = []
    content_end = 0
    total_notes = 0

    for track_index, midi_track in enumerate(mid.tracks):
        if len(track_builds) >= cfg.max_tracks and _track_has_channel_messages(midi_track):
            raise CompositionImportError(
                "import_complexity_exceeded",
                "MIDI track count exceeds configured import limit",
                http_status=413,
                details={"limit": cfg.max_tracks},
            )
        build = _TrackBuild(source_track_index=track_index)
        abs_tick = 0
        for message in midi_track:
            abs_tick += int(message.time)
            if abs_tick < 0:
                raise CompositionImportError(
                    "import_malformed_source",
                    "MIDI absolute tick underflow",
                    http_status=422,
                )

            if message.is_meta:
                meta_kind = message.type
                if meta_kind == "track_name":
                    name = (getattr(message, "name", None) or "").strip()
                    if name:
                        build.name = name[:120]
                elif meta_kind == "set_tempo":
                    bpm = 60_000_000.0 / float(message.tempo)
                    if abs_tick == 0 and root_tempo is None:
                        root_tempo = bpm
                    elif abs_tick > 0:
                        tempo_changes.append(SourceTempoChange(tick=abs_tick, bpm=bpm))
                elif meta_kind == "time_signature":
                    meter = f"{int(message.numerator)}/{int(message.denominator)}"
                    if abs_tick == 0 and root_meter is None:
                        root_meter = meter
                    elif abs_tick > 0:
                        meter_changes.append(SourceMeterChange(tick=abs_tick, time_signature=meter))
                elif meta_kind == "key_signature":
                    key = _mido_key_to_v2(getattr(message, "key", "C"))
                    if key is None:
                        unsupported += 1
                    elif abs_tick == 0 and root_key is None:
                        root_key = key
                    elif abs_tick > 0:
                        key_changes.append(SourceKeyChange(tick=abs_tick, key=key))
                elif meta_kind in {"marker", "cue_marker"}:
                    label = (getattr(message, "text", None) or getattr(message, "name", None) or "").strip()
                    if label:
                        markers.append(
                            SourceMarker(
                                tick=abs_tick,
                                kind="rehearsal" if meta_kind == "marker" else "text",
                                label=label[:200],
                            )
                        )
                elif meta_kind == "text":
                    label = (getattr(message, "text", None) or "").strip()
                    if label:
                        markers.append(SourceMarker(tick=abs_tick, kind="text", label=label[:200]))
                elif meta_kind not in {"end_of_track", "sequencer_specific", "midi_port", "channel_prefix"}:
                    unsupported += 1
                continue

            msg_type = message.type
            if msg_type in {"pitchwheel", "aftertouch", "polytouch", "sysex"}:
                unsupported += 1
                continue

            channel = int(getattr(message, "channel", 0)) + 1  # 1..16
            state = build.channels.setdefault(channel, _ChannelState())
            if state.program is None and channel != 10:
                # Default GM piano until an explicit program arrives.
                state.programs_by_segment.setdefault(state.segment, 0)

            if msg_type == "program_change":
                # Split subsequent notes onto a new program segment.
                if state.notes_by_segment.get(state.segment) or state.program is not None:
                    state.segment += 1
                state.program = int(message.program)
                state.programs_by_segment[state.segment] = state.program
                continue

            if msg_type == "control_change":
                control = int(message.control)
                value = int(message.value)
                if control == CC_VOLUME:
                    if abs_tick == 0:
                        state.volume = value
                    else:
                        state.volume_points.append((abs_tick, value))
                elif control == CC_PAN:
                    v2_pan = value - 64
                    if abs_tick == 0:
                        state.pan = v2_pan
                        state.pan_midi = value
                    else:
                        state.pan_points.append((abs_tick, v2_pan))
                elif control == CC_EXPRESSION:
                    if abs_tick == 0:
                        state.expression = value
                    else:
                        state.expression_points.append((abs_tick, value))
                elif control == CC_SUSTAIN:
                    if value >= 64:
                        if not state.sustain_on:
                            state.sustain_on = True
                            state.sustain_start = abs_tick
                    else:
                        if state.sustain_on and state.sustain_start is not None:
                            duration = abs_tick - state.sustain_start
                            if duration > 0:
                                state.sustains.append(
                                    SourceSustainSpan(
                                        start_tick=state.sustain_start,
                                        duration_ticks=duration,
                                    )
                                )
                        state.sustain_on = False
                        state.sustain_start = None
                else:
                    unsupported += 1
                continue

            if msg_type == "note_on" and int(message.velocity) > 0:
                pitch = int(message.note)
                active_count = sum(len(queue) for queue in state.active.values())
                if active_count >= cfg.max_active_notes:
                    raise CompositionImportError(
                        "import_complexity_exceeded",
                        "Active MIDI note count exceeds configured import limit",
                        http_status=413,
                        details={"limit": cfg.max_active_notes},
                    )
                if total_notes >= cfg.max_notes:
                    raise CompositionImportError(
                        "import_complexity_exceeded",
                        "MIDI note count exceeds configured import limit",
                        http_status=413,
                        details={"limit": cfg.max_notes},
                    )
                state.active[pitch].append(
                    _ActiveNote(
                        start_tick=abs_tick,
                        velocity=int(message.velocity),
                        ordinal=state.note_ordinal,
                        segment=state.segment,
                    )
                )
                state.note_ordinal += 1
                state.programs_by_segment.setdefault(state.segment, state.program if state.program is not None else 0)
                continue

            if msg_type == "note_off" or (msg_type == "note_on" and int(message.velocity) == 0):
                pitch = int(message.note)
                queue = state.active.get(pitch)
                if not queue:
                    unmatched_offs += 1
                    continue
                started_note = queue.popleft()
                duration = abs_tick - started_note.start_tick
                if duration <= 0:
                    # Zero-length: omit with dangling policy rather than inventing duration.
                    unmatched_offs += 1
                    continue
                state.notes_by_segment[started_note.segment].append(
                    SourceNoteEvent(
                        onset_tick=started_note.start_tick,
                        duration_ticks=duration,
                        pitch_midi=pitch,
                        velocity=started_note.velocity,
                        source_ordinal=started_note.ordinal,
                    )
                )
                total_notes += 1
                content_end = max(content_end, abs_tick)
                continue

            unsupported += 1

        content_end = max(content_end, abs_tick)
        # Close dangling note-ons at track end (omit with warning).
        dangling = 0
        for state in build.channels.values():
            for pitch, queue in list(state.active.items()):
                while queue:
                    queue.popleft()
                    dangling += 1
            if state.sustain_on and state.sustain_start is not None and abs_tick > state.sustain_start:
                state.sustains.append(
                    SourceSustainSpan(
                        start_tick=state.sustain_start,
                        duration_ticks=abs_tick - state.sustain_start,
                    )
                )
                state.sustain_on = False
                state.sustain_start = None
        unmatched_offs += 0  # keep separate from dangling
        if dangling:
            # counted after loop
            build_dangling = dangling
        else:
            build_dangling = 0
        # Store dangling on a temporary attribute via tuple accumulation
        setattr(build, "_dangling", build_dangling)
        if build.channels or build.name:
            track_builds.append(build)

    dangling_total = sum(int(getattr(build, "_dangling", 0)) for build in track_builds)
    source_tracks = _materialize_tracks(track_builds, cfg=cfg)

    # Deduplicate same-tick conductor events deterministically (last wins later in canonicalize).
    score = ParsedSourceScore(
        format="midi",
        source_ppq=source_ppq,
        content_end_tick=content_end,
        root_tempo=root_tempo,
        root_time_signature=root_meter,
        root_key=root_key,
        tempo_changes=tempo_changes,
        time_signature_changes=meter_changes,
        key_changes=key_changes,
        markers=markers,
        tracks=source_tracks,
    )
    parse_ms = (time.perf_counter() - started) * 1000.0
    logger.debug(
        "MIDI parse finished",
        extra={
            "source_ppq": source_ppq,
            "result_track_count": len(source_tracks),
            "note_count": total_notes,
            "unsupported_event_count": unsupported,
            "unmatched_note_off_count": unmatched_offs,
            "dangling_note_on_count": dangling_total,
            "parse_ms": round(parse_ms, 3),
        },
    )
    logger.info(
        "MIDI parse succeeded",
        extra={
            "smf_type": mid.type,
            "source_ppq": source_ppq,
            "track_count": len(source_tracks),
            "note_count": total_notes,
            "approximation_hint_count": unsupported + unmatched_offs + dangling_total,
        },
    )
    return MidiParseResult(
        score=score,
        unsupported_event_count=unsupported,
        unmatched_note_off_count=unmatched_offs,
        dangling_note_on_count=dangling_total,
        parse_ms=parse_ms,
    )


def _materialize_tracks(builds: list[_TrackBuild], *, cfg: ImportSettings) -> list[SourceTrack]:
    tracks: list[SourceTrack] = []
    for build in builds:
        for channel in sorted(build.channels):
            state = build.channels[channel]
            segments = sorted(set(state.notes_by_segment) | set(state.programs_by_segment) | {0})
            for segment in segments:
                notes = list(state.notes_by_segment.get(segment, []))
                program = state.programs_by_segment.get(segment, state.program)
                # Skip empty non-initial segments without notes and without distinct program metadata.
                if not notes and segment != 0 and program is None:
                    continue
                if not notes and segment != 0 and not state.programs_by_segment:
                    continue
                # Keep empty tracks only when they carry a name/program on segment 0 with no other content —
                # otherwise skip purely empty channel shells.
                if not notes and segment == 0 and len(segments) > 1:
                    # Still emit if later segments exist? Prefer only segments that have notes or are sole segment.
                    if any(state.notes_by_segment.get(other) for other in segments if other != 0):
                        continue
                if not notes and program is None and build.name is None and channel != 10:
                    continue
                if len(tracks) >= cfg.max_tracks:
                    raise CompositionImportError(
                        "import_complexity_exceeded",
                        "Expanded MIDI channel/program track count exceeds configured import limit",
                        http_status=413,
                        details={"limit": cfg.max_tracks},
                    )
                instrument_name = None if channel == 10 else gm_program_name(program if program is not None else 0)
                automation: list[SourceAutomationLane] = []
                if state.volume_points:
                    automation.append(
                        SourceAutomationLane(
                            parameter="volume",
                            interpolation="step",
                            points=[SourceAutomationPoint(tick=t, value=v) for t, v in state.volume_points],
                        )
                    )
                if state.pan_points:
                    automation.append(
                        SourceAutomationLane(
                            parameter="pan",
                            interpolation="step",
                            points=[SourceAutomationPoint(tick=t, value=v) for t, v in state.pan_points],
                        )
                    )
                if state.expression_points:
                    automation.append(
                        SourceAutomationLane(
                            parameter="expression",
                            interpolation="step",
                            points=[SourceAutomationPoint(tick=t, value=v) for t, v in state.expression_points],
                        )
                    )
                tracks.append(
                    SourceTrack(
                        source_track_index=build.source_track_index,
                        channel=channel,
                        program=0 if channel == 10 else (program if program is not None else 0),
                        name=build.name,
                        instrument_name=instrument_name,
                        is_drum=channel == 10,
                        volume=state.volume,
                        pan=state.pan,
                        expression=state.expression,
                        notes=notes,
                        sustain_pedals=list(state.sustains) if segment == segments[0] else [],
                        automation=automation if segment == segments[0] else [],
                        program_segment=segment,
                    )
                )
    return tracks


def _track_has_channel_messages(midi_track: Any) -> bool:
    return any(not message.is_meta for message in midi_track)


def _mido_key_to_v2(key: str) -> str | None:
    raw = (key or "").strip()
    if not raw:
        return None
    # mido uses forms like "C", "Am", "F#m", "Bb"
    if raw.endswith("m"):
        tonic = raw[:-1]
        mode = "minor"
    else:
        tonic = raw
        mode = "major"
    tonic = tonic.replace("♭", "b").replace("♯", "#")
    candidate = f"{tonic} {mode}"
    from app.composition_schemas import KEY_PATTERN

    if KEY_PATTERN.match(candidate):
        return candidate
    return None


def _attach_midi_parse_issues(report: ImportReport, parsed: MidiParseResult) -> None:
    if parsed.unsupported_event_count:
        report.add_issue(
            code="unsupported_midi_event_omitted",
            action="omitted",
            count=parsed.unsupported_event_count,
            severity="warning",
        )
    dangling_and_unmatched = parsed.dangling_note_on_count + parsed.unmatched_note_off_count
    if dangling_and_unmatched:
        report.add_issue(
            code="dangling_note_omitted",
            action="omitted",
            count=dangling_and_unmatched,
            severity="warning",
            details={
                "dangling_note_on_count": parsed.dangling_note_on_count,
                "unmatched_note_off_count": parsed.unmatched_note_off_count,
            },
        )
    # Program-change splits are visible as multiple tracks sharing source_track_index.
    split_count = 0
    by_source: dict[int, int] = defaultdict(int)
    for track in parsed.score.tracks:
        by_source[track.source_track_index] += 1
    split_count = sum(count - 1 for count in by_source.values() if count > 1)
    if split_count:
        report.add_issue(
            code="program_change_split_track",
            action="normalized",
            count=split_count,
            severity="info",
        )
