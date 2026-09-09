"""Deterministic fake LLM provider for credit-free generate, region edit, and motif variation.

Enabled via ``LLM_FAKE_MODE=1`` (and/or provider id ``fake``). Never opens
network sockets to OpenAI/DeepSeek. Responses are fixture-backed Composition V1
documents, ``replace_region`` patches, and constrained motif proposals.
"""

from __future__ import annotations

import logging
import os

from ..llm_settings import FAKE_PROVIDER, LLMProviderSettings
from ..schemas import (
    Composition,
    CompositionEditSelection,
    CompositionRegionReplacementPatch,
    CompositionRegionTrackReplacement,
    CompositionV2,
    CompositionV2NoteEvent,
    GenerationValidationReport,
    LLMCompositionEditRequest,
    LLMMusicGenerationRequest,
)
from .composition_region_patch import (
    CompositionRegionPatchError,
    apply_region_replacement_patch,
    event_in_region,
    selection_tick_bounds,
    summarize_region_selection,
)
from .composition_timing import bar_duration_ticks
from .composition_normalizer import normalize_composition_json
from .composition_validator import _min_events_for_track
from .fixture_compositions import (
    FIXTURE_16BAR_MULTITRACK,
    FIXTURE_V2_EXPRESSIVE,
    FixtureCompositionError,
    load_composition_fixture_cached,
)
from .generation_constraints import GenerationConstraints


logger = logging.getLogger(__name__)

FAKE_MODEL_ID = "fake-deterministic"
FAKE_DISPLAY_NAME = "Fake (deterministic)"
# Env flag read by tests to force InvalidLLMOutputError without network.
FAKE_MALFORMED_ENV = "LLM_FAKE_INJECT_MALFORMED"


class FakeLLMError(RuntimeError):
    """Raised when the fake provider cannot satisfy a request."""


def is_fake_provider(provider: LLMProviderSettings | str | None) -> bool:
    if provider is None:
        return False
    name = provider.provider if isinstance(provider, LLMProviderSettings) else str(provider)
    return name.strip().lower() == FAKE_PROVIDER


async def generate_fake_music_json(
    request: LLMMusicGenerationRequest,
    provider: LLMProviderSettings,
    *,
    constraints: GenerationConstraints | None = None,
) -> tuple[CompositionV2, list[str], LLMProviderSettings, GenerationValidationReport | None]:
    """Return a fixture-backed multi-track CompositionV2 without calling a real LLM."""
    from .generation_constraints import (
        build_generation_constraints,
        validate_generation_constraints,
    )

    _maybe_inject_malformed("generate")
    active_constraints = constraints or build_generation_constraints(request)

    # Prefer native V2 expressive fixture when duration matches; otherwise 16-bar V1 migrate path.
    fixture_name = (
        FIXTURE_V2_EXPRESSIVE
        if active_constraints.duration_bars == 4
        and active_constraints.time_signature == "4/4"
        and not active_constraints.key_user_specified
        and not active_constraints.sections_user_specified
        else FIXTURE_16BAR_MULTITRACK
    )

    logger.info(
        "Fake LLM music generation started",
        extra={
            "provider": provider.provider,
            "model": provider.model,
            "duration_bars": request.prompt.duration_bars,
            "instrument_count": len(request.prompt.instruments),
            "fixture": fixture_name,
            "constraint_key": active_constraints.key,
        },
    )

    try:
        composition = load_composition_fixture_cached(fixture_name)
    except FixtureCompositionError as exc:
        logger.error(
            "Fake LLM generate failed to load fixture",
            extra={"fixture": fixture_name, "error_type": type(exc).__name__},
        )
        raise FakeLLMError(str(exc)) from exc

    # Deep copy via dump/validate so callers cannot mutate the cached fixture.
    music = CompositionV2.model_validate(composition.model_dump(mode="json"))
    warnings: list[str] = [
        "Fake LLM mode: returned deterministic fixture composition (no API credits used).",
    ]

    # Transform soft-compatible hard fields; refuse contradictory hard mismatches.
    updates: dict[str, object] = {}
    if music.tempo < active_constraints.tempo_min:
        updates["tempo"] = active_constraints.tempo_min
    elif music.tempo > active_constraints.tempo_max:
        updates["tempo"] = active_constraints.tempo_max

    if active_constraints.duration_bars != music.bar_count:
        raise FakeLLMError(
            "Fake LLM fixture cannot satisfy requested duration_bars="
            f"{active_constraints.duration_bars}; fixture has bar_count={music.bar_count}"
        )
    if active_constraints.time_signature != music.time_signature:
        raise FakeLLMError(
            "Fake LLM fixture cannot satisfy requested time_signature="
            f"{active_constraints.time_signature}; fixture has {music.time_signature}"
        )
    if active_constraints.key_user_specified and active_constraints.key != music.key:
        raise FakeLLMError(
            "Fake LLM fixture cannot satisfy requested key="
            f"{active_constraints.key}; fixture has key={music.key}"
        )
    if active_constraints.sections_user_specified and active_constraints.sections is not None:
        actual = [(section.type, section.start_bar, section.bar_count) for section in music.sections]
        expected = [
            (section.type, section.start_bar, section.bar_count) for section in active_constraints.sections
        ]
        if actual != expected:
            raise FakeLLMError(
                "Fake LLM fixture cannot satisfy requested section sequence/bar counts"
            )

    if updates:
        music = music.model_copy(update=updates)
        warnings.append(
            "Fake LLM adjusted fixture tempo into the requested inclusive bounds."
        )

    report = validate_generation_constraints(music, active_constraints)
    instrumentation = report.instrumentation
    satisfied_count = len(instrumentation.satisfied) if instrumentation else 0
    missing_count = len(instrumentation.missing) if instrumentation else 0
    duplicate_count = len(instrumentation.suspicious_duplicates) if instrumentation else 0
    actionable_duplicate_count = (
        sum(1 for item in instrumentation.suspicious_duplicates if item.actionable)
        if instrumentation
        else 0
    )
    logger.info(
        "Fake LLM instrumentation gate",
        extra={
            "status": report.status,
            "satisfied_count": satisfied_count,
            "missing_count": missing_count,
            "suspicious_duplicate_count": duplicate_count,
            "actionable_duplicate_count": actionable_duplicate_count,
            "present_identities": (
                list(instrumentation.present_identities) if instrumentation else []
            ),
        },
    )
    if not report.ok:
        codes = [item.code for item in report.errors]
        logger.warning(
            "Fake LLM fixture conformance failure",
            extra={"error_codes": codes, "status": report.status, "missing_count": missing_count},
        )
        logger.error(
            "Fake LLM fixture failed generation constraint gate",
            extra={"error_codes": codes, "status": report.status},
        )
        raise FakeLLMError(
            "Fake LLM fixture failed generation constraint validation: "
            + ", ".join(codes)
        )

    music, theme_report, theme_warnings = _apply_fake_thematic_recurrence(
        music,
        request=request,
        constraints=active_constraints,
    )
    warnings.extend(theme_warnings)
    if theme_report is not None:
        report = theme_report

    event_count = sum(len(track.events) for track in music.tracks)
    logger.info(
        "Fake LLM music generation completed",
        extra={
            "provider": provider.provider,
            "model": provider.model,
            "bar_count": music.bar_count,
            "track_count": len(music.tracks),
            "event_count": event_count,
            "warning_count": len(warnings),
            "validation_status": report.status,
            "motif_count": len(music.motifs),
        },
    )
    logger.debug(
        "Fake LLM generate fixture summary",
        extra={
            "fixture": fixture_name,
            "schema_version": music.schema_version,
            "tempo": music.tempo,
            "time_signature": music.time_signature,
            "tempo_change_count": len(music.tempo_changes),
            "articulation_note_count": sum(
                1 for track in music.tracks for event in track.events if event.articulations
            ),
            "track_ids": [track.id for track in music.tracks],
            "constraint_error_count": len(report.errors),
            "motif_count": len(music.motifs),
        },
    )
    return music, warnings, provider, report


def _apply_fake_thematic_recurrence(
    music: CompositionV2,
    *,
    request: LLMMusicGenerationRequest,
    constraints: GenerationConstraints,
) -> tuple[CompositionV2, GenerationValidationReport | None, list[str]]:
    """Inject Motif A seed + deterministic mechanical recurrence via production transforms."""
    from app.composition_schemas import (
        MOTIF_MIN_EVENT_REFS,
        CompositionV2MotifDefinition,
        CompositionV2MotifOccurrence,
        CompositionV2MotifTransformProvenance,
    )
    from app.schemas import ThematicRecurrenceOutcome
    from app.services.composition_motif_transform import (
        MotifDestinationSpec,
        MotifTransformError,
        extract_relative_motif,
        transform_transpose,
    )
    from app.services.generation_constraints import validate_generation_constraints

    warnings: list[str] = []
    instructions = (request.prompt.instructions or "").strip().lower()
    wants_theme = any(
        token in instructions for token in ("motif", "theme", "transpose", "invert", "recurring")
    )
    # Multi-section fixtures always demonstrate intentional recurrence unless single-section.
    if len(music.sections) < 2:
        logger.debug(
            "Fake thematic recurrence skipped",
            extra={"reason": "single_section", "section_count": len(music.sections)},
        )
        return music, None, warnings

    melody = next(
        (track for track in music.tracks if track.role in {"melody", "lead"} and not track.is_drum),
        None,
    )
    if melody is None or len(melody.events) < MOTIF_MIN_EVENT_REFS:
        logger.warning(
            "Fake thematic recurrence unsupported",
            extra={"code": "theme_source_empty", "has_melody": melody is not None},
        )
        return music, None, warnings

    # Ensure stable event IDs on the melody track.
    used_ids = {event.id for track in music.tracks for event in track.events if event.id}
    updated_tracks = []
    for track in music.tracks:
        if track.id != melody.id:
            updated_tracks.append(track)
            continue
        events = []
        for index, event in enumerate(track.events):
            event_id = event.id
            if not event_id:
                event_id = f"fake-mel-{index:04d}"
                suffix = 2
                while event_id in used_ids:
                    event_id = f"fake-mel-{index:04d}-{suffix}"
                    suffix += 1
                used_ids.add(event_id)
                event = event.model_copy(update={"id": event_id})
            events.append(event)
        melody = track.model_copy(update={"events": events})
        updated_tracks.append(melody)
    music = music.model_copy(update={"tracks": updated_tracks})
    melody = next(track for track in music.tracks if track.id == melody.id)

    seed_section = music.sections[0]
    seed_start = seed_section.start_tick
    seed_end = seed_section.start_tick + seed_section.duration_ticks
    seed_events = [
        event
        for event in melody.events
        if event.start_tick >= seed_start and event.start_tick < seed_end and event.id
    ]
    seed_events = sorted(seed_events, key=lambda item: (item.start_tick, item.duration_ticks, item.id or ""))
    if len(seed_events) < MOTIF_MIN_EVENT_REFS:
        # Fall back to first N melody events.
        seed_events = sorted(melody.events, key=lambda item: (item.start_tick, item.id or ""))[:4]
    seed_events = seed_events[:8]
    if len(seed_events) < MOTIF_MIN_EVENT_REFS:
        logger.warning(
            "Fake thematic recurrence skipped",
            extra={"code": "theme_source_empty", "seed_count": len(seed_events)},
        )
        return music, None, warnings

    seed_ids = [event.id for event in seed_events if event.id]
    target_section = music.sections[min(len(music.sections) - 1, 1 if len(music.sections) > 1 else 0)]
    if target_section.id == seed_section.id and len(music.sections) > 1:
        target_section = music.sections[-1]
    target_start = target_section.start_tick
    transpose_semitones = 5

    logger.debug(
        "Fake thematic recurrence started",
        extra={
            "scenario": "transpose_recurrence",
            "seed_event_count": len(seed_ids),
            "operation": "transpose",
            "wants_theme_instructions": wants_theme,
            "target_section_start_bar": target_section.start_bar,
        },
    )

    try:
        extracted = extract_relative_motif(music, track_id=melody.id, event_ids=seed_ids)
        destination = MotifDestinationSpec(
            track=melody,
            start_tick=target_start,
            duration_limit=music.duration_ticks,
            anchor_midi=extracted.anchor_midi,
            existing_event_ids=frozenset(seed_ids),
            allow_overlap=True,
        )
        transform_result = transform_transpose(
            extracted.notes,
            semitones=transpose_semitones,
            composition=music,
            destination=destination,
            id_seed="fake-theme-a",
        )
    except MotifTransformError as exc:
        logger.warning(
            "Fake thematic recurrence failed",
            extra={"code": getattr(exc, "code", "theme_transform_mismatch")},
        )
        warnings.append("Fake LLM could not apply thematic recurrence; returned fixture notes only.")
        return music, None, warnings

    span_end = target_start + transform_result.span_ticks
    kept = [
        event
        for event in melody.events
        if not (
            event.id not in seed_ids
            and event.start_tick < span_end
            and (event.start_tick + event.duration_ticks) > target_start
        )
    ]
    new_events = list(transform_result.events)
    merged = sorted(
        [*kept, *new_events],
        key=lambda item: (item.start_tick, item.duration_ticks, item.id or ""),
    )
    updated_melody = melody.model_copy(update={"events": merged})
    tracks = [updated_melody if track.id == melody.id else track for track in music.tracks]
    created_ids = [event.id for event in new_events if event.id]
    motif = CompositionV2MotifDefinition(
        id="motif-a",
        label="Motif A",
        occurrences=[
            CompositionV2MotifOccurrence(
                id="motif-a-original",
                track_id=melody.id,
                event_ids=seed_ids,
                relationship="original",
            ),
            CompositionV2MotifOccurrence(
                id="motif-a-dep-transpose-1",
                track_id=melody.id,
                event_ids=created_ids,
                relationship="transpose",
                transform=CompositionV2MotifTransformProvenance(
                    operation="transpose",
                    transpose_semitones=transpose_semitones,
                ),
            ),
        ],
    )
    music = music.model_copy(update={"tracks": tracks, "motifs": [motif]})
    score = transform_result.verification.components.combined_score
    logger.info(
        "Fake LLM deterministic thematic recurrence applied",
        extra={
            "motif_id": motif.id,
            "operation": "transpose",
            "identity_score": score,
            "created_event_count": len(created_ids),
            "seed_event_count": len(seed_ids),
        },
    )
    warnings.append(
        "Fake LLM mode: applied deterministic Motif A transpose recurrence via production transforms."
    )

    report = validate_generation_constraints(music, constraints)
    report = report.model_copy(
        update={
            "thematic": [
                ThematicRecurrenceOutcome(
                    deployment_id="dep-transpose-1",
                    operation="transpose",
                    identity_score=score,
                    status="realized",
                )
            ]
        }
    )
    return music, report, warnings


async def edit_fake_composition_region(
    request: LLMCompositionEditRequest,
    provider: LLMProviderSettings,
) -> tuple[CompositionV2, CompositionRegionReplacementPatch, list[str], LLMProviderSettings]:
    """Build and apply a deterministic replace_region patch for the selection."""
    _maybe_inject_malformed("edit")

    selection = request.edit.selection
    logger.info(
        "Fake LLM region edit started",
        extra={
            "provider": provider.provider,
            "model": provider.model,
            "start_bar": selection.start_bar,
            "end_bar": selection.end_bar,
            "target_track_count": len(selection.track_ids or []),
            "bar_count": request.composition.bar_count,
        },
    )

    composition = normalize_composition_json(request.composition)
    scope = summarize_region_selection(composition, selection)
    patch = _build_deterministic_region_patch(
        composition,
        scope.target_track_ids,
        selection.start_bar,
        selection.end_bar,
    )

    logger.debug(
        "Fake LLM region patch built",
        extra={
            "start_bar": patch.start_bar,
            "end_bar": patch.end_bar,
            "target_track_ids": patch.target_track_ids,
            "replace_track_count": len(patch.replace_tracks),
            "replacement_event_count": sum(len(track.events) for track in patch.replace_tracks),
            "in_region_event_count": scope.total_in_region_events,
        },
    )

    result = apply_region_replacement_patch(
        composition,
        patch,
        allow_harmony_changes=request.edit.allow_harmony_changes,
        allow_added_tracks=request.edit.allow_added_tracks,
    )
    warnings = [
        "Fake LLM mode: applied deterministic region patch (no API credits used).",
        *result.warnings,
        *patch.warnings,
    ]
    # Preserve order while dropping exact duplicate messages (avoids React key collisions in UI).
    deduped: list[str] = []
    seen: set[str] = set()
    for warning in warnings:
        if warning in seen:
            continue
        seen.add(warning)
        deduped.append(warning)
    warnings = deduped
    logger.info(
        "Fake LLM region edit completed",
        extra={
            "provider": provider.provider,
            "model": provider.model,
            "replaced_event_count": result.replaced_event_count,
            "preserved_event_count": result.preserved_event_count,
            "warning_count": len(warnings),
        },
    )
    return normalize_composition_json(result.composition), result.patch, warnings, provider


def _build_deterministic_region_patch(
    composition: Composition | CompositionV2,
    target_track_ids: list[str],
    start_bar: int,
    end_bar: int,
) -> CompositionRegionReplacementPatch:
    """Replace in-region notes with a transposed, clearly different pattern.

    Emits enough notes per track so post-apply integrity density checks still
    pass when the selection is a short window of a short composition (e.g. bars
    1–2 of the 4-bar V2 expressive fixture).
    """
    selection = CompositionEditSelection(
        start_bar=start_bar,
        end_bar=end_bar,
        track_ids=list(target_track_ids),
    )
    bounds = selection_tick_bounds(composition, selection)
    bar_ticks = bar_duration_ticks(composition.time_signature, composition.ticks_per_quarter)
    selected_bars = end_bar - start_bar + 1
    replace_tracks: list[CompositionRegionTrackReplacement] = []

    for track in composition.tracks:
        if track.id not in target_track_ids:
            continue
        outside_count = sum(1 for event in track.events if not event_in_region(event, bounds))
        min_events = _min_events_for_track(composition.bar_count, "moderate", track.role)
        needed_in_region = max(selected_bars, min_events - outside_count)
        notes_per_bar = max(1, (needed_in_region + selected_bars - 1) // selected_bars)
        slot_ticks = max(1, min(composition.ticks_per_quarter, bar_ticks // notes_per_bar))
        logger.debug(
            "[FIX:fake-region-density] Planning replacement note density",
            extra={
                "track_id": track.id,
                "role": track.role,
                "outside_count": outside_count,
                "min_events": min_events,
                "needed_in_region": needed_in_region,
                "notes_per_bar": notes_per_bar,
                "selected_bars": selected_bars,
            },
        )
        new_events: list[CompositionV2NoteEvent] = []
        for bar in range(start_bar, end_bar + 1):
            bar_start = bounds.start_tick + (bar - start_bar) * bar_ticks
            for slot in range(notes_per_bar):
                start_tick = bar_start + slot * slot_ticks
                if start_tick >= bounds.end_tick:
                    break
                duration = min(slot_ticks, bounds.end_tick - start_tick, bar_start + bar_ticks - start_tick)
                if duration <= 0:
                    continue
                pitch = _shifted_pitch_for_track(track.role, bar + slot)
                articulations = ["accent"] if bar == start_bar and slot == 0 else []
                new_events.append(
                    CompositionV2NoteEvent(
                        type="note",
                        pitch=pitch,
                        start_tick=start_tick,
                        duration_ticks=duration,
                        velocity=100,
                        articulations=articulations,
                        tie=None,
                    )
                )
        replace_tracks.append(
            CompositionRegionTrackReplacement(track_id=track.id, events=new_events)
        )

    return CompositionRegionReplacementPatch(
        schema_version="composition.v2",
        operation="replace_region",
        start_bar=start_bar,
        end_bar=end_bar,
        target_track_ids=list(target_track_ids),
        replace_tracks=replace_tracks,
        warnings=["Fake region patch replaces in-region events with deterministic notes."],
    )


def _shifted_pitch_for_track(role: str, bar: int) -> str:
    """Pick a deterministic pitch that differs from the usual fixture pattern."""
    role_l = (role or "").lower()
    if role_l == "bass":
        pitches = ["Bb1", "C2", "Db2", "Eb2"]
    elif role_l in {"harmony", "pad", "rhythm"}:
        pitches = ["Bb3", "C4", "Db4", "Eb4"]
    else:
        pitches = ["Bb4", "C5", "Db5", "Eb5"]
    return pitches[(bar - 1) % len(pitches)]


async def draft_fake_motif_variation(
    *,
    operation: str,
    source_notes,
    composition: CompositionV2,
    destination,
    id_seed: str,
    variation_strength: float,
    provider: LLMProviderSettings,
):
    """Build deterministic constrained motif proposals and realize via production path."""
    from app.services.composition_motif_transform import (
        AnswerCounterphraseProposal,
        MelodicVariationProposal,
        RhythmicVariationProposal,
        realize_melodic_variation,
        realize_rhythmic_variation,
        validate_answer_or_counterphrase,
    )
    from app.services.llm_motif_editor import CreativeMotifDraftOutcome

    _maybe_inject_malformed("motif")
    strength = max(0.0, min(1.0, float(variation_strength)))
    note_count = len(source_notes)
    grid = max(1, composition.ticks_per_quarter // 4)
    nudge = max(1, grid // 4)
    logger.info(
        "Fake LLM motif variation started",
        extra={
            "provider": provider.provider,
            "model": provider.model,
            "operation": operation,
            "variation_strength": strength,
            "source_note_count": note_count,
            "destination_track_id": destination.track.id,
        },
    )

    if operation == "rhythmic_variation":
        onset = [0]
        durations: list[int] = []
        for index, note in enumerate(source_notes):
            if index > 0:
                delta = nudge if index % 2 else -nudge
                onset.append(max(0, note.relative_start_tick + delta))
            durations.append(max(grid, note.duration_ticks + (nudge if index % 2 else -nudge // 2)))
        for index in range(1, len(onset)):
            onset[index] = max(onset[index], onset[index - 1])
        proposal = RhythmicVariationProposal(
            note_count=note_count,
            onset_delta_ticks=onset,
            duration_ticks=durations,
        )
        transform_result = realize_rhythmic_variation(
            source_notes,
            proposal,
            composition=composition,
            destination=destination,
            id_seed=id_seed,
            variation_strength=strength,
        )
    elif operation == "melodic_variation":
        pattern = [0, 1, 0, -1]
        offsets = [pattern[index % len(pattern)] for index in range(note_count)]
        proposal = MelodicVariationProposal(note_count=note_count, pitch_semitone_offsets=offsets)
        transform_result = realize_melodic_variation(
            source_notes,
            proposal,
            composition=composition,
            destination=destination,
            id_seed=id_seed,
            variation_strength=strength,
        )
    elif operation in {"answer", "counterphrase"}:
        pattern = [0, 1, 0, -1] if operation == "answer" else [0, -1, 1, 0]
        offsets = [pattern[index % len(pattern)] for index in range(note_count)]
        mild_onset = None
        mild_duration = None
        if strength >= 0.4:
            mild_onset = [0]
            mild_duration = []
            for index, note in enumerate(source_notes):
                if index > 0:
                    mild_onset.append(max(0, note.relative_start_tick + (nudge if index % 2 else 0)))
                mild_duration.append(max(grid, note.duration_ticks))
            for index in range(1, len(mild_onset)):
                mild_onset[index] = max(mild_onset[index], mild_onset[index - 1])
        proposal = AnswerCounterphraseProposal(
            note_count=note_count,
            pitch_semitone_offsets=offsets,
            onset_delta_ticks=mild_onset,
            duration_ticks=mild_duration,
        )
        transform_result = validate_answer_or_counterphrase(
            source_notes,
            proposal,
            operation=operation,  # type: ignore[arg-type]
            composition=composition,
            destination=destination,
            id_seed=id_seed,
            variation_strength=strength,
        )
    else:
        raise FakeLLMError(f"Unsupported fake motif operation: {operation}")

    warnings = [
        "Fake LLM mode: applied deterministic motif variation (no API credits used).",
        *transform_result.warning_codes,
    ]
    logger.info(
        "Fake LLM motif variation completed",
        extra={
            "provider": provider.provider,
            "model": provider.model,
            "operation": operation,
            "created_event_count": len(transform_result.events),
            "identity_score": transform_result.verification.components.combined_score,
        },
    )
    return CreativeMotifDraftOutcome(
        transform_result=transform_result,
        warnings=tuple(warnings),
        provider=provider,
    )


async def draft_fake_reharmonization(request, provider: LLMProviderSettings):
    """Deterministic fake reharmonization for bars 9–12 tension acceptance."""
    from app.harmony_schemas import ReharmonizePreviewRequest
    from app.services.composition_reharmonization import preview_reharmonization

    if not isinstance(request, ReharmonizePreviewRequest):
        raise FakeLLMError("Fake reharmonization requires ReharmonizePreviewRequest")

    _maybe_inject_malformed("reharmonize")
    logger.info(
        "Fake LLM reharmonization started",
        extra={
            "provider": provider.provider,
            "operation": request.operation,
            "content_policy": request.content_policy,
            "start_bar": request.selection.start_bar,
            "end_bar": request.selection.end_bar,
            "target_count": len(request.target_track_ids),
        },
    )
    # Realize through the production deterministic engine so fake never bypasses checks.
    response = preview_reharmonization(request.model_copy(update={"engine": "deterministic"}))
    stamped = response.model_copy(
        update={
            "provider": provider.provider,
            "model": provider.model or FAKE_MODEL_ID,
            "warnings": [*response.warnings, "fake_provider_deterministic_realization"][:32],
        }
    )
    logger.info(
        "Fake LLM reharmonization completed",
        extra={
            "provider": provider.provider,
            "changed_span_count": len(stamped.harmony_changes),
            "changed_track_count": sum(1 for item in stamped.track_changes if item.events_changed > 0),
            "compatibility_status": stamped.compatibility.status,
        },
    )
    return stamped


def _maybe_inject_malformed(stage: str) -> None:
    """Optionally raise InvalidLLMOutputError for safety tests (no network)."""
    # Local import avoids circular import at module load with llm_music_generator.
    from .llm_music_generator import InvalidLLMOutputError

    raw = os.environ.get(FAKE_MALFORMED_ENV, "").strip().lower()
    if raw in {"1", "true", "yes", "generate", "edit", "motif", "reharmonize", "all"}:
        if raw in {"1", "true", "yes", "all"} or raw == stage:
            logger.warning(
                "Fake LLM injecting malformed output for tests",
                extra={"stage": stage, "env": FAKE_MALFORMED_ENV},
            )
            raise InvalidLLMOutputError(
                f"Fake LLM injected malformed output during {stage} (LLM_FAKE_INJECT_MALFORMED)"
            )
