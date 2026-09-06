import logging
import re
from io import BytesIO
from typing import TypedDict

from app.schemas import Composition, _midi_pitch_number


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


def composition_to_midi_ready(composition: Composition) -> MidiReadyComposition:
    logger.debug(
        "Mapping canonical composition to MIDI-ready structure",
        extra={
            "schema_version": composition.schema_version,
            "ticks_per_quarter": composition.ticks_per_quarter,
            "track_count": len(composition.tracks),
            "event_count": sum(len(track.events) for track in composition.tracks),
        },
    )
    midi_tracks: list[MidiReadyTrack] = []
    for track in composition.tracks:
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
                "pitch": event.pitch,
                "start_tick": event.start_tick,
                "duration_ticks": event.duration_ticks,
                "velocity": event.velocity,
            }
            for event in sorted(track.events, key=lambda item: (item.start_tick, item.pitch, item.duration_ticks))
        ]
        midi_track: MidiReadyTrack = {
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
        midi_tracks.append(midi_track)
        logger.debug(
            "Mapped canonical track to MIDI-ready structure",
            extra={
                "track_id": track.id,
                "channel": track.channel,
                "midi_program": track.midi_program,
                "event_count": len(midi_events),
            },
        )

    result: MidiReadyComposition = {
        "schema_version": composition.schema_version,
        "ticks_per_quarter": composition.ticks_per_quarter,
        "tempo": composition.tempo,
        "duration_ticks": composition.duration_ticks,
        "tracks": midi_tracks,
    }
    logger.debug(
        "Mapped composition to MIDI-ready structure",
        extra={"track_count": len(midi_tracks), "duration_ticks": composition.duration_ticks},
    )
    return result


def render_midi(composition: Composition) -> bytes:
    """Render a Standard MIDI File from canonical Composition note events."""
    try:
        import mido
    except ImportError as exc:
        raise CompositionMidiError("mido is not installed") from exc

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
        },
    )

    try:
        mid = mido.MidiFile(ticks_per_beat=composition.ticks_per_quarter, type=1)

        conductor = mido.MidiTrack()
        mid.tracks.append(conductor)
        conductor.append(mido.MetaMessage("track_name", name="Conductor", time=0))
        conductor.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(composition.tempo), time=0))
        numerator, denominator = (int(part) for part in composition.time_signature.split("/"))
        conductor.append(
            mido.MetaMessage(
                "time_signature",
                numerator=numerator,
                denominator=denominator,
                clocks_per_click=24,
                notated_32nd_notes_per_beat=8,
                time=0,
            )
        )
        key_sharps, _key_mode = _midi_key_signature(composition.key)
        if key_sharps is not None:
            conductor.append(mido.MetaMessage("key_signature", key=_mido_key_name(composition.key), time=0))
        else:
            logger.debug(
                "Skipping unsupported MIDI key signature metadata",
                extra={"key": composition.key},
            )
        conductor.append(mido.MetaMessage("end_of_track", time=0))

        for track in composition.tracks:
            midi_track = mido.MidiTrack()
            mid.tracks.append(midi_track)
            channel = track.channel - 1  # mido uses 0-15
            midi_track.append(mido.MetaMessage("track_name", name=track.name or track.id, time=0))
            if not track.is_drum:
                midi_track.append(mido.Message("program_change", channel=channel, program=track.midi_program, time=0))
            midi_track.append(mido.Message("control_change", channel=channel, control=7, value=track.volume, time=0))
            # MIDI pan CC10 is 0..127 with 64 center; composition pan is -64..63.
            pan_value = max(0, min(127, track.pan + 64))
            midi_track.append(mido.Message("control_change", channel=channel, control=10, value=pan_value, time=0))

            timed_events: list[tuple[int, int, str, int, int]] = []
            for event in track.events:
                note_number = _midi_pitch_number(event.pitch)
                timed_events.append((event.start_tick, 1, "on", note_number, event.velocity))
                timed_events.append((event.start_tick + event.duration_ticks, 0, "off", note_number, 0))

            timed_events.sort(key=lambda item: (item[0], item[1], item[3]))
            cursor = 0
            for absolute_tick, _order, kind, note_number, velocity in timed_events:
                delta = absolute_tick - cursor
                if kind == "on":
                    midi_track.append(
                        mido.Message("note_on", channel=channel, note=note_number, velocity=velocity, time=delta)
                    )
                else:
                    midi_track.append(
                        mido.Message("note_off", channel=channel, note=note_number, velocity=0, time=delta)
                    )
                cursor = absolute_tick

            end_delta = max(0, composition.duration_ticks - cursor)
            midi_track.append(mido.MetaMessage("end_of_track", time=end_delta))
            logger.debug(
                "Rendered MIDI track metadata",
                extra={
                    "track_id": track.id,
                    "channel": track.channel,
                    "midi_program": track.midi_program,
                    "event_count": len(track.events),
                    "volume": track.volume,
                    "pan": track.pan,
                },
            )

        buffer = BytesIO()
        mid.save(file=buffer)
        midi_bytes = buffer.getvalue()
        logger.debug(
            "MIDI render completed",
            extra={
                "track_count": len(composition.tracks),
                "event_count": event_count,
                "byte_length": len(midi_bytes),
            },
        )
        return midi_bytes
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
