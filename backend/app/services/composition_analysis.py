"""Deterministic composition.analysis.v1 orchestrator.

Fixed pipeline order over validated V2. Analyzers are pure with respect to the
input composition: before/after canonical dumps must remain equal.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.analysis_schemas import (
    ANALYSIS_ALGORITHM_VERSION,
    ANALYSIS_MAX_SECTION_SUMMARIES,
    AnalysisReportStatus,
    CompositionAnalysisError,
    CompositionAnalysisReport,
    CompositionAnalysisScope,
    DensityAnalysisResult,
    HarmonyAnalysisResult,
    MelodyAnalysisResult,
    RepetitionAnalysisResult,
    RoleAnalysisResult,
    ScaleDegreeAnalysisResult,
    SectionSummaryResult,
    TensionAnalysisResult,
    TonalityAnalysisResult,
    fingerprint_log_prefix,
    prepare_analysis_request,
)
from app.composition_schemas import CompositionV2
from app.services.composition_analysis_context import (
    CompositionAnalysisContext,
    build_analysis_context,
)
from app.services.composition_analysis_warnings import (
    WarningEvaluationInput,
    evaluate_analysis_warnings,
)
from app.services.composition_density_analysis import analyze_density_from_context
from app.services.composition_fingerprint import composition_source_fingerprint
from app.services.composition_harmony_analysis import (
    analyze_harmony_from_context,
    analyze_scale_degrees_from_context,
)
from app.services.composition_logical_notes import attack_in_interval, clip_occupancy
from app.services.composition_melody_analysis import analyze_melody_from_context
from app.services.composition_repetition_analysis import analyze_repetition_from_context
from app.services.composition_role_analysis import (
    analyze_roles_from_context,
    melodic_role_hints_from_roles,
)
from app.services.composition_tension_analysis import analyze_tension_from_context
from app.services.composition_tonality import infer_tonality_from_context


logger = logging.getLogger(__name__)

# Named serialized budget for advisory LLM projections (4–8 KB band).
LLM_ANALYSIS_CONTEXT_MAX_CHARS = 6144
LLM_ANALYSIS_CONTEXT_ADVISORY_HEADER = (
    "ADVISORY musical analysis (derived, non-authoritative). "
    "Hard constraints, user instructions, canonical note events, and deterministic "
    "patch/validation remain the source of truth. Treat inferences as heuristics with "
    "confidence/status; do not invent notes from analysis or override hard rules."
)


def analyze_composition(
    composition: CompositionV2 | dict[str, Any],
    scope: CompositionAnalysisScope | dict[str, Any] | None = None,
) -> CompositionAnalysisReport:
    """Run the fixed-order analysis pipeline and return a sidecar report.

    Raises ``CompositionAnalysisError`` for invalid V2 / scope. Musical warnings
    and insufficient evidence remain non-blocking (report status may be partial
    or empty) rather than HTTP failures.
    """
    started = time.perf_counter()
    stage = "validate_fingerprint"
    before_dump: Any = None
    try:
        logger.debug(
            "Analysis stage start",
            extra={"stage": stage, "algorithm_version": ANALYSIS_ALGORITHM_VERSION},
        )
        validated, _parsed_scope, resolved = prepare_analysis_request(composition, scope)
        before_dump = validated.model_dump(mode="json")
        fingerprint = composition_source_fingerprint(validated)
        logger.debug(
            "Analysis stage end",
            extra={
                "stage": stage,
                "fingerprint_prefix": fingerprint_log_prefix(fingerprint),
                "scope_kind": resolved.kind,
            },
        )

        stage = "build_context"
        logger.debug("Analysis stage start", extra={"stage": stage})
        context = build_analysis_context(validated, scope)
        # Prefer fingerprint from context (same algorithm) for report identity.
        fingerprint = context.source_fingerprint
        logger.debug(
            "Analysis stage end",
            extra={
                "stage": stage,
                "elapsed_ms": context.build_elapsed_ms,
                "scoped_note_count": len(context.scoped_logical_notes),
            },
        )

        stage = "tonality"
        logger.debug("Analysis stage start", extra={"stage": stage})
        tonality = infer_tonality_from_context(context)
        logger.debug(
            "Analysis stage end",
            extra={"stage": stage, "status": tonality.inference.status},
        )

        stage = "harmony"
        logger.debug("Analysis stage start", extra={"stage": stage})
        harmony = analyze_harmony_from_context(context, tonality)
        scale_degrees = analyze_scale_degrees_from_context(context, tonality)
        logger.debug(
            "Analysis stage end",
            extra={
                "stage": stage,
                "status": harmony.inference.status,
                "span_count": len(harmony.spans),
            },
        )

        stage = "initial_roles"
        logger.debug("Analysis stage start", extra={"stage": stage})
        roles = analyze_roles_from_context(context)
        role_hints = melodic_role_hints_from_roles(roles)
        logger.debug(
            "Analysis stage end",
            extra={
                "stage": stage,
                "status": roles.inference.status,
                "track_count": len(roles.tracks),
                "hint_feature_count": len(role_hints.features),
            },
        )

        stage = "melody"
        logger.debug("Analysis stage start", extra={"stage": stage})
        melody = analyze_melody_from_context(
            context,
            tonality=tonality,
            harmony=harmony,
            role_hints=role_hints,
        )
        logger.debug(
            "Analysis stage end",
            extra={
                "stage": stage,
                "status": melody.inference.status,
                "profile_count": len(melody.profiles),
            },
        )

        stage = "final_roles"
        logger.debug("Analysis stage start", extra={"stage": stage})
        if _needs_final_roles(roles, melody):
            roles = analyze_roles_from_context(context)
            logger.debug(
                "Final roles recomputed",
                extra={"stage": stage, "status": roles.inference.status},
            )
        else:
            logger.debug(
                "Final roles skipped",
                extra={"stage": stage, "status": roles.inference.status},
            )

        stage = "density"
        logger.debug("Analysis stage start", extra={"stage": stage})
        density = analyze_density_from_context(context, harmony)
        logger.debug(
            "Analysis stage end",
            extra={"stage": stage, "status": density.inference.status},
        )

        stage = "repetition"
        logger.debug("Analysis stage start", extra={"stage": stage})
        repetition = analyze_repetition_from_context(context)
        logger.debug(
            "Analysis stage end",
            extra={
                "stage": stage,
                "status": repetition.inference.status,
                "motif_count": len(repetition.motifs),
            },
        )

        stage = "tension"
        logger.debug("Analysis stage start", extra={"stage": stage})
        tension = analyze_tension_from_context(
            context,
            tonality=tonality,
            harmony=harmony,
        )
        logger.debug(
            "Analysis stage end",
            extra={"stage": stage, "status": tension.inference.status},
        )

        stage = "warnings"
        logger.debug("Analysis stage start", extra={"stage": stage})
        warnings = evaluate_analysis_warnings(
            WarningEvaluationInput(
                context=context,
                tonality=tonality,
                harmony=harmony,
                melody=melody,
                density=density,
                roles=roles,
                repetition=repetition,
            )
        )
        logger.debug(
            "Analysis stage end",
            extra={"stage": stage, "warning_count": len(warnings)},
        )

        stage = "assemble"
        logger.debug("Analysis stage start", extra={"stage": stage})
        section_summaries = _build_section_summaries(context, tonality, density)
        status = _resolve_report_status(
            context,
            tonality,
            harmony,
            melody,
            density,
            roles,
            repetition,
            tension,
            warnings,
        )
        report = CompositionAnalysisReport(
            algorithm_version=ANALYSIS_ALGORITHM_VERSION,
            source_fingerprint=fingerprint,
            status=status,
            resolved_scope=context.resolved_scope,
            tonality=tonality,
            harmony=harmony,
            scale_degrees=scale_degrees,
            melody=melody,
            density=density,
            roles=roles,
            repetition=repetition,
            tension=tension,
            section_summaries=section_summaries,
            warnings=warnings,
        )

        after_dump = validated.model_dump(mode="json")
        if after_dump != before_dump:
            logger.error(
                "Analysis mutated input composition",
                extra={"stage": stage, "error_type": "InputMutationError"},
            )
            raise CompositionAnalysisError(
                "analysis_internal_error",
                "Analysis mutated the input composition",
                details={"reason": "input_mutation"},
            )

        # Re-check via context composition reference as well.
        context_dump = context.composition.model_dump(mode="json")
        if context_dump != before_dump:
            logger.error(
                "Analysis mutated context composition",
                extra={"stage": stage, "error_type": "InputMutationError"},
            )
            raise CompositionAnalysisError(
                "analysis_internal_error",
                "Analysis mutated the context composition",
                details={"reason": "context_mutation"},
            )

        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
        warning_code_counts: dict[str, int] = {}
        for item in warnings:
            warning_code_counts[item.code] = warning_code_counts.get(item.code, 0) + 1
        logger.info(
            "Composition analysis complete",
            extra={
                "algorithm_version": ANALYSIS_ALGORITHM_VERSION,
                "fingerprint_prefix": fingerprint_log_prefix(fingerprint),
                "scope_kind": context.resolved_scope.kind,
                "status": status,
                "warning_count": len(warnings),
                "warning_code_counts": warning_code_counts,
                "elapsed_ms": elapsed_ms,
                "section_summary_count": len(section_summaries),
            },
        )
        logger.debug("Analysis stage end", extra={"stage": stage, "status": status})
        return report
    except CompositionAnalysisError:
        raise
    except Exception as exc:
        logger.error(
            "Composition analysis failed",
            extra={"stage": stage, "error_type": type(exc).__name__},
        )
        raise CompositionAnalysisError(
            "analysis_internal_error",
            "Unexpected analysis failure",
            details={"reason": "internal_error", "stage": stage, "error_type": type(exc).__name__},
        ) from exc


def build_llm_analysis_context(
    report: CompositionAnalysisReport,
    *,
    max_chars: int = LLM_ANALYSIS_CONTEXT_MAX_CHARS,
    purpose: str = "advisory",
) -> str:
    """Project a bounded advisory analysis summary for LLM prompts.

    Priority-ordered facts only; never dumps full report arrays or event payloads.
    Explicit truncation marker when the named character budget is exceeded.
    """
    budget = max(256, min(int(max_chars), 8192))
    lines: list[str] = [
        LLM_ANALYSIS_CONTEXT_ADVISORY_HEADER,
        (
            f"purpose={purpose}; schema={report.schema_version}; "
            f"algorithm={report.algorithm_version}; status={report.status}; "
            f"scope={report.resolved_scope.kind}; "
            f"bars={report.resolved_scope.start_bar}..{report.resolved_scope.end_bar_exclusive - 1}; "
            f"ticks=[{report.resolved_scope.start_tick},{report.resolved_scope.end_tick})"
        ),
    ]
    if report.resolved_scope.track_id:
        lines.append(f"scope_track_id={report.resolved_scope.track_id}")
    if report.resolved_scope.section_index is not None:
        lines.append(
            f"scope_section_index={report.resolved_scope.section_index}"
            + (
                f"; scope_section_id={report.resolved_scope.section_id}"
                if report.resolved_scope.section_id
                else ""
            )
        )

    tonality = report.tonality
    global_key = tonality.global_key.key if tonality.global_key else None
    lines.append(
        "tonality: "
        f"declared={tonality.declared_key!r}; effective={tonality.effective_key!r}; "
        f"inferred_global={global_key!r}; status={tonality.inference.status}; "
        f"confidence={tonality.inference.confidence}"
    )
    local_bits: list[str] = []
    for span in tonality.local_spans[:4]:
        local_bits.append(
            f"{span.key or 'abstain'}@{span.start_bar}-{span.end_bar_exclusive - 1}"
            f"({span.inference.status})"
        )
    if local_bits:
        lines.append("local_keys: " + "; ".join(local_bits))

    warning_codes = [item.code for item in report.warnings[:12]]
    if warning_codes:
        lines.append("warning_codes: " + ", ".join(warning_codes))
    elif report.warnings:
        lines.append(f"warning_codes: (truncated list; count={len(report.warnings)})")

    harmony = report.harmony
    rhythm = harmony.harmonic_rhythm
    lines.append(
        "harmony: "
        f"status={harmony.inference.status}; declared_agreement={harmony.declared_agreement}; "
        f"span_count={len(harmony.spans)}; "
        f"changes_per_bar={rhythm.changes_per_bar}; unique_chords={rhythm.unique_chord_count}"
    )
    chord_bits: list[str] = []
    for span in harmony.spans[:6]:
        chord_bits.append(f"{span.symbol or span.quality or 'abstain'}@{span.start_tick}-{span.end_tick}")
    if chord_bits:
        lines.append("chord_spans_sample: " + "; ".join(chord_bits))

    melody = report.melody
    profile_bits: list[str] = []
    for profile in melody.profiles[:4]:
        profile_bits.append(
            f"{profile.track_id or '?'}:contour={profile.contour};"
            f"range={profile.pitch_min}-{profile.pitch_max};"
            f"status={profile.inference.status}"
        )
    if profile_bits:
        lines.append("melody_profiles: " + "; ".join(profile_bits))
    cadence_bits = [
        f"{item.kind}@{item.tick}"
        for item in melody.cadences[:4]
    ]
    if cadence_bits:
        lines.append("cadences_sample: " + "; ".join(cadence_bits))
    lines.append(
        f"phrases: count={len(melody.phrases)}; melody_status={melody.inference.status}"
    )

    density = report.density.metrics
    lines.append(
        "density: "
        f"attacks_per_bar={density.attacks_per_bar}; note_load={density.note_load}; "
        f"active_union={density.active_time_union_ratio}; "
        f"max_simultaneity={density.max_simultaneity}; status={report.density.inference.status}"
    )

    role_bits: list[str] = []
    for track in report.roles.tracks[:8]:
        role_bits.append(
            f"{track.track_id}:declared={track.declared_role};"
            f"inferred={track.inferred_role};effective={track.effective_role}"
        )
    if role_bits:
        lines.append("roles: " + "; ".join(role_bits))

    tension = report.tension.components
    lines.append(
        "tension: "
        f"vertical={tension.vertical_dissonance}; chromatic={tension.chromatic_mass}; "
        f"nct={tension.non_chord_tone_mass}; status={report.tension.inference.status}"
    )

    repetition = report.repetition
    lines.append(
        "repetition: "
        f"motif_count={len(repetition.motifs)}; "
        f"section_fingerprint_count={len(repetition.section_fingerprint_ids)}; "
        f"status={repetition.inference.status}"
    )

    section_bits: list[str] = []
    for summary in report.section_summaries[:6]:
        section_bits.append(
            f"idx={summary.section_index}:attacks={summary.attack_count};"
            f"key={summary.inferred_key};load={summary.note_load}"
        )
    if section_bits:
        lines.append("section_summaries: " + "; ".join(section_bits))

    degree_bits = [
        f"d{bucket.degree}{'+' if bucket.alteration > 0 else ''}{bucket.alteration or ''}={bucket.count}"
        for bucket in report.scale_degrees.buckets[:8]
    ]
    if degree_bits:
        lines.append("scale_degrees: " + ", ".join(degree_bits))

    text = "\n".join(lines)
    truncated = False
    if len(text) > budget:
        truncated = True
        marker = "\n[analysis_context_truncated]"
        keep = max(0, budget - len(marker))
        text = text[:keep].rstrip() + marker

    logger.debug(
        "Built LLM analysis context projection",
        extra={
            "purpose": purpose,
            "char_count": len(text),
            "budget": budget,
            "truncated": truncated,
            "report_status": report.status,
            "scope_kind": report.resolved_scope.kind,
            "warning_count": len(report.warnings),
        },
    )
    if truncated:
        logger.warning(
            "LLM analysis context truncated",
            extra={"char_count": len(text), "budget": budget, "purpose": purpose},
        )
    return text


def _needs_final_roles(roles: RoleAnalysisResult, melody: MelodyAnalysisResult) -> bool:
    """Re-run roles only when initial roles were weak but melody selected tracks."""
    if not melody.profiles:
        return False
    return roles.inference.status in {"ambiguous", "insufficient_evidence"}


def _build_section_summaries(
    context: CompositionAnalysisContext,
    tonality: TonalityAnalysisResult,
    density: DensityAnalysisResult,
) -> list[SectionSummaryResult]:
    summaries: list[SectionSummaryResult] = []
    for index, section in enumerate(context.sections):
        start = section.start_tick
        end = section.start_tick + section.duration_ticks
        duration = max(1, end - start)
        attack_count = 0
        load_ticks = 0
        for track in context.tracks:
            for note in track.logical_notes:
                if attack_in_interval(note, start, end):
                    attack_count += 1
                load_ticks += clip_occupancy(note, start, end)
        inferred_key = _inferred_key_for_section(tonality, section.start_bar)
        # Prefer local section load; fall back to scoped density when single section.
        note_load = load_ticks / float(duration)
        if (
            len(context.sections) == 1
            and density.metrics.note_load is not None
            and context.resolved_scope.kind == "composition"
        ):
            note_load = density.metrics.note_load
        summaries.append(
            SectionSummaryResult(
                section_index=index,
                section_id=section.id,
                section_type=section.type,
                start_tick=start,
                end_tick=end,
                attack_count=attack_count,
                note_load=note_load,
                inferred_key=inferred_key,
            )
        )
        if len(summaries) >= ANALYSIS_MAX_SECTION_SUMMARIES:
            break
    return summaries


def _inferred_key_for_section(
    tonality: TonalityAnalysisResult,
    start_bar: int,
) -> str | None:
    for span in tonality.local_spans:
        if span.start_bar <= start_bar < span.end_bar_exclusive and span.key:
            return span.key
    if tonality.global_key and tonality.global_key.key:
        return tonality.global_key.key
    return tonality.effective_key


def _resolve_report_status(
    context: CompositionAnalysisContext,
    tonality: TonalityAnalysisResult,
    harmony: HarmonyAnalysisResult,
    melody: MelodyAnalysisResult,
    density: DensityAnalysisResult,
    roles: RoleAnalysisResult,
    repetition: RepetitionAnalysisResult,
    tension: TensionAnalysisResult,
    warnings: list[Any],
) -> AnalysisReportStatus:
    attacks = context.attacks_in_scope()
    pitched = context.notes_overlapping_scope(drums=False)
    if not attacks:
        return "empty"

    statuses = [
        tonality.inference.status,
        harmony.inference.status,
        melody.inference.status,
        density.inference.status,
        roles.inference.status,
        repetition.inference.status,
        tension.inference.status,
    ]
    if any(status == "truncated" for status in statuses):
        return "partial"
    if not pitched:
        # Percussion-only or silent pitched content: still a successful partial report.
        return "partial"
    abstentions = {
        "insufficient_evidence",
        "not_applicable",
        "ambiguous",
    }
    if all(status in abstentions for status in statuses):
        return "partial"
    if any(status in abstentions for status in statuses):
        return "partial"
    # Presence of technical warnings does not demote an otherwise ok report.
    _ = warnings
    return "ok"
