"""Deterministic composition.v2 → Standard MIDI File projection."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from io import BytesIO
from typing import Literal, Sequence, TypedDict

from app.composition_schemas import (
    CompositionV1,
    CompositionV2,
    CompositionV2NoteEvent,
    CompositionV2Track,
    DYNAMIC_LEVEL_TO_EXPRESSION,
    articulation_gate_ticks,
    articulation_velocity,
    combined_expression,
    midi_pitch_number,
    round_half_away_from_zero,
)
from app.services.composition_migration import migrate_v1_to_v2
from app.services.composition_projection import (
    ProjectionReport,
    empty_projection_report,
    record_motif_metadata_omission,
)
from app.services.composition_timeline import CompiledTimeline, compile_timeline


logger = logging.getLogger(__name__)

KEY_TONIC_TO_SHARPS = {
    "C": 0,
    "G": 1,
    "D": 2,
    "A": 3,
    "E": 4,
    "B": 5,
    "F#": 6,
    "C#": 7,
    "F": -1,
    "Bb": -2,
    "Eb": -3,
    "Ab": -4,
    "Db": -5,
    "Gb": -6,
    "Cb": -7,
}

# Same-tick stable ordering (lower runs first).
ORDER_CONDUCTOR = 0
ORDER_MARKER = 1
ORDER_CONTROL = 2
ORDER_NOTE_OFF = 3
ORDER_NOTE_ON = 4

CC_VOLUME = 7
CC_PAN = 10
CC_EXPRESSION = 11
CC_SUSTAIN = 64

CC_SUB_ORDER = {CC_VOLUME: 0, CC_PAN: 1, CC_EXPRESSION: 2, CC_SUSTAIN: 3}

ConductorKind = Literal["tempo", "meter", "key"]
MarkerKind = Literal["rehearsal", "text"]


class MidiReadyNote(TypedDict):
    pitch: str
    start_tick: int
    duration_ticks: int
    velocity: int


class MidiReadyTrack(TypedDict):
    id: str
    name: str
    instrument: str
    role: str
    midi_program: int
    channel: int
    is_drum: bool
    volume: int
    pan: int
    events: list[MidiReadyNote]


class MidiReadyComposition(TypedDict):
    schema_version: str
    ticks_per_quarter: int
    tempo: int
    duration_ticks: int
    tracks: list[MidiReadyTrack]


class CompositionMidiError(ValueError):
    pass


@dataclass(frozen=True)
class LogicalNote:
    pitch: str
    start_tick: int
    duration_ticks: int
    velocity: int


@dataclass(frozen=True)
class ProjectionEvent:
    tick: int
    order: int
    sub_order: int
    track_index: int | None
    kind: str
    payload: dict


@dataclass(frozen=True)
class MidiRenderResult:
    midi_bytes: bytes
    report: ProjectionReport


def automation_sample_interval(ticks_per_quarter: int) -> int:
    """Maximum spacing between linear automation samples (shared with Tone.js)."""
    return max(1, ticks_per_quarter // 16)


def composition_to_midi_ready(composition: CompositionV1 | CompositionV2) -> MidiReadyComposition:
    if isinstance(composition, CompositionV1):
        composition = migrate_v1_to_v2(composition).composition
    report = empty_projection_report()
    projected = _project_all_tracks(composition, report)
    midi_tracks: list[MidiReadyTrack] = []
    for track, notes in zip(composition.tracks, projected):
        if track.channel < 1 or track.channel > 16 or track.midi_program < 0 or track.midi_program > 127:
            logger.error(
                "Unsupported MIDI track metadata",
                extra={
                    "track_id": track.id,
                    "channel": track.channel,
                    "midi_program": track.midi_program,
                },
            )
            raise CompositionMidiError("Track MIDI channel/program metadata is unsupported")
        midi_events: list[MidiReadyNote] = [
            {
                "pitch": note.pitch,
                "start_tick": note.start_tick,
                "duration_ticks": note.duration_ticks,
                "velocity": note.velocity,
            }
            for note in sorted(notes, key=lambda item: (item.start_tick, item.pitch, item.duration_ticks))
        ]
        midi_tracks.append(
            {
                "id": track.id,
                "name": track.name,
                "instrument": track.instrument,
                "role": track.role,
                "midi_program": track.midi_program,
                "channel": track.channel,
                "is_drum": track.is_drum,
                "volume": track.volume,
                "pan": track.pan,
                "events": midi_events,
            }
        )
    logger.debug(
        "Mapped composition to MIDI-ready structure",
        extra={
            "schema_version": composition.schema_version,
            "track_count": len(midi_tracks),
            "duration_ticks": composition.duration_ticks,
        },
    )
    return {
        "schema_version": composition.schema_version,
        "ticks_per_quarter": composition.ticks_per_quarter,
        "tempo": composition.tempo,
        "duration_ticks": composition.duration_ticks,
        "tracks": midi_tracks,
    }


def render_midi(composition: CompositionV1 | CompositionV2) -> bytes:
    """Render a Standard MIDI File from canonical Composition note events."""
    return render_midi_with_report(composition).midi_bytes


def render_midi_with_report(composition: CompositionV1 | CompositionV2) -> MidiRenderResult:
    if isinstance(composition, CompositionV1):
        composition = migrate_v1_to_v2(composition).composition
    try:
        import mido
    except ImportError as exc:
        raise CompositionMidiError("mido is not installed") from exc

    report = empty_projection_report()
    timeline = compile_timeline(composition)
    event_count = sum(len(track.events) for track in composition.tracks)
    logger.debug(
        "MIDI render started",
        extra={
            "schema_version": composition.schema_version,
            "tempo": composition.tempo,
            "time_signature": composition.time_signature,
            "ticks_per_quarter": composition.ticks_per_quarter,
            "track_count": len(composition.tracks),
            "event_count": event_count,
            "tempo_change_count": len(composition.tempo_changes),
            "marker_count": len(composition.markers),
        },
    )

    try:
        _record_inherent_losses(composition, report)
        record_motif_metadata_omission(composition, report)
        track_notes = _project_all_tracks(composition, report)
        cc_streams = [_build_track_cc_stream(track, composition, timeline, report) for track in composition.tracks]
        assert_shared_channel_program_compatible(composition.tracks)
        _assert_no_channel_control_conflicts(composition.tracks, cc_streams, report)

        stream = _build_projection_stream(composition, timeline, track_notes, cc_streams, report)
        midi_bytes = _encode_midi_file(composition, stream, track_notes, mido)
        logger.debug(
            "MIDI render completed",
            extra={
                "track_count": len(composition.tracks),
                "event_count": event_count,
                "message_count": len(stream),
                "byte_length": len(midi_bytes),
                **report.summary_extra(),
            },
        )
        logger.info(
            "MIDI projection finished",
            extra={
                "byte_length": len(midi_bytes),
                **report.summary_extra(),
            },
        )
        return MidiRenderResult(midi_bytes=midi_bytes, report=report)
    except CompositionMidiError:
        raise
    except Exception as exc:
        logger.error(
            "MIDI render failed",
            extra={
                "error_type": type(exc).__name__,
                "schema_version": composition.schema_version,
                "track_count": len(composition.tracks),
            },
        )
        raise CompositionMidiError("Failed to render MIDI from composition") from exc


def _record_inherent_losses(composition: CompositionV2, report: ProjectionReport) -> None:
    if any(event.tie is not None for track in composition.tracks for event in track.events):
        report.add_issue(code="tie_ids_lost", status="approximated")
    if any(track.events for track in composition.tracks):
        report.add_issue(code="pitch_spelling_lost", status="approximated")


def _project_all_tracks(composition: CompositionV2, report: ProjectionReport) -> list[list[LogicalNote]]:
    projected: list[list[LogicalNote]] = []
    articulation_count = 0
    for track in composition.tracks:
        notes, transformed = _project_track_notes(track)
        projected.append(notes)
        articulation_count += transformed
    if articulation_count:
        report.add_issue(
            code="articulation_transformed",
            status="approximated",
            details={"note_count": articulation_count},
        )
    return projected


def _project_track_notes(track: CompositionV2Track) -> tuple[list[LogicalNote], int]:
    logical = _collapse_tie_chains(track)
    projected: list[LogicalNote] = []
    transformed = 0
    for note in logical:
        gate = articulation_gate_ticks(note.duration_ticks, note.articulations)
        velocity = articulation_velocity(note.velocity, note.articulations)
        if gate != note.duration_ticks or velocity != note.velocity:
            transformed += 1
        projected.append(
            LogicalNote(
                pitch=note.pitch,
                start_tick=note.start_tick,
                duration_ticks=gate,
                velocity=velocity,
            )
        )
    return projected, transformed


@dataclass(frozen=True)
class _CollapsedNote:
    pitch: str
    start_tick: int
    duration_ticks: int
    velocity: int
    articulations: tuple[str, ...]


def _collapse_tie_chains(track: CompositionV2Track) -> list[_CollapsedNote]:
    """Delegate to shared logical-note collapse; preserve MIDI projection fields only."""
    from app.services.composition_logical_notes import collapse_track_tie_chains

    return [
        _CollapsedNote(
            pitch=note.pitch,
            start_tick=note.start_tick,
            duration_ticks=note.duration_ticks,
            velocity=note.velocity,
            articulations=note.articulations,
        )
        for note in collapse_track_tie_chains(track)
    ]


def _build_track_cc_stream(
    track: CompositionV2Track,
    composition: CompositionV2,
    timeline: CompiledTimeline,
    report: ProjectionReport,
) -> list[tuple[int, int, int]]:
    interval = automation_sample_interval(composition.ticks_per_quarter)
    events: list[tuple[int, int, int]] = []

    volume_events = _compile_automation_lane(
        static_value=track.volume,
        lane=next((lane for lane in track.automation if lane.parameter == "volume"), None),
        interval=interval,
        parameter="volume",
        report=report,
    )
    pan_events = _compile_automation_lane(
        static_value=track.pan,
        lane=next((lane for lane in track.automation if lane.parameter == "pan"), None),
        interval=interval,
        parameter="pan",
        report=report,
    )
    expression_lane_events = _compile_automation_lane(
        static_value=track.expression,
        lane=next((lane for lane in track.automation if lane.parameter == "expression"), None),
        interval=interval,
        parameter="expression",
        report=report,
    )

    dynamic_events = _compile_dynamic_expression_events(track, expression_lane_events)
    if track.dynamic_marks:
        report.add_issue(
            code="expression_combined",
            status="approximated",
            path=f"tracks/{track.id}/dynamic_marks",
            details={"mark_count": len(track.dynamic_marks)},
        )

    for tick, value in volume_events:
        events.append((tick, CC_VOLUME, max(0, min(127, value))))
    for tick, value in pan_events:
        events.append((tick, CC_PAN, _pan_to_midi(value)))
    for tick, value in dynamic_events:
        events.append((tick, CC_EXPRESSION, max(0, min(127, value))))

    if track.sustain_pedals:
        report.add_issue(
            code="sustain_projected",
            status="approximated",
            path=f"tracks/{track.id}/sustain_pedals",
            details={"span_count": len(track.sustain_pedals)},
        )
        for pedal in track.sustain_pedals:
            events.append((pedal.start_tick, CC_SUSTAIN, 127))
            events.append((pedal.start_tick + pedal.duration_ticks, CC_SUSTAIN, 0))

    events.sort(key=lambda item: (item[0], CC_SUB_ORDER.get(item[1], 99), item[1]))
    return _dedupe_consecutive_cc_values(events)


def _compile_dynamic_expression_events(
    track: CompositionV2Track,
    expression_lane_events: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    dynamic_points: list[tuple[int, int]] = [(0, 127)]
    for mark in track.dynamic_marks:
        dynamic_points.append((mark.tick, DYNAMIC_LEVEL_TO_EXPRESSION[mark.level]))
    dynamic_points.sort(key=lambda item: item[0])

    change_ticks = sorted({tick for tick, _value in expression_lane_events} | {tick for tick, _value in dynamic_points})
    combined: list[tuple[int, int]] = []
    for tick in change_ticks:
        lane_value = _value_at_tick(expression_lane_events, tick, default=track.expression)
        dynamic_value = _value_at_tick(dynamic_points, tick, default=127)
        combined.append((tick, combined_expression(lane_value, dynamic_value)))
    return _dedupe_consecutive_cc_values(combined, value_index=1)


def _compile_automation_lane(
    *,
    static_value: int,
    lane,
    interval: int,
    parameter: str,
    report: ProjectionReport,
) -> list[tuple[int, int]]:
    if lane is None or not lane.points:
        return [(0, static_value)]

    points = [(0, static_value), *[(point.tick, point.value) for point in lane.points]]
    if lane.interpolation == "step":
        return _dedupe_consecutive_cc_values(points, value_index=1)

    sampled: list[tuple[int, int]] = []
    for index in range(len(points) - 1):
        start_tick, start_value = points[index]
        end_tick, end_value = points[index + 1]
        segment = _sample_linear_segment(start_tick, start_value, end_tick, end_value, interval)
        if index == 0:
            sampled.extend(segment)
        else:
            sampled.extend(segment[1:])
    report.add_issue(
        code="automation_sampled",
        status="approximated",
        details={
            "parameter": parameter,
            "sample_interval_ticks": interval,
            "sample_count": len(sampled),
        },
    )
    return _dedupe_consecutive_cc_values(sampled, value_index=1)


def _sample_linear_segment(
    start_tick: int,
    start_value: int,
    end_tick: int,
    end_value: int,
    interval: int,
) -> list[tuple[int, int]]:
    if end_tick <= start_tick:
        return [(start_tick, round_half_away_from_zero(start_value))]

    samples: list[tuple[int, int]] = [(start_tick, round_half_away_from_zero(start_value))]
    tick = start_tick + interval
    span = end_tick - start_tick
    while tick < end_tick:
        ratio = (tick - start_tick) / span
        value = start_value + ratio * (end_value - start_value)
        samples.append((tick, round_half_away_from_zero(value)))
        tick += interval
    samples.append((end_tick, round_half_away_from_zero(end_value)))
    return samples


def _value_at_tick(points: list[tuple[int, int]], tick: int, *, default: int) -> int:
    value = default
    for point_tick, point_value in points:
        if point_tick <= tick:
            value = point_value
        else:
            break
    return value


def _dedupe_consecutive_cc_values(
    events: list[tuple[int, int, int]] | list[tuple[int, int]],
    *,
    value_index: int = 2,
) -> list:
    if not events:
        return []
    deduped = [events[0]]
    for event in events[1:]:
        previous = deduped[-1]
        if value_index == 1:
            if event[1] == previous[1]:
                continue
        else:
            if event[1] == previous[1] and event[value_index] == previous[value_index]:
                continue
        deduped.append(event)
    return deduped


def assert_shared_channel_program_compatible(
    tracks: Sequence[CompositionV2Track],
) -> None:
    """Reject shared MIDI channels with conflicting programs or drum placement.

    Channel 10 is reserved for drums. Pitched tracks that share a channel must
    use the same ``midi_program``. Callers that also need CC-stream equality
    should use :func:`assert_shared_channel_control_compatible` or MIDI render.
    """
    by_channel: dict[int, list[CompositionV2Track]] = {}
    for track in tracks:
        by_channel.setdefault(int(track.channel), []).append(track)

    for channel, group in by_channel.items():
        if channel == 10:
            non_drums = [track.id for track in group if not track.is_drum]
            if non_drums:
                logger.error(
                    "Non-drum track assigned to reserved drum channel",
                    extra={"channel": channel, "track_count": len(non_drums)},
                )
                raise CompositionMidiError(
                    f"Channel 10 is reserved for drums (conflicting track count={len(non_drums)})"
                )
            continue
        drums = [track.id for track in group if track.is_drum]
        if drums:
            logger.error(
                "Drum track assigned outside channel 10",
                extra={"channel": channel, "track_count": len(drums)},
            )
            raise CompositionMidiError(
                f"Drum tracks must use channel 10 (conflicting track count={len(drums)})"
            )
        if len(group) < 2:
            continue
        programs = {int(track.midi_program) for track in group}
        if len(programs) > 1:
            logger.error(
                "MIDI channel program conflict",
                extra={
                    "channel": channel,
                    "track_count": len(group),
                    "program_count": len(programs),
                },
            )
            raise CompositionMidiError(
                f"Tracks sharing channel {channel} must use the same midi_program "
                f"(found {len(programs)} programs across {len(group)} tracks)"
            )


def tracks_share_channel_controllers(
    left: CompositionV2Track,
    right: CompositionV2Track,
) -> bool:
    """True when static volume/pan/expression and CC-like lanes are compatible for sharing."""
    if (
        left.volume != right.volume
        or left.pan != right.pan
        or left.expression != right.expression
    ):
        return False
    left_pedals = [pedal.model_dump(mode="json") for pedal in left.sustain_pedals]
    right_pedals = [pedal.model_dump(mode="json") for pedal in right.sustain_pedals]
    if left_pedals != right_pedals:
        return False
    left_auto = [lane.model_dump(mode="json") for lane in left.automation]
    right_auto = [lane.model_dump(mode="json") for lane in right.automation]
    return left_auto == right_auto


def assert_shared_channel_control_compatible(
    tracks: Sequence[CompositionV2Track],
    cc_streams: Sequence[Sequence[tuple[int, int, int]]] | None = None,
    *,
    report: ProjectionReport | None = None,
) -> None:
    """Reject shared-channel tracks whose compiled CC streams disagree."""
    if cc_streams is None:
        # Static compatibility only (volume/pan/expression/pedals/automation).
        by_channel: dict[int, list[CompositionV2Track]] = {}
        for track in tracks:
            by_channel.setdefault(int(track.channel), []).append(track)
        for channel, group in by_channel.items():
            if len(group) < 2:
                continue
            reference = group[0]
            for track in group[1:]:
                if not tracks_share_channel_controllers(reference, track):
                    if report is not None:
                        report.add_issue(
                            code="midi_channel_control_conflict",
                            severity="error",
                            status="failed",
                            path=f"tracks/{track.id}",
                            details={"channel": channel, "reference_track_id": reference.id},
                        )
                    logger.error(
                        "MIDI channel control conflict",
                        extra={
                            "channel": channel,
                            "track_id": track.id,
                            "reference_track_id": reference.id,
                        },
                    )
                    raise CompositionMidiError(
                        f"Track-local MIDI controls conflict on channel {channel} "
                        f"(track {track.id} vs {reference.id})"
                    )
        return

    _assert_no_channel_control_conflicts(list(tracks), list(cc_streams), report)


def _assert_no_channel_control_conflicts(
    tracks: list[CompositionV2Track],
    cc_streams: list[list[tuple[int, int, int]]],
    report: ProjectionReport | None,
) -> None:
    by_channel: dict[int, list[tuple[str, list[tuple[int, int, int]]]]] = {}
    for track, stream in zip(tracks, cc_streams):
        by_channel.setdefault(track.channel, []).append((track.id, stream))

    for channel, entries in by_channel.items():
        if len(entries) < 2:
            continue
        reference_id, reference = entries[0]
        for track_id, stream in entries[1:]:
            if stream != reference:
                if report is not None:
                    report.add_issue(
                        code="midi_channel_control_conflict",
                        severity="error",
                        status="failed",
                        path=f"tracks/{track_id}",
                        details={"channel": channel, "reference_track_id": reference_id},
                    )
                logger.error(
                    "MIDI channel control conflict",
                    extra={"channel": channel, "track_id": track_id, "reference_track_id": reference_id},
                )
                raise CompositionMidiError(
                    f"Track-local MIDI controls conflict on channel {channel} "
                    f"(track {track_id} vs {reference_id})"
                )


def _build_projection_stream(
    composition: CompositionV2,
    timeline: CompiledTimeline,
    track_notes: list[list[LogicalNote]],
    cc_streams: list[list[tuple[int, int, int]]],
    report: ProjectionReport,
) -> list[ProjectionEvent]:
    events: list[ProjectionEvent] = []
    events.extend(_conductor_projection_events(composition, timeline, report))
    events.extend(_marker_projection_events(composition, report))

    for track_index, (track, notes, cc_stream) in enumerate(
        zip(composition.tracks, track_notes, cc_streams)
    ):
        for tick, control, value in cc_stream:
            events.append(
                ProjectionEvent(
                    tick=tick,
                    order=ORDER_CONTROL,
                    sub_order=CC_SUB_ORDER.get(control, 99),
                    track_index=track_index,
                    kind="control_change",
                    payload={"control": control, "value": value, "channel": track.channel},
                )
            )
        for note in notes:
            note_number = midi_pitch_number(note.pitch)
            events.append(
                ProjectionEvent(
                    tick=note.start_tick + note.duration_ticks,
                    order=ORDER_NOTE_OFF,
                    sub_order=note_number,
                    track_index=track_index,
                    kind="note_off",
                    payload={"note": note_number, "channel": track.channel},
                )
            )
            events.append(
                ProjectionEvent(
                    tick=note.start_tick,
                    order=ORDER_NOTE_ON,
                    sub_order=note_number,
                    track_index=track_index,
                    kind="note_on",
                    payload={"note": note_number, "velocity": note.velocity, "channel": track.channel},
                )
            )

    events.sort(key=lambda item: (item.tick, item.order, item.sub_order, item.track_index or -1))
    return events


def _conductor_projection_events(
    composition: CompositionV2,
    timeline: CompiledTimeline,
    report: ProjectionReport,
) -> list[ProjectionEvent]:
    import mido

    events: list[ProjectionEvent] = []
    tempo_points: list[tuple[int, int]] = [(0, composition.tempo), *list(timeline.tempo_changes)]
    for tick, bpm in tempo_points:
        tempo_value = mido.bpm2tempo(bpm)
        effective_bpm = round(mido.tempo2bpm(tempo_value))
        if effective_bpm != bpm:
            report.add_issue(
                code="tempo_quantized",
                status="approximated",
                details={"authored_bpm": bpm, "midi_bpm": effective_bpm, "tick": tick},
            )
        events.append(
            ProjectionEvent(
                tick=tick,
                order=ORDER_CONDUCTOR,
                sub_order=0,
                track_index=None,
                kind="set_tempo",
                payload={"tempo": tempo_value, "authored_bpm": bpm},
            )
        )

    meter_points: list[tuple[int, str]] = [(0, composition.time_signature), *list(timeline.time_signature_changes)]
    for tick, signature in meter_points:
        numerator, denominator = (int(part) for part in signature.split("/"))
        events.append(
            ProjectionEvent(
                tick=tick,
                order=ORDER_CONDUCTOR,
                sub_order=1,
                track_index=None,
                kind="time_signature",
                payload={
                    "numerator": numerator,
                    "denominator": denominator,
                    "clocks_per_click": 24,
                    "notated_32nd_notes_per_beat": 8,
                },
            )
        )

    key_points: list[tuple[int, str]] = [(0, composition.key), *list(timeline.key_changes)]
    for tick, key_name in key_points:
        key_sharps, _mode = _midi_key_signature(key_name)
        if key_sharps is None:
            report.add_issue(
                code="key_spelling_unsupported",
                status="approximated",
                details={"key": key_name, "tick": tick},
            )
            continue
        events.append(
            ProjectionEvent(
                tick=tick,
                order=ORDER_CONDUCTOR,
                sub_order=2,
                track_index=None,
                kind="key_signature",
                payload={"key": _mido_key_name(key_name)},
            )
        )
    return events


def _marker_projection_events(composition: CompositionV2, report: ProjectionReport) -> list[ProjectionEvent]:
    if not composition.markers:
        return []
    report.add_issue(
        code="marker_normalized",
        status="approximated",
        details={"marker_count": len(composition.markers)},
    )
    events: list[ProjectionEvent] = []
    for index, marker in enumerate(composition.markers):
        events.append(
            ProjectionEvent(
                tick=marker.tick,
                order=ORDER_MARKER,
                sub_order=0 if marker.kind == "rehearsal" else 1,
                track_index=None,
                kind="marker",
                payload={"marker_kind": marker.kind, "label": marker.label.strip(), "index": index},
            )
        )
    return events


def _encode_midi_file(
    composition: CompositionV2,
    stream: list[ProjectionEvent],
    track_notes: list[list[LogicalNote]],
    mido,
) -> bytes:
    mid = mido.MidiFile(ticks_per_beat=composition.ticks_per_quarter, type=1)

    conductor = mido.MidiTrack()
    mid.tracks.append(conductor)
    conductor.append(mido.MetaMessage("track_name", name="Conductor", time=0))
    conductor_events = [event for event in stream if event.track_index is None]
    _append_delta_messages(conductor, conductor_events, mido, channel=None)

    for track_index, track in enumerate(composition.tracks):
        midi_track = mido.MidiTrack()
        mid.tracks.append(midi_track)
        channel = track.channel - 1
        midi_track.append(mido.MetaMessage("track_name", name=track.name or track.id, time=0))
        if not track.is_drum:
            midi_track.append(mido.Message("program_change", channel=channel, program=track.midi_program, time=0))

        track_events = [event for event in stream if event.track_index == track_index]
        _append_delta_messages(midi_track, track_events, mido, channel=channel)

        last_tick = 0
        if track_events:
            last_tick = max(event.tick for event in track_events)
        if track_notes[track_index]:
            last_tick = max(last_tick, max(note.start_tick + note.duration_ticks for note in track_notes[track_index]))
        end_delta = max(0, composition.duration_ticks - last_tick)
        midi_track.append(mido.MetaMessage("end_of_track", time=end_delta))

    conductor.append(mido.MetaMessage("end_of_track", time=0))

    buffer = BytesIO()
    mid.save(file=buffer)
    return buffer.getvalue()


def _append_delta_messages(track, events: list[ProjectionEvent], mido, *, channel: int | None) -> None:
    cursor = 0
    for event in sorted(events, key=lambda item: (item.tick, item.order, item.sub_order)):
        delta = max(0, event.tick - cursor)
        if event.kind == "set_tempo":
            track.append(mido.MetaMessage("set_tempo", tempo=event.payload["tempo"], time=delta))
        elif event.kind == "time_signature":
            track.append(
                mido.MetaMessage(
                    "time_signature",
                    numerator=event.payload["numerator"],
                    denominator=event.payload["denominator"],
                    clocks_per_click=event.payload["clocks_per_click"],
                    notated_32nd_notes_per_beat=event.payload["notated_32nd_notes_per_beat"],
                    time=delta,
                )
            )
        elif event.kind == "key_signature":
            track.append(mido.MetaMessage("key_signature", key=event.payload["key"], time=delta))
        elif event.kind == "marker":
            meta_type = "marker" if event.payload["marker_kind"] == "rehearsal" else "text"
            track.append(mido.MetaMessage(meta_type, text=event.payload["label"], time=delta))
        elif event.kind == "control_change" and channel is not None:
            track.append(
                mido.Message(
                    "control_change",
                    channel=channel,
                    control=event.payload["control"],
                    value=event.payload["value"],
                    time=delta,
                )
            )
        elif event.kind == "note_on" and channel is not None:
            track.append(
                mido.Message(
                    "note_on",
                    channel=channel,
                    note=event.payload["note"],
                    velocity=event.payload["velocity"],
                    time=delta,
                )
            )
        elif event.kind == "note_off" and channel is not None:
            track.append(
                mido.Message(
                    "note_off",
                    channel=channel,
                    note=event.payload["note"],
                    velocity=0,
                    time=delta,
                )
            )
        cursor = event.tick


def _pan_to_midi(pan: int) -> int:
    return max(0, min(127, pan + 64))


def _midi_key_signature(key_name: str) -> tuple[int | None, str]:
    match = re.match(r"^([A-G][#b]?)\s+(major|minor)$", key_name.strip(), re.IGNORECASE)
    if not match:
        return None, "major"
    tonic = match.group(1)
    mode = match.group(2).lower()
    if mode == "minor":
        relative_major = {
            "A": "C",
            "E": "G",
            "B": "D",
            "F#": "A",
            "C#": "E",
            "G#": "B",
            "D#": "F#",
            "D": "F",
            "G": "Bb",
            "C": "Eb",
            "F": "Ab",
            "Bb": "Db",
            "Eb": "Gb",
        }.get(tonic)
        if relative_major is None:
            return None, mode
        sharps = KEY_TONIC_TO_SHARPS.get(relative_major)
        return sharps, mode
    sharps = KEY_TONIC_TO_SHARPS.get(tonic)
    return sharps, mode


def _mido_key_name(key_name: str) -> str:
    tonic, mode = key_name.split(maxsplit=1)
    if mode.lower() == "minor":
        return f"{tonic}m"
    return tonic
