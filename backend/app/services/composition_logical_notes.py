"""Shared logical-note / tie-collapse helpers for analysis and export.

Each tie chain collapses to one attack and one occupancy span. Source event IDs
are retained in bounded form for analysis locators. MIDI export may project the
collapsed notes further (articulation gate/velocity) without changing collapse
semantics.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Sequence

from app.composition_schemas import CompositionV2NoteEvent, CompositionV2Track, midi_pitch_number


logger = logging.getLogger(__name__)

# Bound source IDs retained per logical note (analysis locators).
MAX_SOURCE_EVENT_IDS = 16


@dataclass(frozen=True)
class CollapsedLogicalNote:
    """One sounding occupancy span after tie collapse (canonical notated duration)."""

    pitch: str
    midi_number: int
    pitch_class: int
    start_tick: int
    duration_ticks: int
    velocity: int
    articulations: tuple[str, ...]
    staff: str | None
    voice: int | None
    source_event_ids: tuple[str, ...]
    tie_group_id: str | None
    member_count: int

    @property
    def end_tick(self) -> int:
        return self.start_tick + self.duration_ticks


def collapse_track_tie_chains(track: CompositionV2Track) -> list[CollapsedLogicalNote]:
    """Collapse validated tie chains within one track into logical occupancy spans.

    Standalone notes keep authored duration. Tie groups use the head pitch/velocity/
    articulations and the summed notated duration of all members. Output order is
    deterministic by (start_tick, pitch, duration_ticks) and independent of authored
    event array order for tied groups.
    """
    tie_members: dict[str, list[CompositionV2NoteEvent]] = {}
    standalone: list[CompositionV2NoteEvent] = []
    for event in track.events:
        if event.tie is None:
            standalone.append(event)
            continue
        tie_members.setdefault(event.tie.group_id, []).append(event)

    collapsed: list[CollapsedLogicalNote] = []
    for event in standalone:
        collapsed.append(_from_single_event(event, tie_group_id=None, member_count=1))

    for group_id, members in tie_members.items():
        ordered = sorted(members, key=lambda item: (item.start_tick, item.duration_ticks))
        head = ordered[0]
        total_duration = sum(item.duration_ticks for item in ordered)
        source_ids = _bounded_source_ids(ordered)
        midi = midi_pitch_number(head.pitch)
        collapsed.append(
            CollapsedLogicalNote(
                pitch=head.pitch,
                midi_number=midi,
                pitch_class=midi % 12,
                start_tick=head.start_tick,
                duration_ticks=total_duration,
                velocity=head.velocity,
                articulations=tuple(head.articulations),
                staff=head.staff,
                voice=head.voice,
                source_event_ids=source_ids,
                tie_group_id=group_id,
                member_count=len(ordered),
            )
        )

    collapsed.sort(key=lambda item: (item.start_tick, item.pitch, item.duration_ticks))
    logger.debug(
        "Collapsed track tie chains",
        extra={
            "track_id": track.id,
            "source_event_count": len(track.events),
            "logical_note_count": len(collapsed),
            "tie_group_count": len(tie_members),
        },
    )
    return collapsed


def note_overlaps_interval(note: CollapsedLogicalNote, start_tick: int, end_tick: int) -> bool:
    """Overlap semantics: note.start < end and note.end > start."""
    return note.start_tick < end_tick and note.end_tick > start_tick


def clip_occupancy(
    note: CollapsedLogicalNote,
    start_tick: int,
    end_tick: int,
) -> int:
    """Return clipped occupancy ticks inside [start_tick, end_tick)."""
    if not note_overlaps_interval(note, start_tick, end_tick):
        return 0
    left = max(note.start_tick, start_tick)
    right = min(note.end_tick, end_tick)
    return max(0, right - left)


def attack_in_interval(note: CollapsedLogicalNote, start_tick: int, end_tick: int) -> bool:
    """Attack-at-onset semantics for note counts."""
    return start_tick <= note.start_tick < end_tick


def _from_single_event(
    event: CompositionV2NoteEvent,
    *,
    tie_group_id: str | None,
    member_count: int,
) -> CollapsedLogicalNote:
    midi = midi_pitch_number(event.pitch)
    source_ids = _bounded_source_ids([event])
    return CollapsedLogicalNote(
        pitch=event.pitch,
        midi_number=midi,
        pitch_class=midi % 12,
        start_tick=event.start_tick,
        duration_ticks=event.duration_ticks,
        velocity=event.velocity,
        articulations=tuple(event.articulations),
        staff=event.staff,
        voice=event.voice,
        source_event_ids=source_ids,
        tie_group_id=tie_group_id,
        member_count=member_count,
    )


def _bounded_source_ids(events: Sequence[CompositionV2NoteEvent]) -> tuple[str, ...]:
    ids: list[str] = []
    for event in events:
        if event.id is None:
            continue
        ids.append(event.id)
        if len(ids) >= MAX_SOURCE_EVENT_IDS:
            break
    return tuple(ids)
