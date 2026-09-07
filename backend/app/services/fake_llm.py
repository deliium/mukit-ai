"""Deterministic fake LLM provider for credit-free generate and region edit.

Enabled via ``LLM_FAKE_MODE=1`` (and/or provider id ``fake``). Never opens
network sockets to OpenAI/DeepSeek. Responses are fixture-backed Composition V1
documents and ``replace_region`` patches.
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
    GenerationValidationReport,
    LLMCompositionEditRequest,
    LLMMusicGenerationRequest,
    NoteEvent,
)
from .composition_region_patch import (
    apply_region_replacement_patch,
    selection_tick_bounds,
    summarize_region_selection,
)
from .composition_timing import bar_duration_ticks
from .fixture_compositions import (
    FIXTURE_16BAR_MULTITRACK,
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
) -> tuple[Composition, list[str], LLMProviderSettings, GenerationValidationReport | None]:
    """Return a fixture-backed multi-track Composition without calling a real LLM."""
    from .generation_constraints import (
        build_generation_constraints,
        validate_generation_constraints,
    )

    _maybe_inject_malformed("generate")
    active_constraints = constraints or build_generation_constraints(request)

    logger.info(
        "Fake LLM music generation started",
        extra={
            "provider": provider.provider,
            "model": provider.model,
            "duration_bars": request.prompt.duration_bars,
            "instrument_count": len(request.prompt.instruments),
            "fixture": FIXTURE_16BAR_MULTITRACK,
            "constraint_key": active_constraints.key,
        },
    )

    try:
        composition = load_composition_fixture_cached(FIXTURE_16BAR_MULTITRACK)
    except FixtureCompositionError as exc:
        logger.error(
            "Fake LLM generate failed to load fixture",
            extra={"fixture": FIXTURE_16BAR_MULTITRACK, "error_type": type(exc).__name__},
        )
        raise FakeLLMError(str(exc)) from exc

    # Deep copy via dump/validate so callers cannot mutate the cached fixture.
    music = Composition.model_validate(composition.model_dump(mode="json"))
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
    if not report.ok:
        codes = [item.code for item in report.errors]
        logger.error(
            "Fake LLM fixture failed generation constraint gate",
            extra={"error_codes": codes, "status": report.status},
        )
        raise FakeLLMError(
            "Fake LLM fixture failed generation constraint validation: "
            + ", ".join(codes)
        )

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
        },
    )
    logger.debug(
        "Fake LLM generate fixture summary",
        extra={
            "fixture": FIXTURE_16BAR_MULTITRACK,
            "tempo": music.tempo,
            "time_signature": music.time_signature,
            "track_ids": [track.id for track in music.tracks],
            "constraint_error_count": len(report.errors),
        },
    )
    return music, warnings, provider, report


async def edit_fake_composition_region(
    request: LLMCompositionEditRequest,
    provider: LLMProviderSettings,
) -> tuple[Composition, CompositionRegionReplacementPatch, list[str], LLMProviderSettings]:
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

    scope = summarize_region_selection(request.composition, selection)
    patch = _build_deterministic_region_patch(
        request.composition,
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
        request.composition,
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
    return result.composition, result.patch, warnings, provider


def _build_deterministic_region_patch(
    composition: Composition,
    target_track_ids: list[str],
    start_bar: int,
    end_bar: int,
) -> CompositionRegionReplacementPatch:
    """Replace in-region notes with a transposed, clearly different pattern."""
    selection = CompositionEditSelection(
        start_bar=start_bar,
        end_bar=end_bar,
        track_ids=list(target_track_ids),
    )
    bounds = selection_tick_bounds(composition, selection)
    bar_ticks = bar_duration_ticks(composition.time_signature, composition.ticks_per_quarter)
    replace_tracks: list[CompositionRegionTrackReplacement] = []

    for track in composition.tracks:
        if track.id not in target_track_ids:
            continue
        new_events: list[NoteEvent] = []
        for bar in range(start_bar, end_bar + 1):
            bar_start = bounds.start_tick + (bar - start_bar) * bar_ticks
            pitch = _shifted_pitch_for_track(track.role, bar)
            duration = min(composition.ticks_per_quarter, bar_ticks)
            if bar_start + duration > bounds.end_tick:
                duration = max(1, bounds.end_tick - bar_start)
            if duration <= 0:
                continue
            new_events.append(
                NoteEvent(
                    type="note",
                    pitch=pitch,
                    start_tick=bar_start,
                    duration_ticks=duration,
                    velocity=100,
                )
            )
        replace_tracks.append(
            CompositionRegionTrackReplacement(track_id=track.id, events=new_events)
        )

    return CompositionRegionReplacementPatch(
        schema_version="composition.v1",
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


def _maybe_inject_malformed(stage: str) -> None:
    """Optionally raise InvalidLLMOutputError for safety tests (no network)."""
    # Local import avoids circular import at module load with llm_music_generator.
    from .llm_music_generator import InvalidLLMOutputError

    raw = os.environ.get(FAKE_MALFORMED_ENV, "").strip().lower()
    if raw in {"1", "true", "yes", "generate", "edit", "all"}:
        if raw in {"1", "true", "yes", "all"} or raw == stage:
            logger.warning(
                "Fake LLM injecting malformed output for tests",
                extra={"stage": stage, "env": FAKE_MALFORMED_ENV},
            )
            raise InvalidLLMOutputError(
                f"Fake LLM injected malformed output during {stage} (LLM_FAKE_INJECT_MALFORMED)"
            )
