"""Bounded, deterministic source-context extraction for composition development.

Resolves section/bar sources, append seams, and structured musical context for
provider prompts. Never persists analysis; advisory projection is clamped text only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from app.composition_development_schemas import (
    CompositionDevelopmentError,
    CompositionDevelopmentPreviewRequest,
    DevelopmentOperation,
    DevelopmentSourceSelector,
)
from app.composition_schemas import (
    CompositionV2,
    CompositionV2HarmonyItem,
    CompositionV2MotifDefinition,
    CompositionV2Section,
    CompositionV2Track,
    midi_pitch_number,
)
from app.services.composition_analysis import analyze_composition, build_llm_analysis_context
from app.services.composition_timeline import CompiledTimeline, compile_timeline


logger = logging.getLogger(__name__)

DEVELOPMENT_CONTEXT_VERSION = "composition.development.context.v1"
DEFAULT_SEAM_CONTEXT_BARS = 4
MAX_MOTIF_EXEMPLAR_NOTES = 32
MAX_HARMONY_CONTEXT_ITEMS = 24
MAX_CADENCE_NOTES = 8
MAX_RELATIVE_NOTES_PER_TRACK = 64


@dataclass(frozen=True)
class RelativeContextNote:
    relative_start_tick: int
    duration_ticks: int
    pitch_semitone_offset: int
    velocity: int
    pitch: str


@dataclass(frozen=True)
class TrackContextSummary:
    track_id: str
    role: str
    instrument: str
    midi_program: int
    channel: int
    is_drum: bool
    pitch_min: int | None
    pitch_max: int | None
    attack_count: int
    note_load_ticks: int
    onset_ticks: tuple[int, ...]
    relative_notes: tuple[RelativeContextNote, ...]


@dataclass(frozen=True)
class MotifExemplar:
    motif_id: str
    label: str
    occurrence_id: str
    track_id: str
    relationship: str
    relative_notes: tuple[RelativeContextNote, ...]


@dataclass(frozen=True)
class SectionNeighborhood:
    section_id: str | None
    section_type: str
    label: str | None
    start_bar: int
    bar_count: int
    start_tick: int
    duration_ticks: int
    index: int


@dataclass(frozen=True)
class ResolvedDevelopmentScope:
    """Resolved source musical context and output placement."""

    operation: DevelopmentOperation
    source_start_bar: int
    source_end_bar: int
    source_start_tick: int
    source_end_tick: int
    # Append seam = current duration; variation uses selected half-open range.
    output_start_bar: int
    output_end_bar: int
    output_start_tick: int
    output_end_tick: int
    output_bars: int
    seam_tick: int
    source_section: SectionNeighborhood | None
    prior_section: SectionNeighborhood | None
    next_section: SectionNeighborhood | None
    defaulted_source: bool


@dataclass
class DevelopmentSourceContext:
    """Structured bounded context for one development preview request."""

    version: str
    composition: CompositionV2
    timeline: CompiledTimeline
    scope: ResolvedDevelopmentScope
    active_tempo: int
    active_time_signature: str
    active_key: str
    recent_harmony: tuple[dict[str, Any], ...]
    track_summaries: tuple[TrackContextSummary, ...]
    motif_exemplars: tuple[MotifExemplar, ...]
    rhythmic_profile: dict[str, float]
    cadence_notes: tuple[RelativeContextNote, ...]
    advisory_analysis: str | None
    truncated: bool
    truncation_reasons: tuple[str, ...] = field(default_factory=tuple)
    warning_codes: tuple[str, ...] = field(default_factory=tuple)


def _section_neighborhood(section: CompositionV2Section, index: int) -> SectionNeighborhood:
    return SectionNeighborhood(
        section_id=section.id,
        section_type=section.type,
        label=section.label,
        start_bar=section.start_bar,
        bar_count=section.bar_count,
        start_tick=section.start_tick,
        duration_ticks=section.duration_ticks,
        index=index,
    )


def _find_section_by_id(composition: CompositionV2, section_id: str) -> tuple[CompositionV2Section, int]:
    matches = [
        (index, section)
        for index, section in enumerate(composition.sections)
        if section.id == section_id
    ]
    if not matches:
        raise CompositionDevelopmentError(
            "development_invalid_source",
            "source section_id was not found",
            details={"section_id_present": True},
        )
    if len(matches) > 1:
        raise CompositionDevelopmentError(
            "development_invalid_source",
            "source section_id is ambiguous",
            details={"section_id_present": True, "match_count": len(matches)},
        )
    index, section = matches[0]
    return section, index


def _section_covering_bar(composition: CompositionV2, bar: int) -> tuple[CompositionV2Section, int] | None:
    for index, section in enumerate(composition.sections):
        end_bar = section.start_bar + section.bar_count - 1
        if section.start_bar <= bar <= end_bar:
            return section, index
    return None


def _trailing_section_scope(composition: CompositionV2, timeline: CompiledTimeline) -> ResolvedDevelopmentScope:
    last = composition.sections[-1]
    index = len(composition.sections) - 1
    start_bar = last.start_bar
    end_bar = last.start_bar + last.bar_count - 1
    start_tick, end_tick = timeline.bar_range_ticks(start_bar, end_bar)
    prior = (
        _section_neighborhood(composition.sections[index - 1], index - 1)
        if index > 0
        else None
    )
    return ResolvedDevelopmentScope(
        operation="continue",
        source_start_bar=start_bar,
        source_end_bar=end_bar,
        source_start_tick=start_tick,
        source_end_tick=end_tick,
        output_start_bar=composition.bar_count + 1,
        output_end_bar=composition.bar_count,
        output_start_tick=composition.duration_ticks,
        output_end_tick=composition.duration_ticks,
        output_bars=0,
        seam_tick=composition.duration_ticks,
        source_section=_section_neighborhood(last, index),
        prior_section=prior,
        next_section=None,
        defaulted_source=True,
    )


def resolve_development_scope(
    composition: CompositionV2,
    request: CompositionDevelopmentPreviewRequest,
    timeline: CompiledTimeline | None = None,
) -> ResolvedDevelopmentScope:
    """Resolve source context and output placement for the requested operation."""
    compiled = timeline or compile_timeline(composition)
    operation = request.operation
    source = request.source

    if source is None:
        if operation == "vary_section":
            raise CompositionDevelopmentError(
                "development_source_required",
                details={"operation": operation},
            )
        base = _trailing_section_scope(composition, compiled)
        output_bars = int(request.output_bars or 0)
        output_start_bar = composition.bar_count + 1
        output_end_bar = composition.bar_count + output_bars
        return ResolvedDevelopmentScope(
            operation=operation,
            source_start_bar=base.source_start_bar,
            source_end_bar=base.source_end_bar,
            source_start_tick=base.source_start_tick,
            source_end_tick=base.source_end_tick,
            output_start_bar=output_start_bar,
            output_end_bar=output_end_bar,
            output_start_tick=composition.duration_ticks,
            output_end_tick=composition.duration_ticks,  # absolute end filled at realization
            output_bars=output_bars,
            seam_tick=composition.duration_ticks,
            source_section=base.source_section,
            prior_section=base.prior_section,
            next_section=None,
            defaulted_source=True,
        )

    start_bar: int
    end_bar: int
    section_nb: SectionNeighborhood | None = None
    section_index: int | None = None

    if source.section_id is not None:
        section, section_index = _find_section_by_id(composition, source.section_id)
        start_bar = section.start_bar
        end_bar = section.start_bar + section.bar_count - 1
        section_nb = _section_neighborhood(section, section_index)
    else:
        assert source.start_bar is not None and source.end_bar is not None
        start_bar = source.start_bar
        end_bar = source.end_bar
        if start_bar < 1 or end_bar > composition.bar_count or end_bar < start_bar:
            raise CompositionDevelopmentError(
                "development_invalid_source",
                "source bar range is out of bounds",
                details={
                    "start_bar": start_bar,
                    "end_bar": end_bar,
                    "bar_count": composition.bar_count,
                },
            )
        covered = _section_covering_bar(composition, start_bar)
        if covered is not None:
            section, section_index = covered
            section_nb = _section_neighborhood(section, section_index)

    start_tick, end_tick = compiled.bar_range_ticks(start_bar, end_bar)
    prior = None
    next_section = None
    if section_index is not None:
        if section_index > 0:
            prior = _section_neighborhood(composition.sections[section_index - 1], section_index - 1)
        if section_index + 1 < len(composition.sections):
            next_section = _section_neighborhood(
                composition.sections[section_index + 1],
                section_index + 1,
            )

    if operation == "vary_section":
        return ResolvedDevelopmentScope(
            operation=operation,
            source_start_bar=start_bar,
            source_end_bar=end_bar,
            source_start_tick=start_tick,
            source_end_tick=end_tick,
            output_start_bar=start_bar,
            output_end_bar=end_bar,
            output_start_tick=start_tick,
            output_end_tick=end_tick,
            output_bars=end_bar - start_bar + 1,
            seam_tick=start_tick,
            source_section=section_nb,
            prior_section=prior,
            next_section=next_section,
            defaulted_source=False,
        )

    output_bars = int(request.output_bars or 0)
    return ResolvedDevelopmentScope(
        operation=operation,
        source_start_bar=start_bar,
        source_end_bar=end_bar,
        source_start_tick=start_tick,
        source_end_tick=end_tick,
        output_start_bar=composition.bar_count + 1,
        output_end_bar=composition.bar_count + output_bars,
        output_start_tick=composition.duration_ticks,
        output_end_tick=composition.duration_ticks,
        output_bars=output_bars,
        seam_tick=composition.duration_ticks,
        source_section=section_nb,
        prior_section=prior,
        next_section=next_section,
        defaulted_source=False,
    )


def _events_in_half_open(
    track: CompositionV2Track,
    start_tick: int,
    end_tick: int,
) -> list[Any]:
    return [
        event
        for event in track.events
        if event.start_tick < end_tick and (event.start_tick + event.duration_ticks) > start_tick
    ]


def _relative_notes_from_events(
    events: list[Any],
    *,
    anchor_tick: int,
    limit: int = MAX_RELATIVE_NOTES_PER_TRACK,
) -> tuple[tuple[RelativeContextNote, ...], bool]:
    if not events:
        return (), False
    sorted_events = sorted(events, key=lambda event: (event.start_tick, event.pitch, event.duration_ticks))
    truncated = len(sorted_events) > limit
    selected = sorted_events[:limit]
    pitched = [event for event in selected if not getattr(event, "is_drum", False)]
    if not pitched:
        pitched = selected
    anchor_midi = midi_pitch_number(pitched[0].pitch)
    notes: list[RelativeContextNote] = []
    for event in selected:
        notes.append(
            RelativeContextNote(
                relative_start_tick=max(0, event.start_tick - anchor_tick),
                duration_ticks=event.duration_ticks,
                pitch_semitone_offset=midi_pitch_number(event.pitch) - anchor_midi,
                velocity=event.velocity,
                pitch=event.pitch,
            )
        )
    return tuple(notes), truncated


def _track_summary(
    track: CompositionV2Track,
    *,
    start_tick: int,
    end_tick: int,
) -> tuple[TrackContextSummary, bool]:
    events = _events_in_half_open(track, start_tick, end_tick)
    attacks = [event for event in events if start_tick <= event.start_tick < end_tick]
    midi_values = [midi_pitch_number(event.pitch) for event in attacks] if not track.is_drum else []
    relative_notes, truncated = _relative_notes_from_events(attacks, anchor_tick=start_tick)
    onset_ticks = tuple(sorted({event.start_tick - start_tick for event in attacks[:MAX_RELATIVE_NOTES_PER_TRACK]}))
    note_load = sum(
        max(0, min(end_tick, event.start_tick + event.duration_ticks) - max(start_tick, event.start_tick))
        for event in events
    )
    return (
        TrackContextSummary(
            track_id=track.id,
            role=track.role,
            instrument=track.instrument,
            midi_program=track.midi_program,
            channel=track.channel,
            is_drum=track.is_drum,
            pitch_min=min(midi_values) if midi_values else None,
            pitch_max=max(midi_values) if midi_values else None,
            attack_count=len(attacks),
            note_load_ticks=note_load,
            onset_ticks=onset_ticks,
            relative_notes=relative_notes,
        ),
        truncated,
    )


def _recent_harmony(
    harmony: list[CompositionV2HarmonyItem],
    *,
    start_tick: int,
    end_tick: int,
) -> tuple[tuple[dict[str, Any], ...], bool]:
    overlapping = [
        item
        for item in harmony
        if item.start_tick < end_tick and (item.start_tick + item.duration_ticks) > start_tick
    ]
    truncated = len(overlapping) > MAX_HARMONY_CONTEXT_ITEMS
    selected = overlapping[-MAX_HARMONY_CONTEXT_ITEMS:]
    payload = tuple(
        {
            "relative_start_tick": max(0, item.start_tick - start_tick),
            "duration_ticks": item.duration_ticks,
            "chord": item.chord,
        }
        for item in selected
    )
    return payload, truncated


def _motif_exemplars(
    composition: CompositionV2,
    *,
    start_tick: int,
    end_tick: int,
) -> tuple[tuple[MotifExemplar, ...], bool]:
    if not composition.motifs:
        return (), False
    event_by_id: dict[str, Any] = {}
    for track in composition.tracks:
        for event in track.events:
            if event.id:
                event_by_id[event.id] = (track.id, event)

    exemplars: list[MotifExemplar] = []
    truncated = False
    for motif in composition.motifs:
        for occurrence in motif.occurrences:
            if occurrence.relationship not in {"original", "repeat", "sequence", "answer"}:
                continue
            resolved_events = []
            for event_id in occurrence.event_ids:
                found = event_by_id.get(event_id)
                if found is None:
                    continue
                track_id, event = found
                if event.start_tick < end_tick and (event.start_tick + event.duration_ticks) > start_tick:
                    resolved_events.append(event)
            if not resolved_events:
                continue
            relative_notes, note_truncated = _relative_notes_from_events(
                resolved_events,
                anchor_tick=min(event.start_tick for event in resolved_events),
                limit=MAX_MOTIF_EXEMPLAR_NOTES,
            )
            truncated = truncated or note_truncated
            exemplars.append(
                MotifExemplar(
                    motif_id=motif.id,
                    label=motif.label,
                    occurrence_id=occurrence.id,
                    track_id=occurrence.track_id,
                    relationship=occurrence.relationship,
                    relative_notes=relative_notes,
                )
            )
            if len(exemplars) >= 8:
                truncated = True
                return tuple(exemplars), truncated
    return tuple(exemplars), truncated


def _rhythmic_profile(track_summaries: tuple[TrackContextSummary, ...], span_ticks: int) -> dict[str, float]:
    if span_ticks <= 0:
        return {
            "attacks_per_bar_approx": 0.0,
            "note_load_ratio": 0.0,
            "onset_entropy_proxy": 0.0,
        }
    total_attacks = sum(summary.attack_count for summary in track_summaries)
    total_load = sum(summary.note_load_ticks for summary in track_summaries)
    onset_buckets = set()
    for summary in track_summaries:
        for onset in summary.onset_ticks:
            onset_buckets.add(onset // max(1, span_ticks // 16))
    return {
        "attacks_per_bar_approx": round(total_attacks / max(1.0, span_ticks / 1920.0), 4),
        "note_load_ratio": round(total_load / float(span_ticks * max(1, len(track_summaries))), 4),
        "onset_entropy_proxy": round(len(onset_buckets) / 16.0, 4),
    }


def _cadence_notes(
    track_summaries: tuple[TrackContextSummary, ...],
) -> tuple[RelativeContextNote, ...]:
    """Last few relative notes from melody/lead-like tracks near the seam."""
    preferred_roles = {"melody", "lead", "countermelody"}
    candidates = [summary for summary in track_summaries if summary.role in preferred_roles]
    if not candidates:
        candidates = list(track_summaries)
    notes: list[RelativeContextNote] = []
    for summary in candidates:
        notes.extend(summary.relative_notes[-MAX_CADENCE_NOTES:])
    notes.sort(key=lambda note: note.relative_start_tick)
    return tuple(notes[-MAX_CADENCE_NOTES:])


def _advisory_analysis_projection(
    composition: CompositionV2,
    *,
    budget_chars: int,
) -> tuple[str | None, tuple[str, ...]]:
    warnings: list[str] = []
    try:
        report = analyze_composition(composition)
        text = build_llm_analysis_context(report, max_chars=min(budget_chars, 8192), purpose="development")
        if "truncated" in text.lower():
            warnings.append("context_truncated")
        return text, tuple(warnings)
    except Exception as exc:  # noqa: BLE001 — advisory only; never fail development on analysis
        logger.info(
            "Advisory analysis projection skipped",
            extra={"error_type": type(exc).__name__},
        )
        return None, ()


def build_development_source_context(
    request: CompositionDevelopmentPreviewRequest,
) -> DevelopmentSourceContext:
    """Extract immutable, bounded context for development provider prompts."""
    composition = request.composition
    timeline = compile_timeline(composition)
    scope = resolve_development_scope(composition, request, timeline)

    # Prefer trailing seam bars for append context; use selected range for variation.
    if request.operation == "vary_section":
        context_start_bar = scope.source_start_bar
        context_end_bar = scope.source_end_bar
    else:
        context_end_bar = scope.source_end_bar
        context_start_bar = max(1, context_end_bar - DEFAULT_SEAM_CONTEXT_BARS + 1)
        # Prefer source section window when it is already short.
        if scope.source_section is not None:
            context_start_bar = max(context_start_bar, scope.source_section.start_bar)

    context_start_tick, context_end_tick = timeline.bar_range_ticks(context_start_bar, context_end_bar)
    seam_state = timeline.state_at_tick(min(scope.seam_tick, max(0, composition.duration_ticks - 1)))

    truncation_reasons: list[str] = []
    warning_codes: list[str] = []

    track_summaries: list[TrackContextSummary] = []
    for track in composition.tracks:
        summary, truncated = _track_summary(track, start_tick=context_start_tick, end_tick=context_end_tick)
        track_summaries.append(summary)
        if truncated:
            truncation_reasons.append(f"track_notes:{track.id}")

    harmony, harmony_truncated = _recent_harmony(
        composition.harmony,
        start_tick=context_start_tick,
        end_tick=context_end_tick,
    )
    if harmony_truncated:
        truncation_reasons.append("harmony")
    if not composition.harmony:
        warning_codes.append("empty_harmony_context")

    motifs, motif_truncated = _motif_exemplars(
        composition,
        start_tick=context_start_tick,
        end_tick=context_end_tick,
    )
    if motif_truncated:
        truncation_reasons.append("motifs")
    if not composition.motifs:
        warning_codes.append("empty_motif_context")

    span_ticks = max(1, context_end_tick - context_start_tick)
    rhythmic_profile = _rhythmic_profile(tuple(track_summaries), span_ticks)
    cadence_notes = _cadence_notes(tuple(track_summaries))

    remaining_budget = max(1000, request.options.context_budget_chars - 2000)
    advisory, advisory_warnings = _advisory_analysis_projection(
        composition,
        budget_chars=remaining_budget,
    )
    warning_codes.extend(advisory_warnings)

    truncated = bool(truncation_reasons) or ("context_truncated" in warning_codes)
    context = DevelopmentSourceContext(
        version=DEVELOPMENT_CONTEXT_VERSION,
        composition=composition,
        timeline=timeline,
        scope=scope,
        active_tempo=seam_state.tempo,
        active_time_signature=seam_state.time_signature,
        active_key=seam_state.key,
        recent_harmony=harmony,
        track_summaries=tuple(track_summaries),
        motif_exemplars=motifs,
        rhythmic_profile=rhythmic_profile,
        cadence_notes=cadence_notes,
        advisory_analysis=advisory,
        truncated=truncated,
        truncation_reasons=tuple(truncation_reasons),
        warning_codes=tuple(dict.fromkeys(warning_codes)),
    )

    logger.info(
        "Built composition development source context",
        extra={
            "operation": request.operation,
            "intent": request.development_intent,
            "strength": request.variation_strength,
            "source_start_bar": scope.source_start_bar,
            "source_end_bar": scope.source_end_bar,
            "source_start_tick": scope.source_start_tick,
            "source_end_tick": scope.source_end_tick,
            "output_bars": scope.output_bars,
            "seam_tick": scope.seam_tick,
            "track_count": len(track_summaries),
            "harmony_count": len(harmony),
            "motif_exemplar_count": len(motifs),
            "truncated": truncated,
            "warning_code_count": len(context.warning_codes),
            "defaulted_source": scope.defaulted_source,
        },
    )
    return context


def development_context_prompt_payload(context: DevelopmentSourceContext) -> dict[str, Any]:
    """Serialize bounded context for prompts — counts and relative notes only."""
    return {
        "version": context.version,
        "active_tempo": context.active_tempo,
        "active_time_signature": context.active_time_signature,
        "active_key": context.active_key,
        "scope": {
            "operation": context.scope.operation,
            "source_start_bar": context.scope.source_start_bar,
            "source_end_bar": context.scope.source_end_bar,
            "output_bars": context.scope.output_bars,
            "seam_tick": context.scope.seam_tick,
            "source_section_type": (
                context.scope.source_section.section_type if context.scope.source_section else None
            ),
            "prior_section_type": (
                context.scope.prior_section.section_type if context.scope.prior_section else None
            ),
            "next_section_type": (
                context.scope.next_section.section_type if context.scope.next_section else None
            ),
        },
        "recent_harmony": list(context.recent_harmony),
        "tracks": [
            {
                "track_id": summary.track_id,
                "role": summary.role,
                "instrument": summary.instrument,
                "is_drum": summary.is_drum,
                "pitch_min": summary.pitch_min,
                "pitch_max": summary.pitch_max,
                "attack_count": summary.attack_count,
                "relative_notes": [
                    {
                        "relative_start_tick": note.relative_start_tick,
                        "duration_ticks": note.duration_ticks,
                        "pitch_semitone_offset": note.pitch_semitone_offset,
                        "velocity": note.velocity,
                    }
                    for note in summary.relative_notes
                ],
            }
            for summary in context.track_summaries
        ],
        "motif_exemplars": [
            {
                "motif_id": exemplar.motif_id,
                "label": exemplar.label,
                "relationship": exemplar.relationship,
                "track_id": exemplar.track_id,
                "relative_notes": [
                    {
                        "relative_start_tick": note.relative_start_tick,
                        "duration_ticks": note.duration_ticks,
                        "pitch_semitone_offset": note.pitch_semitone_offset,
                    }
                    for note in exemplar.relative_notes
                ],
            }
            for exemplar in context.motif_exemplars
        ],
        "rhythmic_profile": context.rhythmic_profile,
        "cadence_note_count": len(context.cadence_notes),
        "advisory_analysis": context.advisory_analysis,
        "truncated": context.truncated,
        "warning_codes": list(context.warning_codes),
    }
