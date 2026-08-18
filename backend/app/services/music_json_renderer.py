import logging
import re
import tempfile
from pathlib import Path

from ..schemas import LLMMusicJson, LLMMusicNoteItem, LLMMusicTrack


logger = logging.getLogger(__name__)
FLAT_CHORD_PATTERN = re.compile(r"^([A-G])b")


class MusicJsonRenderError(RuntimeError):
    pass


def render_musicxml(music: LLMMusicJson) -> tuple[str, list[str]]:
    logger.info("Music JSON to MusicXML conversion started")
    logger.debug(
        "Music JSON render metadata",
        extra={
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
        score.metadata = metadata.Metadata(title=f"LLM Generated Music JSON - {music.key} - {music.time_signature}")

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
                    _append_notes(chord, note, measure, measure_notes, _measure_quarter_length(music.time_signature))
                else:
                    element = _fallback_element_for_track(chord, harmony, note, track, chord_name, warnings)
                    measure.append(element)
                part.append(measure)

            score.append(part)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".musicxml") as tmp_file:
            tmp_path = Path(tmp_file.name)

        try:
            score.write("musicxml", fp=str(tmp_path))
            musicxml = tmp_path.read_text(encoding="utf-8")
        finally:
            tmp_path.unlink(missing_ok=True)

        logger.info("Music JSON to MusicXML conversion completed")
        return musicxml, warnings
    except Exception as exc:
        logger.error(
            "Music JSON to MusicXML conversion failed",
            extra={
                "error_type": type(exc).__name__,
                "tempo": music.tempo,
                "key": music.key,
                "time_signature": music.time_signature,
                "track_count": len(music.tracks),
            },
        )
        raise MusicJsonRenderError("Failed to render MusicXML from music JSON") from exc


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
    measure.insert(0, _music21_key(key_module, music.key))
    measure.insert(0, meter_module.TimeSignature(music.time_signature))
    if include_markings:
        measure.insert(0, tempo_module.MetronomeMark(number=music.tempo))


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
