import logging
import re
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..composition_schemas import CompositionV1, CompositionV2, CompositionV1Track, CompositionV2Track, CompositionV1NoteEvent, CompositionV2NoteEvent
from ..schemas import LLMMusicJson, LLMMusicNoteItem, LLMMusicTrack
from .composition_projection import ProjectionReport, empty_projection_report, record_motif_metadata_omission
from .composition_timeline import CompiledTimeline, compile_timeline


CompositionLike = CompositionV1 | CompositionV2
CompositionTrackLike = CompositionV1Track | CompositionV2Track
NoteEventLike = CompositionV1NoteEvent | CompositionV2NoteEvent


logger = logging.getLogger(__name__)
FLAT_CHORD_PATTERN = re.compile(r"^([A-G])b")
_ARTICULATION_CLASS_NAMES = {
    "staccato": "Staccato",
    "staccatissimo": "Staccatissimo",
    "tenuto": "Tenuto",
    "accent": "Accent",
    "marcato": "StrongAccent",
}


class MusicJsonRenderError(RuntimeError):
    pass


@dataclass(frozen=True)
class _MeasureNoteFragment:
    pitch: str
    offset_ticks: int
    duration_ticks: int
    velocity: int
    tie: str | None  # None | "start" | "continue" | "stop"
    voice: int | None
    articulations: tuple[str, ...] = ()


@dataclass(frozen=True)
class _LogicalNote:
    pitch: str
    start_tick: int
    duration_ticks: int
    velocity: int
    voice: int | None
    staff: str | None
    articulations: tuple[str, ...] = ()


@dataclass
class _NotationContext:
    composition: CompositionLike
    timeline: CompiledTimeline
    harmony_by_bar: dict[int, str]
    section_label_by_bar: dict[int, str] = field(default_factory=dict)
    markers_by_bar: dict[int, list[tuple[str, str]]] = field(default_factory=dict)
    tempo_by_bar: dict[int, list[tuple[int, int]]] = field(default_factory=dict)
    meter_by_bar: dict[int, str] = field(default_factory=dict)
    key_by_bar: dict[int, str] = field(default_factory=dict)
    dynamic_marks_by_track_bar: dict[str, dict[int, list[tuple[int, str]]]] = field(default_factory=dict)
    sustain_pedals_by_track: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    report: ProjectionReport = field(default_factory=empty_projection_report)


def render_musicxml(music: CompositionLike | LLMMusicJson) -> tuple[str, ProjectionReport]:
    if isinstance(music, (CompositionV1, CompositionV2)):
        return _render_canonical_musicxml(music)
    return _render_legacy_musicxml(music)


def _render_canonical_musicxml(composition: CompositionLike) -> tuple[str, ProjectionReport]:
    event_count = sum(len(track.events) for track in composition.tracks)
    logger.info(
        "Canonical MusicXML render started",
        extra={"schema_version": composition.schema_version},
    )
    logger.debug(
        "Canonical MusicXML render metadata",
        extra={
            "schema_version": composition.schema_version,
            "ticks_per_quarter": composition.ticks_per_quarter,
            "tempo": composition.tempo,
            "key": composition.key,
            "time_signature": composition.time_signature,
            "track_count": len(composition.tracks),
            "event_count": event_count,
            "bar_count": composition.bar_count,
            "per_track_event_counts": {track.id: len(track.events) for track in composition.tracks},
        },
    )

    try:
        from music21 import chord, clef, harmony, instrument, key, layout, metadata, meter, note, stream, tempo, tie
    except ImportError as exc:
        raise MusicJsonRenderError("music21 is not installed") from exc

    context = _build_notation_context(composition)
    try:
        score = stream.Score(id="composition_score")
        score.metadata = metadata.Metadata(
            title=f"Composition - {composition.key} - {composition.time_signature}"
        )

        for index, track in enumerate(composition.tracks, start=1):
            if _is_piano_composition_track(track):
                piano_parts = _canonical_piano_parts(
                    chord_module=chord,
                    clef_module=clef,
                    harmony_module=harmony,
                    instrument_module=instrument,
                    key_module=key,
                    meter_module=meter,
                    note_module=note,
                    stream_module=stream,
                    tempo_module=tempo,
                    tie_module=tie,
                    context=context,
                    track=track,
                    track_index=index,
                )
                for piano_part in piano_parts:
                    score.append(piano_part)
                score.insert(
                    0,
                    layout.StaffGroup(
                        piano_parts,
                        name=track.name or track.instrument,
                        symbol="brace",
                        barTogether=True,
                    ),
                )
                continue

            part = stream.Part(id=f"track_{index}_{track.id}")
            part.partName = track.name or track.instrument
            part.insert(0, _instrument_for_composition_track(instrument, track))
            fragments_by_bar = _split_events_into_measure_fragments(
                track.events,
                timeline=context.timeline,
            )
            for bar_number in range(1, composition.bar_count + 1):
                measure = stream.Measure(number=bar_number)
                if bar_number == 1:
                    _insert_staff_metadata_fields(
                        key,
                        meter,
                        tempo,
                        measure,
                        composition.key,
                        composition.time_signature,
                        composition.tempo,
                        include_markings=index == 1,
                    )
                    measure.insert(0, _clef_for_composition_track(clef, track))
                else:
                    _insert_bar_attribute_changes(
                        key,
                        meter,
                        measure,
                        context,
                        bar_number,
                    )
                if index == 1:
                    _append_chord_symbol(harmony, measure, context.harmony_by_bar.get(bar_number), context.report)
                    _insert_visible_part_directions(
                        tempo,
                        measure,
                        context,
                        bar_number,
                        include_markings=True,
                    )
                _insert_track_dynamic_marks(measure, context, track.id, bar_number)
                bar_fragments = fragments_by_bar.get(bar_number, [])
                measure_ql = _bar_measure_quarter_length(context.timeline, bar_number)
                if bar_fragments:
                    _append_canonical_fragments(
                        chord,
                        note,
                        tie,
                        measure,
                        bar_fragments,
                        measure_ql=measure_ql,
                        ticks_per_quarter=context.timeline.ticks_per_quarter,
                        stream_module=stream,
                    )
                else:
                    logger.debug(
                        "Inserted rest for canonical track without events in measure",
                        extra={"track_id": track.id, "bar_number": bar_number},
                    )
                    measure.append(note.Rest(quarterLength=measure_ql))
                part.append(measure)
            _attach_track_pedal_spanners(part, context, track.id)
            score.append(part)

        musicxml = _write_score_musicxml(score)
        logger.info(
            "Canonical MusicXML render completed",
            extra={
                "schema_version": composition.schema_version,
                **context.report.summary_extra(),
                "musicxml_length": len(musicxml),
            },
        )
        return musicxml, context.report
    except MusicJsonRenderError:
        raise
    except Exception as exc:
        logger.error(
            "Canonical MusicXML render failed",
            extra={
                "error_type": type(exc).__name__,
                "schema_version": composition.schema_version,
                "tempo": composition.tempo,
                "key": composition.key,
                "time_signature": composition.time_signature,
                "track_count": len(composition.tracks),
            },
        )
        raise MusicJsonRenderError("Failed to render MusicXML from composition") from exc


def _build_notation_context(composition: CompositionLike) -> _NotationContext:
    timeline = compile_timeline(composition)
    report = empty_projection_report()
    from app.services.composition_harmony_spans import harmony_change_points_by_bar

    harmony_by_bar = harmony_change_points_by_bar(
        composition.harmony,
        boundaries=timeline.bar_boundaries,
        duration_ticks=timeline.duration_ticks,
        bar_count=timeline.bar_count,
    )

    section_label_by_bar: dict[int, str] = {}
    for section in composition.sections:
        label = getattr(section, "label", None)
        if label:
            section_label_by_bar[int(section.start_bar)] = str(label)

    markers_by_bar: dict[int, list[tuple[str, str]]] = {}
    for marker in getattr(composition, "markers", ()) or ():
        bar = timeline.bar_at_tick(int(marker.tick))
        markers_by_bar.setdefault(bar, []).append((str(marker.kind), str(marker.label)))

    tempo_by_bar: dict[int, list[tuple[int, int]]] = {}
    for change in getattr(composition, "tempo_changes", ()) or ():
        tick = int(change.tick)
        bar = timeline.bar_at_tick(tick)
        offset = tick - timeline.bar_start_tick(bar)
        tempo_by_bar.setdefault(bar, []).append((offset, int(change.bpm)))

    meter_by_bar: dict[int, str] = {}
    for change in getattr(composition, "time_signature_changes", ()) or ():
        tick = int(change.tick)
        bar = timeline.bar_at_tick(tick)
        if tick == timeline.bar_start_tick(bar):
            meter_by_bar[bar] = str(change.time_signature)

    key_by_bar: dict[int, str] = {}
    for change in getattr(composition, "key_changes", ()) or ():
        tick = int(change.tick)
        bar = timeline.bar_at_tick(tick)
        if tick == timeline.bar_start_tick(bar):
            key_by_bar[bar] = str(change.key)

    dynamic_marks_by_track_bar: dict[str, dict[int, list[tuple[int, str]]]] = {}
    sustain_pedals_by_track: dict[str, list[tuple[int, int]]] = {}
    if isinstance(composition, CompositionV2):
        for track in composition.tracks:
            if track.automation:
                report.add_issue(
                    code="automation_omitted_from_notation",
                    severity="info",
                    status="omitted",
                    path=f"tracks/{track.id}/automation",
                    details={"lane_count": len(track.automation)},
                )
            per_bar: dict[int, list[tuple[int, str]]] = {}
            for mark in track.dynamic_marks:
                bar = timeline.bar_at_tick(int(mark.tick))
                offset = int(mark.tick) - timeline.bar_start_tick(bar)
                per_bar.setdefault(bar, []).append((offset, str(mark.level)))
            if per_bar:
                dynamic_marks_by_track_bar[track.id] = per_bar
            pedals: list[tuple[int, int]] = []
            for pedal in track.sustain_pedals:
                start = int(pedal.start_tick)
                end = start + int(pedal.duration_ticks)
                pedals.append((start, end))
            if pedals:
                sustain_pedals_by_track[track.id] = pedals
                report.add_issue(
                    code="sustain_projected",
                    severity="info",
                    status="exact",
                    path=f"tracks/{track.id}/sustain_pedals",
                    details={"span_count": len(pedals)},
                )
        record_motif_metadata_omission(composition, report)

    logger.debug(
        "MusicXML notation context compiled",
        extra={
            "bar_count": timeline.bar_count,
            "tempo_change_bars": sorted(tempo_by_bar),
            "meter_change_bars": sorted(meter_by_bar),
            "key_change_bars": sorted(key_by_bar),
            "marker_bars": sorted(markers_by_bar),
            "section_label_bars": sorted(section_label_by_bar),
            **report.summary_extra(),
        },
    )
    return _NotationContext(
        composition=composition,
        timeline=timeline,
        harmony_by_bar=harmony_by_bar,
        section_label_by_bar=section_label_by_bar,
        markers_by_bar=markers_by_bar,
        tempo_by_bar=tempo_by_bar,
        meter_by_bar=meter_by_bar,
        key_by_bar=key_by_bar,
        dynamic_marks_by_track_bar=dynamic_marks_by_track_bar,
        sustain_pedals_by_track=sustain_pedals_by_track,
        report=report,
    )


def _bar_measure_quarter_length(timeline: CompiledTimeline, bar_number: int) -> float:
    bar_ticks = timeline.bar_end_tick(bar_number) - timeline.bar_start_tick(bar_number)
    return bar_ticks / timeline.ticks_per_quarter


def _insert_bar_attribute_changes(
    key_module,
    meter_module,
    measure,
    context: _NotationContext,
    bar_number: int,
) -> None:
    meter = context.meter_by_bar.get(bar_number)
    if meter:
        measure.insert(0, meter_module.TimeSignature(meter))
    key_name = context.key_by_bar.get(bar_number)
    if key_name:
        measure.insert(0, _music21_key(key_module, key_name))


def _insert_visible_part_directions(
    tempo_module,
    measure,
    context: _NotationContext,
    bar_number: int,
    *,
    include_markings: bool,
) -> None:
    if not include_markings:
        return
    tpq = context.timeline.ticks_per_quarter
    label = context.section_label_by_bar.get(bar_number)
    if label:
        from music21 import expressions

        measure.insert(0, expressions.TextExpression(label))
    for kind, text in context.markers_by_bar.get(bar_number, ()):
        from music21 import expressions

        if kind == "rehearsal":
            measure.insert(0, expressions.RehearsalMark(text))
        else:
            measure.insert(0, expressions.TextExpression(text))
    for offset_ticks, bpm in context.tempo_by_bar.get(bar_number, ()):
        measure.insert(offset_ticks / tpq, tempo_module.MetronomeMark(number=bpm))


def _insert_track_dynamic_marks(
    measure,
    context: _NotationContext,
    track_id: str,
    bar_number: int,
) -> None:
    marks = context.dynamic_marks_by_track_bar.get(track_id, {}).get(bar_number, ())
    if not marks:
        return
    from music21 import dynamics

    tpq = context.timeline.ticks_per_quarter
    for offset_ticks, level in marks:
        measure.insert(offset_ticks / tpq, dynamics.Dynamic(level))


def _attach_track_pedal_spanners(part, context: _NotationContext, track_id: str) -> None:
    pedals = context.sustain_pedals_by_track.get(track_id, ())
    if not pedals:
        return
    from music21 import expressions

    timeline = context.timeline
    for start_tick, end_tick in pedals:
        start_element = _find_part_element_near_tick(part, start_tick, timeline)
        end_element = _find_part_element_near_tick(part, max(start_tick, end_tick - 1), timeline)
        if start_element is None or end_element is None:
            logger.debug(
                "Skipped sustain pedal spanner without anchor notes",
                extra={"track_id": track_id, "start_tick": start_tick, "end_tick": end_tick},
            )
            continue
        part.append(expressions.PedalMark(start_element, end_element))


def _find_part_element_near_tick(part, tick: int, timeline: CompiledTimeline):
    from music21 import chord as m21_chord
    from music21 import note as m21_note

    best = None
    best_distance = None
    for element in part.recurse().notes:
        if isinstance(element, (m21_note.Note, m21_chord.Chord)):
            element_tick = int(round(float(element.getOffsetInHierarchy(part)) * timeline.ticks_per_quarter))
            distance = abs(element_tick - tick)
            if best_distance is None or distance < best_distance:
                best = element
                best_distance = distance
    return best


def _logical_notes_from_events(events: Sequence[NoteEventLike]) -> list[_LogicalNote]:
    chains: dict[str, list[NoteEventLike]] = {}
    logical: list[_LogicalNote] = []
    for event in events:
        tie = getattr(event, "tie", None)
        if tie is None:
            logical.append(
                _LogicalNote(
                    pitch=event.pitch,
                    start_tick=event.start_tick,
                    duration_ticks=event.duration_ticks,
                    velocity=event.velocity,
                    voice=event.voice,
                    staff=event.staff,
                    articulations=tuple(getattr(event, "articulations", ()) or ()),
                )
            )
            continue
        group_id = tie.group_id
        if tie.type == "start":
            chains[group_id] = [event]
        elif group_id in chains:
            chains[group_id].append(event)
        if tie.type == "stop" and group_id in chains:
            ordered = chains.pop(group_id)
            head = ordered[0]
            logical.append(
                _LogicalNote(
                    pitch=head.pitch,
                    start_tick=head.start_tick,
                    duration_ticks=sum(item.duration_ticks for item in ordered),
                    velocity=head.velocity,
                    voice=head.voice,
                    staff=head.staff,
                    articulations=tuple(getattr(head, "articulations", ()) or ()),
                )
            )
    for leftover in chains.values():
        head = leftover[0]
        logical.append(
            _LogicalNote(
                pitch=head.pitch,
                start_tick=head.start_tick,
                duration_ticks=sum(item.duration_ticks for item in leftover),
                velocity=head.velocity,
                voice=head.voice,
                staff=head.staff,
                articulations=tuple(getattr(head, "articulations", ()) or ()),
            )
        )
    return sorted(logical, key=lambda note: (note.start_tick, note.pitch))


def _split_events_into_measure_fragments(
    events: Sequence[NoteEventLike],
    *,
    timeline: CompiledTimeline,
) -> dict[int, list[_MeasureNoteFragment]]:
    fragments_by_bar: dict[int, list[_MeasureNoteFragment]] = {
        bar: [] for bar in range(1, timeline.bar_count + 1)
    }
    for logical in _logical_notes_from_events(events):
        note_end = logical.start_tick + logical.duration_ticks
        first_segment = True
        for bar_number in range(1, timeline.bar_count + 1):
            bar_start = timeline.bar_start_tick(bar_number)
            bar_end = timeline.bar_end_tick(bar_number)
            if logical.start_tick >= bar_end or note_end <= bar_start:
                continue
            segment_start = max(logical.start_tick, bar_start)
            segment_end = min(note_end, bar_end)
            segment = segment_end - segment_start
            if segment <= 0:
                continue
            remaining_after = note_end - segment_end
            if first_segment and remaining_after == 0:
                tie_type = None
            elif first_segment:
                tie_type = "start"
            elif remaining_after == 0:
                tie_type = "stop"
            else:
                tie_type = "continue"
            fragments_by_bar[bar_number].append(
                _MeasureNoteFragment(
                    pitch=logical.pitch,
                    offset_ticks=segment_start - bar_start,
                    duration_ticks=segment,
                    velocity=logical.velocity,
                    tie=tie_type,
                    voice=logical.voice,
                    articulations=logical.articulations if first_segment else (),
                )
            )
            if tie_type in {"start", "continue"}:
                logger.debug(
                    "Split canonical note across measure boundary",
                    extra={
                        "pitch": logical.pitch,
                        "bar_number": bar_number,
                        "segment_ticks": segment,
                        "tie": tie_type,
                    },
                )
            first_segment = False
    return fragments_by_bar


def _render_legacy_musicxml(music: LLMMusicJson) -> tuple[str, ProjectionReport]:
    logger.info("Music JSON to MusicXML conversion started", extra={"schema_version": "legacy"})
    logger.debug(
        "Legacy Music JSON render metadata",
        extra={
            "schema_version": "legacy",
            "bar_count": _total_bars(music),
            "chord_count": len(music.harmony),
            "tempo": music.tempo,
            "key": music.key,
            "time_signature": music.time_signature,
        },
    )

    try:
        from music21 import chord, clef, harmony, instrument, key, layout, metadata, meter, note, stream, tempo
    except ImportError as exc:
        raise MusicJsonRenderError("music21 is not installed") from exc

    report = empty_projection_report()
    try:
        score = stream.Score(id="llm_music_json_score")
        score.metadata = metadata.Metadata(
            title=f"LLM Generated Music JSON - {music.key} - {music.time_signature}"
        )

        harmony_by_bar = {item.bar: item.chord for item in music.harmony}
        notes_by_track_staff_bar = _notes_by_track_staff_bar(music.notes)
        total_bars = _total_bars(music)

        for index, track in enumerate(music.tracks, start=1):
            if _is_piano_track(track):
                piano_parts = _piano_parts(
                    chord,
                    clef,
                    harmony,
                    instrument,
                    key,
                    meter,
                    note,
                    stream,
                    tempo,
                    music,
                    track,
                    index,
                    total_bars,
                    harmony_by_bar,
                    notes_by_track_staff_bar,
                    report,
                )
                for piano_part in piano_parts:
                    score.append(piano_part)
                score.insert(0, layout.StaffGroup(piano_parts, name=track.instrument, symbol="brace", barTogether=True))
                continue

            part = stream.Part(id=f"track_{index}_{track.role}")
            part.partName = track.instrument
            part.insert(0, _instrument_for_track(instrument, track))

            for bar_number in range(1, total_bars + 1):
                measure = stream.Measure(number=bar_number)
                if bar_number == 1:
                    _insert_staff_metadata(key, meter, tempo, measure, music, index == 1)
                    measure.insert(0, _clef_for_legacy_track(clef, track))
                chord_name = harmony_by_bar.get(bar_number)
                _append_chord_symbol(harmony, measure, chord_name, report)
                measure_notes = notes_by_track_staff_bar.get((index, "treble", bar_number), [])
                if measure_notes:
                    _append_notes(
                        chord,
                        note,
                        measure,
                        measure_notes,
                        _measure_quarter_length(music.time_signature),
                    )
                else:
                    element = _fallback_element_for_track(chord, harmony, note, track, chord_name, report)
                    measure.append(element)
                part.append(measure)

            score.append(part)

        musicxml = _write_score_musicxml(score)
        logger.info(
            "Music JSON to MusicXML conversion completed",
            extra={"schema_version": "legacy", **report.summary_extra()},
        )
        return musicxml, report
    except Exception as exc:
        logger.error(
            "Music JSON to MusicXML conversion failed",
            extra={
                "error_type": type(exc).__name__,
                "schema_version": "legacy",
                "tempo": music.tempo,
                "key": music.key,
                "time_signature": music.time_signature,
                "track_count": len(music.tracks),
            },
        )
        raise MusicJsonRenderError("Failed to render MusicXML from music JSON") from exc


def _write_score_musicxml(score) -> str:
    with tempfile.TemporaryDirectory(prefix="mukit-musicxml-") as tmp_dir:
        tmp_path = Path(tmp_dir) / "score.musicxml"
        logger.debug("Writing MusicXML via scoped temporary directory", extra={"output_name": tmp_path.name})
        score.write("musicxml", fp=str(tmp_path))
        musicxml = tmp_path.read_text(encoding="utf-8")
        logger.debug("Read MusicXML from scoped temporary file", extra={"musicxml_length": len(musicxml)})
        return musicxml


def _append_canonical_fragments(
    chord_module,
    note_module,
    tie_module,
    measure,
    fragments: list[_MeasureNoteFragment],
    *,
    measure_ql: float,
    ticks_per_quarter: int,
    stream_module=None,
) -> None:
    grouped: dict[tuple[int, int, int | None], list[_MeasureNoteFragment]] = {}
    for fragment in fragments:
        grouped.setdefault((fragment.offset_ticks, fragment.duration_ticks, fragment.voice), []).append(fragment)

    groups = sorted(
        grouped.items(),
        key=lambda item: (item[0][0], item[0][1], item[0][2] is None, item[0][2] or 0),
    )
    voice_assignments = _assign_fragment_groups_to_voices(groups)
    voice_count = max(voice_assignments.values(), default=1)

    if voice_count <= 1:
        _fill_measure_with_groups(
            chord_module,
            note_module,
            tie_module,
            measure,
            groups,
            measure_ql=measure_ql,
            ticks_per_quarter=ticks_per_quarter,
        )
        return

    if stream_module is None:
        from music21 import stream as stream_module

    groups_by_voice: dict[int, list] = {}
    for group_key, items in groups:
        voice_number = voice_assignments[group_key]
        groups_by_voice.setdefault(voice_number, []).append((group_key, items))

    for voice_number, voice_groups in sorted(groups_by_voice.items()):
        voice = stream_module.Voice(id=str(voice_number))
        _fill_measure_with_groups(
            chord_module,
            note_module,
            tie_module,
            voice,
            voice_groups,
            measure_ql=measure_ql,
            ticks_per_quarter=ticks_per_quarter,
        )
        measure.insert(0, voice)
    logger.debug(
        "Canonical polyphony rendered with voices",
        extra={"voice_count": voice_count, "group_count": len(groups)},
    )


def _assign_fragment_groups_to_voices(
    groups: list[tuple[tuple[int, int, int | None], list[_MeasureNoteFragment]]],
) -> dict[tuple[int, int, int | None], int]:
    assignments: dict[tuple[int, int, int | None], int] = {}
    voice_ends: dict[int, int] = {}
    next_voice = 1
    for (offset_ticks, duration_ticks, explicit_voice), _items in groups:
        key = (offset_ticks, duration_ticks, explicit_voice)
        if explicit_voice is not None:
            assignments[key] = explicit_voice
            voice_ends[explicit_voice] = max(voice_ends.get(explicit_voice, 0), offset_ticks + duration_ticks)
            next_voice = max(next_voice, explicit_voice + 1)
            continue
        placed = False
        for voice_number, end_tick in sorted(voice_ends.items()):
            if offset_ticks >= end_tick:
                assignments[key] = voice_number
                voice_ends[voice_number] = offset_ticks + duration_ticks
                placed = True
                break
        if not placed:
            assignments[key] = next_voice
            voice_ends[next_voice] = offset_ticks + duration_ticks
            next_voice += 1
    return assignments


def _fill_measure_with_groups(
    chord_module,
    note_module,
    tie_module,
    container,
    groups: list[tuple[tuple[int, int, int | None], list[_MeasureNoteFragment]]],
    *,
    measure_ql: float,
    ticks_per_quarter: int,
) -> None:
    occupied_until = 0
    measure_ticks = int(round(measure_ql * ticks_per_quarter))
    for (offset_ticks, duration_ticks, _voice), items in groups:
        offset_ql = offset_ticks / ticks_per_quarter
        duration_ql = duration_ticks / ticks_per_quarter
        if offset_ticks > occupied_until:
            rest_ql = (offset_ticks - occupied_until) / ticks_per_quarter
            logger.debug(
                "Inserted rest for empty canonical tick range",
                extra={"offset_ticks": occupied_until, "duration_ticks": offset_ticks - occupied_until},
            )
            container.insert(occupied_until / ticks_per_quarter, note_module.Rest(quarterLength=rest_ql))

        pitches = [item.pitch for item in items]
        if len(pitches) == 1:
            element = note_module.Note(pitches[0], quarterLength=duration_ql)
            _apply_velocity(element, items[0].velocity)
            _apply_tie(tie_module, element, items[0].tie)
            _apply_articulations(element, items[0].articulations)
        else:
            element = chord_module.Chord(pitches, quarterLength=duration_ql)
            for note_obj, item in zip(element.notes, items):
                _apply_velocity(note_obj, item.velocity)
                _apply_tie(tie_module, note_obj, item.tie)
                _apply_articulations(note_obj, item.articulations)
        container.insert(offset_ql, element)
        occupied_until = max(occupied_until, offset_ticks + duration_ticks)

    if occupied_until < measure_ticks:
        rest_ticks = measure_ticks - occupied_until
        container.insert(
            occupied_until / ticks_per_quarter,
            note_module.Rest(quarterLength=rest_ticks / ticks_per_quarter),
        )


def _apply_velocity(element, velocity: int) -> None:
    try:
        element.volume.velocity = velocity
    except Exception:
        pass


def _apply_tie(tie_module, element, tie_type: str | None) -> None:
    if not tie_type:
        return
    element.tie = tie_module.Tie(tie_type)


def _apply_articulations(element, articulation_names: Sequence[str]) -> None:
    if not articulation_names:
        return
    from music21 import articulations as m21_articulations

    for name in articulation_names:
        class_name = _ARTICULATION_CLASS_NAMES.get(name)
        if not class_name:
            continue
        cls = getattr(m21_articulations, class_name, None)
        if cls is None:
            continue
        element.articulations.append(cls())


def _canonical_piano_parts(
    *,
    chord_module,
    clef_module,
    harmony_module,
    instrument_module,
    key_module,
    meter_module,
    note_module,
    stream_module,
    tempo_module,
    tie_module,
    context: _NotationContext,
    track: CompositionTrackLike,
    track_index: int,
) -> list:
    parts = []
    composition = context.composition
    for staff_name in ("treble", "bass"):
        staff_events = [
            event
            for event in track.events
            if (event.staff or _default_staff_for_pitch(event.pitch)) == staff_name
        ]
        fragments_by_bar = _split_events_into_measure_fragments(
            staff_events,
            timeline=context.timeline,
        )
        part = stream_module.PartStaff(id=f"track_{track_index}_{track.id}_{staff_name}")
        part.partName = track.name or track.instrument if staff_name == "treble" else ""
        part.insert(0, instrument_module.Piano())
        for bar_number in range(1, composition.bar_count + 1):
            measure = stream_module.Measure(number=bar_number)
            if bar_number == 1:
                _insert_staff_metadata_fields(
                    key_module,
                    meter_module,
                    tempo_module,
                    measure,
                    composition.key,
                    composition.time_signature,
                    composition.tempo,
                    include_markings=staff_name == "treble" and track_index == 1,
                )
                measure.insert(0, clef_module.TrebleClef() if staff_name == "treble" else clef_module.BassClef())
            else:
                _insert_bar_attribute_changes(
                    key_module,
                    meter_module,
                    measure,
                    context,
                    bar_number,
                )
            if staff_name == "treble" and track_index == 1:
                _append_chord_symbol(harmony_module, measure, context.harmony_by_bar.get(bar_number), context.report)
                _insert_visible_part_directions(
                    tempo_module,
                    measure,
                    context,
                    bar_number,
                    include_markings=True,
                )
            if staff_name == "treble":
                _insert_track_dynamic_marks(measure, context, track.id, bar_number)
            bar_fragments = fragments_by_bar.get(bar_number, [])
            measure_ql = _bar_measure_quarter_length(context.timeline, bar_number)
            if bar_fragments:
                _append_canonical_fragments(
                    chord_module,
                    note_module,
                    tie_module,
                    measure,
                    bar_fragments,
                    measure_ql=measure_ql,
                    ticks_per_quarter=context.timeline.ticks_per_quarter,
                    stream_module=stream_module,
                )
            else:
                measure.append(note_module.Rest(quarterLength=measure_ql))
            part.append(measure)
        if staff_name == "treble":
            _attach_track_pedal_spanners(part, context, track.id)
        parts.append(part)
    return parts


def _default_staff_for_pitch(pitch: str) -> str:
    match = re.match(r"^[A-G][#b]?(-?\d+)$", pitch)
    if not match:
        return "treble"
    octave = int(match.group(1))
    return "bass" if octave < 4 else "treble"


def _is_piano_composition_track(track: CompositionTrackLike) -> bool:
    return "piano" in track.instrument.lower() or track.staff == "grand"


def _uses_bass_clef(*, role: str, instrument: str, staff: str | None = None) -> bool:
    if staff == "bass":
        return True
    if role == "bass":
        return True
    return "bass" in instrument.lower()


def _clef_for_composition_track(clef_module, track: CompositionTrackLike):
    uses_bass = _uses_bass_clef(role=track.role, instrument=track.instrument, staff=track.staff)
    clef_name = "bass" if uses_bass else "treble"
    logger.info(
        "[FIX] Applied clef for non-piano composition track",
        extra={
            "track_id": track.id,
            "role": track.role,
            "instrument": track.instrument,
            "staff": track.staff,
            "clef": clef_name,
        },
    )
    return clef_module.BassClef() if uses_bass else clef_module.TrebleClef()


def _clef_for_legacy_track(clef_module, track: LLMMusicTrack):
    uses_bass = _uses_bass_clef(role=track.role, instrument=track.instrument)
    clef_name = "bass" if uses_bass else "treble"
    logger.info(
        "[FIX] Applied clef for non-piano legacy track",
        extra={
            "role": track.role,
            "instrument": track.instrument,
            "clef": clef_name,
        },
    )
    return clef_module.BassClef() if uses_bass else clef_module.TrebleClef()


def _instrument_for_composition_track(instrument_module, track: CompositionTrackLike):
    """Resolve music21 instrument preferring program/name over authored role.

    Arrangement candidates may assign bass/pad/etc. roles to non-matching
    instruments (e.g. cello with role=bass). Explicit ``midi_program`` and
    instrument labels must win; role is only a fallback when identity is blank.
    """
    return _resolve_music21_instrument(
        instrument_module,
        instrument=track.instrument,
        role=track.role,
        midi_program=getattr(track, "midi_program", None),
        is_drum=bool(getattr(track, "is_drum", False)),
    )


def _total_bars(music: LLMMusicJson) -> int:
    return sum(section.bars for section in music.sections)


def _instrument_for_track(instrument_module, track: LLMMusicTrack):
    return _resolve_music21_instrument(
        instrument_module,
        instrument=track.instrument,
        role=track.role,
        midi_program=getattr(track, "midi_program", None),
        is_drum=False,
    )


def _resolve_music21_instrument(
    instrument_module,
    *,
    instrument: str | None,
    role: str | None,
    midi_program: int | None,
    is_drum: bool,
):
    instrument_name = (instrument or "").lower().strip()
    role_name = (role or "").lower().strip()

    if is_drum or role_name in {"drums", "percussion"} or "drum" in instrument_name:
        return instrument_module.Woodblock()

    if midi_program is not None:
        try:
            program = int(midi_program)
        except (TypeError, ValueError):
            program = None
        if program is not None and 0 <= program <= 127:
            try:
                resolved = instrument_module.instrumentFromMidiProgram(program)
                if resolved is not None:
                    return resolved
            except Exception:
                logger.debug(
                    "music21 instrumentFromMidiProgram failed; falling back to name/role",
                    extra={"midi_program": program},
                )

    named = _instrument_from_explicit_name(instrument_module, instrument_name)
    if named is not None:
        return named

    # Role is a last-resort fallback when label/program did not identify a sound.
    if role_name == "bass":
        return instrument_module.ElectricBass()
    if role_name in {"pad", "harmony"} and not instrument_name:
        return instrument_module.StringInstrument()
    return instrument_module.Piano()


def _instrument_from_explicit_name(instrument_module, instrument_name: str):
    """Map a non-empty instrument label without letting role or 'bass' substrings steal."""
    if not instrument_name:
        return None
    # Specific woodwinds before the generic "bass" token check (bassoon).
    if "bassoon" in instrument_name:
        return instrument_module.Bassoon()
    if "flute" in instrument_name or "piccolo" in instrument_name:
        return instrument_module.Flute()
    if "clarinet" in instrument_name:
        return instrument_module.Clarinet()
    if "oboe" in instrument_name:
        return instrument_module.Oboe()
    if "english horn" in instrument_name or "englishhorn" in instrument_name:
        return instrument_module.EnglishHorn()
    if "cello" in instrument_name or "violoncello" in instrument_name:
        return instrument_module.Violoncello()
    if "viola" in instrument_name:
        return instrument_module.Viola()
    if "violin" in instrument_name:
        return instrument_module.Violin()
    if "contrabass" in instrument_name or "double bass" in instrument_name:
        return instrument_module.Contrabass()
    if (
        instrument_name in {"bass", "acoustic bass", "electric bass", "bass guitar"}
        or "electric bass" in instrument_name
        or "acoustic bass" in instrument_name
        or instrument_name.endswith(" bass")
        or instrument_name.startswith("bass ")
    ):
        return instrument_module.ElectricBass()
    if "trumpet" in instrument_name:
        return instrument_module.Trumpet()
    if "trombone" in instrument_name:
        return instrument_module.Trombone()
    if "tuba" in instrument_name:
        return instrument_module.Tuba()
    if "horn" in instrument_name:
        return instrument_module.Horn()
    if "guitar" in instrument_name:
        return instrument_module.AcousticGuitar()
    if "harp" in instrument_name:
        return instrument_module.Harp()
    if "organ" in instrument_name:
        return instrument_module.PipeOrgan()
    if (
        "string" in instrument_name
        or instrument_name in {"pad", "strings"}
        or "synth pad" in instrument_name
        or instrument_name.endswith(" pad")
    ):
        return instrument_module.StringInstrument()
    if "piano" in instrument_name or "keyboard" in instrument_name:
        return instrument_module.Piano()
    return None


def _is_piano_track(track: LLMMusicTrack) -> bool:
    return "piano" in track.instrument.lower()


def _notes_by_track_staff_bar(notes: list[LLMMusicNoteItem]) -> dict[tuple[int, str, int], list[LLMMusicNoteItem]]:
    grouped: dict[tuple[int, str, int], list[LLMMusicNoteItem]] = {}
    for item in notes:
        grouped.setdefault((item.track, item.staff, item.bar), []).append(item)
    return grouped


def _piano_parts(
    chord_module,
    clef_module,
    harmony_module,
    instrument_module,
    key_module,
    meter_module,
    note_module,
    stream_module,
    tempo_module,
    music: LLMMusicJson,
    track: LLMMusicTrack,
    track_index: int,
    total_bars: int,
    harmony_by_bar: dict[int, str],
    notes_by_track_staff_bar: dict[tuple[int, str, int], list[LLMMusicNoteItem]],
    report: ProjectionReport,
) -> list:
    parts = []
    for staff_name in ("treble", "bass"):
        part = stream_module.PartStaff(id=f"track_{track_index}_piano_{staff_name}")
        part.partName = track.instrument if staff_name == "treble" else ""
        part.insert(0, instrument_module.Piano())

        for bar_number in range(1, total_bars + 1):
            measure = stream_module.Measure(number=bar_number)
            if bar_number == 1:
                _insert_staff_metadata(
                    key_module,
                    meter_module,
                    tempo_module,
                    measure,
                    music,
                    staff_name == "treble" and track_index == 1,
                )
                measure.insert(0, clef_module.TrebleClef() if staff_name == "treble" else clef_module.BassClef())
            if staff_name == "treble":
                _append_chord_symbol(harmony_module, measure, harmony_by_bar.get(bar_number), report)

            measure_notes = notes_by_track_staff_bar.get((track_index, staff_name, bar_number), [])
            if measure_notes:
                _append_notes(
                    chord_module,
                    note_module,
                    measure,
                    measure_notes,
                    _measure_quarter_length(music.time_signature),
                )
            else:
                measure.append(note_module.Rest(quarterLength=_measure_quarter_length(music.time_signature)))
            part.append(measure)

        parts.append(part)
    return parts


def _music21_key(key_module, key_name: str):
    tonic, mode = key_name.split(maxsplit=1)
    return key_module.Key(tonic, mode.lower())


def _insert_staff_metadata(
    key_module,
    meter_module,
    tempo_module,
    measure,
    music: LLMMusicJson,
    include_markings: bool,
) -> None:
    _insert_staff_metadata_fields(
        key_module,
        meter_module,
        tempo_module,
        measure,
        music.key,
        music.time_signature,
        music.tempo,
        include_markings=include_markings,
    )


def _insert_staff_metadata_fields(
    key_module,
    meter_module,
    tempo_module,
    measure,
    key_name: str,
    time_signature: str,
    tempo_bpm: int,
    *,
    include_markings: bool,
) -> None:
    measure.insert(0, _music21_key(key_module, key_name))
    measure.insert(0, meter_module.TimeSignature(time_signature))
    if include_markings:
        measure.insert(0, tempo_module.MetronomeMark(number=tempo_bpm))


def _append_chord_symbol(harmony_module, measure, chord_name: str | None, report: ProjectionReport) -> None:
    if not chord_name:
        return
    try:
        chord_symbol = harmony_module.ChordSymbol(_music21_chord_name(chord_name))
        chord_symbol.quarterLength = 0
        measure.insert(0, chord_symbol)
    except Exception:
        logger.warning("Unsupported chord symbol skipped during MusicXML render", extra={"chord": chord_name})
        report.add_issue(
            code="marker_normalized",
            severity="warning",
            status="approximated",
            message=f"Unsupported chord symbol skipped: {chord_name}",
            details={"chord": chord_name},
        )


def _append_notes(
    chord_module,
    note_module,
    measure,
    measure_notes: list[LLMMusicNoteItem],
    measure_quarter_length: float,
) -> None:
    notes_by_start: dict[tuple[float, float], list[LLMMusicNoteItem]] = {}
    for item in measure_notes:
        notes_by_start.setdefault((item.beat - 1, item.duration), []).append(item)

    cursor = 0.0
    for (offset, duration), items in sorted(notes_by_start.items()):
        if offset > cursor:
            measure.insert(cursor, note_module.Rest(quarterLength=offset - cursor))
        pitches = [item.pitch for item in items]
        if len(pitches) == 1:
            element = note_module.Note(pitches[0], quarterLength=duration)
        else:
            element = chord_module.Chord(pitches, quarterLength=duration)
        measure.insert(offset, element)
        cursor = max(cursor, offset + duration)

    if cursor < measure_quarter_length:
        measure.insert(cursor, note_module.Rest(quarterLength=measure_quarter_length - cursor))


def _fallback_element_for_track(
    chord_module,
    harmony_module,
    note_module,
    track: LLMMusicTrack,
    chord_name: str | None,
    report: ProjectionReport,
):
    if track.role in {"drums", "percussion"}:
        hit = note_module.Note("C4", quarterLength=4)
        hit.lyric = "hit"
        return hit

    if not chord_name:
        return note_module.Rest(quarterLength=4)

    try:
        harmony_chord = harmony_module.ChordSymbol(_music21_chord_name(chord_name))
        harmony_chord.quarterLength = 4
    except Exception:
        logger.warning("Unsupported chord simplified during MusicXML render", extra={"chord": chord_name})
        report.add_issue(
            code="marker_normalized",
            severity="warning",
            status="approximated",
            message=f"Unsupported chord simplified: {chord_name}",
            details={"chord": chord_name},
        )
        return note_module.Rest(quarterLength=4)

    if track.role == "bass":
        bass_note = harmony_chord.root() or harmony_chord.pitches[0]
        return note_module.Note(bass_note, quarterLength=4)

    if track.role == "harmony":
        return chord_module.Chord(harmony_chord.pitches, quarterLength=4)

    return harmony_chord


def _music21_chord_name(chord_name: str) -> str:
    return FLAT_CHORD_PATTERN.sub(r"\1-", chord_name.strip())


def _measure_quarter_length(time_signature: str) -> float:
    numerator, denominator = (int(part) for part in time_signature.split("/"))
    return numerator * (4 / denominator)
