import logging
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..schemas import Composition, CompositionTrack, LLMMusicJson, LLMMusicNoteItem, LLMMusicTrack, NoteEvent
from .composition_timing import bar_duration_ticks


logger = logging.getLogger(__name__)
FLAT_CHORD_PATTERN = re.compile(r"^([A-G])b")


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


def render_musicxml(music: Composition | LLMMusicJson) -> tuple[str, list[str]]:
    if isinstance(music, Composition):
        return _render_canonical_musicxml(music)
    return _render_legacy_musicxml(music)


def _render_canonical_musicxml(composition: Composition) -> tuple[str, list[str]]:
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

    warnings: list[str] = []
    try:
        score = stream.Score(id="composition_v1_score")
        score.metadata = metadata.Metadata(
            title=f"Composition V1 - {composition.key} - {composition.time_signature}"
        )

        harmony_by_bar = {item.bar: item.chord for item in composition.harmony}
        bar_ticks = bar_duration_ticks(composition.time_signature, composition.ticks_per_quarter)
        measure_ql = _measure_quarter_length(composition.time_signature)
        tpq = composition.ticks_per_quarter

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
                    composition=composition,
                    track=track,
                    track_index=index,
                    harmony_by_bar=harmony_by_bar,
                    bar_ticks=bar_ticks,
                    measure_ql=measure_ql,
                    ticks_per_quarter=tpq,
                    warnings=warnings,
                )
                for piano_part in piano_parts:
                    score.append(piano_part)
                score.insert(
                    0,
                    layout.StaffGroup(piano_parts, name=track.name or track.instrument, symbol="brace", barTogether=True),
                )
                continue

            part = stream.Part(id=f"track_{index}_{track.id}")
            part.partName = track.name or track.instrument
            part.insert(0, _instrument_for_composition_track(instrument, track))
            fragments_by_bar = _split_events_into_measure_fragments(
                track.events,
                bar_count=composition.bar_count,
                bar_ticks=bar_ticks,
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
                # Chord symbols are notation metadata only; attach once on the first part.
                if index == 1:
                    _append_chord_symbol(harmony, measure, harmony_by_bar.get(bar_number), warnings)
                bar_fragments = fragments_by_bar.get(bar_number, [])
                if bar_fragments:
                    _append_canonical_fragments(
                        chord,
                        note,
                        tie,
                        measure,
                        bar_fragments,
                        measure_ql=measure_ql,
                        ticks_per_quarter=tpq,
                        stream_module=stream,
                    )
                else:
                    logger.debug(
                        "Inserted rest for canonical track without events in measure",
                        extra={"track_id": track.id, "bar_number": bar_number},
                    )
                    measure.append(note.Rest(quarterLength=measure_ql))
                part.append(measure)
            score.append(part)

        musicxml = _write_score_musicxml(score)
        logger.info(
            "Canonical MusicXML render completed",
            extra={
                "schema_version": composition.schema_version,
                "warning_count": len(warnings),
                "musicxml_length": len(musicxml),
            },
        )
        return musicxml, warnings
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


def _render_legacy_musicxml(music: LLMMusicJson) -> tuple[str, list[str]]:
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

    warnings: list[str] = []
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
                    warnings,
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
                chord_name = harmony_by_bar.get(bar_number)
                _append_chord_symbol(harmony, measure, chord_name, warnings)
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
                    element = _fallback_element_for_track(chord, harmony, note, track, chord_name, warnings)
                    measure.append(element)
                part.append(measure)

            score.append(part)

        musicxml = _write_score_musicxml(score)
        logger.info(
            "Music JSON to MusicXML conversion completed",
            extra={"schema_version": "legacy", "warning_count": len(warnings)},
        )
        return musicxml, warnings
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


def _split_events_into_measure_fragments(
    events: list[NoteEvent],
    *,
    bar_count: int,
    bar_ticks: int,
) -> dict[int, list[_MeasureNoteFragment]]:
    fragments_by_bar: dict[int, list[_MeasureNoteFragment]] = {bar: [] for bar in range(1, bar_count + 1)}
    for event in events:
        remaining = event.duration_ticks
        cursor = event.start_tick
        first_segment = True
        while remaining > 0:
            bar_index = cursor // bar_ticks
            bar_number = bar_index + 1
            if bar_number > bar_count:
                break
            offset_ticks = cursor % bar_ticks
            available = bar_ticks - offset_ticks
            segment = min(remaining, available)
            remaining_after = remaining - segment
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
                    pitch=event.pitch,
                    offset_ticks=offset_ticks,
                    duration_ticks=segment,
                    velocity=event.velocity,
                    tie=tie_type,
                    voice=event.voice,
                )
            )
            if tie_type in {"start", "continue"}:
                logger.debug(
                    "Split/tied canonical note across measure boundary",
                    extra={
                        "pitch": event.pitch,
                        "bar_number": bar_number,
                        "segment_ticks": segment,
                        "tie": tie_type,
                    },
                )
            cursor += segment
            remaining = remaining_after
            first_segment = False
    return fragments_by_bar


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
    # Group by (offset, duration, voice) so equal-duration simultaneous pitches become chords.
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
    """Greedy interval coloring so overlapping unequal durations become distinct voices."""
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
        else:
            element = chord_module.Chord(pitches, quarterLength=duration_ql)
            for note_obj, item in zip(element.notes, items):
                _apply_velocity(note_obj, item.velocity)
                _apply_tie(tie_module, note_obj, item.tie)
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
    composition: Composition,
    track: CompositionTrack,
    track_index: int,
    harmony_by_bar: Mapping[int, str],
    bar_ticks: int,
    measure_ql: float,
    ticks_per_quarter: int,
    warnings: list[str],
) -> list:
    parts = []
    for staff_name in ("treble", "bass"):
        staff_events = [
            event
            for event in track.events
            if (event.staff or _default_staff_for_pitch(event.pitch)) == staff_name
        ]
        fragments_by_bar = _split_events_into_measure_fragments(
            staff_events,
            bar_count=composition.bar_count,
            bar_ticks=bar_ticks,
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
            if staff_name == "treble" and track_index == 1:
                _append_chord_symbol(harmony_module, measure, harmony_by_bar.get(bar_number), warnings)
            bar_fragments = fragments_by_bar.get(bar_number, [])
            if bar_fragments:
                _append_canonical_fragments(
                    chord_module,
                    note_module,
                    tie_module,
                    measure,
                    bar_fragments,
                    measure_ql=measure_ql,
                    ticks_per_quarter=ticks_per_quarter,
                    stream_module=stream_module,
                )
            else:
                measure.append(note_module.Rest(quarterLength=measure_ql))
            part.append(measure)
        parts.append(part)
    return parts


def _default_staff_for_pitch(pitch: str) -> str:
    match = re.match(r"^[A-G][#b]?(-?\d+)$", pitch)
    if not match:
        return "treble"
    octave = int(match.group(1))
    return "bass" if octave < 4 else "treble"


def _is_piano_composition_track(track: CompositionTrack) -> bool:
    return "piano" in track.instrument.lower() or track.staff == "grand"


def _instrument_for_composition_track(instrument_module, track: CompositionTrack):
    instrument_name = track.instrument.lower()
    if "flute" in instrument_name:
        return instrument_module.Flute()
    if "bass" in instrument_name or track.role == "bass":
        return instrument_module.ElectricBass()
    if "drum" in instrument_name or track.role in {"drums", "percussion"} or track.is_drum:
        return instrument_module.Woodblock()
    if "string" in instrument_name or track.role == "pad":
        return instrument_module.StringInstrument()
    return instrument_module.Piano()


def _total_bars(music: LLMMusicJson) -> int:
    return sum(section.bars for section in music.sections)


def _instrument_for_track(instrument_module, track: LLMMusicTrack):
    instrument_name = track.instrument.lower()
    if "bass" in instrument_name or track.role == "bass":
        return instrument_module.ElectricBass()
    if "drum" in instrument_name or track.role in {"drums", "percussion"}:
        return instrument_module.Woodblock()
    if "string" in instrument_name:
        return instrument_module.StringInstrument()
    return instrument_module.Piano()


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
    warnings: list[str],
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
                _append_chord_symbol(harmony_module, measure, harmony_by_bar.get(bar_number), warnings)

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


def _append_chord_symbol(harmony_module, measure, chord_name: str | None, warnings: list[str]) -> None:
    if not chord_name:
        return
    try:
        chord_symbol = harmony_module.ChordSymbol(_music21_chord_name(chord_name))
        chord_symbol.quarterLength = 0
        measure.insert(0, chord_symbol)
    except Exception:
        logger.warning("Unsupported chord symbol skipped during MusicXML render", extra={"chord": chord_name})
        warnings.append(f"Unsupported chord symbol skipped: {chord_name}")


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
    warnings: list[str],
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
        warnings.append(f"Unsupported chord simplified: {chord_name}")
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
