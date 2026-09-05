import logging
from typing import TypedDict

from app.schemas import Composition


logger = logging.getLogger(__name__)


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
