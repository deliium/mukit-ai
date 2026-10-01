"""Shared 32-bar picture score for film-score adaptation vectors."""

from __future__ import annotations

from pydantic import TypeAdapter

from app.composition_schemas import CompositionV2
from app.film_score_adapt_schemas import FilmPictureEdit, FilmTimelineSnapshot
from app.film_score_schemas import FilmCueSnapshot

_EDIT = TypeAdapter(FilmPictureEdit)

BAR_TICKS = 1920
PITCHES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def pitch_for_bar(bar: int) -> str:
    index = bar - 1
    return f"{PITCHES[index % 12]}{4 + index // 12}"


def cue(
    cue_id: str,
    seconds: float,
    *,
    kind: str = "hit_point",
    importance: str = "critical",
    tolerance_frames: int = 0,
) -> FilmCueSnapshot:
    return FilmCueSnapshot(
        id=cue_id,
        kind=kind,
        importance=importance,
        video_seconds=seconds,
        tolerance_frames=tolerance_frames,
    )


def snapshot(
    duration: float,
    cues: list[FilmCueSnapshot],
    *,
    rate: tuple[int, int] = (24, 1),
) -> FilmTimelineSnapshot:
    return FilmTimelineSnapshot(
        duration_seconds=duration,
        frame_rate_numerator=rate[0],
        frame_rate_denominator=rate[1],
        video_origin_seconds=0,
        musical_origin_tick=0,
        cues=cues,
    )


def score_bars(bar_count: int, *, section_bars: int = 8, label_at: dict[int, str] | None = None) -> CompositionV2:
    """One note per bar. Section ``label_at`` is 1-based section index to label."""
    labels = label_at or {}
    sections = []
    bar = 1
    section_index = 1
    while bar <= bar_count:
        count = min(section_bars, bar_count - bar + 1)
        label = labels.get(section_index)
        section_type = "chorus" if label == "climax" else "verse"
        sections.append(
            {
                "id": f"sec_{section_index}",
                "type": section_type,
                "label": label,
                "start_bar": bar,
                "bar_count": count,
                "start_tick": (bar - 1) * BAR_TICKS,
                "duration_ticks": count * BAR_TICKS,
            }
        )
        bar += count
        section_index += 1
    events = [
        {
            "type": "note",
            "id": f"note_bar_{index:02d}",
            "pitch": pitch_for_bar(index),
            "start_tick": (index - 1) * BAR_TICKS,
            "duration_ticks": BAR_TICKS,
            "velocity": 80,
        }
        for index in range(1, bar_count + 1)
    ]
    harmony = []
    chords = ["C", "G", "F", "C"]
    for index, section in enumerate(sections):
        harmony.append(
            {
                "start_tick": section["start_tick"],
                "duration_ticks": section["duration_ticks"],
                "chord": chords[index % len(chords)],
            }
        )
    return CompositionV2.model_validate(
        {
            "schema_version": "composition.v2",
            "tempo": 120,
            "key": "C major",
            "time_signature": "4/4",
            "ticks_per_quarter": 480,
            "bar_count": bar_count,
            "duration_ticks": bar_count * BAR_TICKS,
            "sections": sections,
            "tracks": [
                {
                    "id": "melody",
                    "name": "Melody",
                    "instrument": "acoustic_grand_piano",
                    "role": "melody",
                    "midi_program": 0,
                    "channel": 1,
                    "events": events,
                }
            ],
            "harmony": harmony,
            "markers": [
                {
                    "id": "mark_later",
                    "tick": 19 * BAR_TICKS,
                    "kind": "rehearsal",
                    "label": "later",
                }
            ]
            if bar_count >= 20
            else [],
            "motifs": [
                {
                    "id": "motif_theme",
                    "label": "Theme",
                    "occurrences": [
                        {
                            "id": "occ_original",
                            "track_id": "melody",
                            "event_ids": ["note_bar_01", "note_bar_02", "note_bar_03"],
                            "relationship": "original",
                        }
                    ],
                }
            ],
        }
    )


def base_score() -> CompositionV2:
    return score_bars(32, label_at={3: "climax"})


def base_previous(cues: list[FilmCueSnapshot] | None = None, duration: float = 64) -> FilmTimelineSnapshot:
    return snapshot(
        duration,
        cues
        if cues is not None
        else [cue("hit_aaaa0001", 6), cue("hit_bbbb0002", 36)],
    )


def delete_span(start: float, end: float, op_id: str = "edit_0000000a"):
    return _EDIT.validate_python(
        {
            "op_id": op_id,
            "kind": "delete_span",
            "start_seconds": start,
            "end_seconds": end,
        }
    )


def move_hit(cue_id: str, from_seconds: float, to_seconds: float, op_id: str = "edit_0000000b"):
    return _EDIT.validate_python(
        {
            "op_id": op_id,
            "kind": "move_hit",
            "cue_id": cue_id,
            "from_seconds": from_seconds,
            "to_seconds": to_seconds,
        }
    )


def insert_span(at_seconds: float, duration: float, op_id: str = "edit_0000000c"):
    return _EDIT.validate_python(
        {
            "op_id": op_id,
            "kind": "insert_span",
            "at_seconds": at_seconds,
            "duration_seconds": duration,
        }
    )
