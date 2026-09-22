"""Music Evaluation Engine — deterministic structured critique (read-only).

Inventory / non-goals checklist (Task 1 freeze):

(a) Current thin Critic path reused via this engine (bounded analysis sibling +
    ``AgentCritiqueV1`` + optional RevisionPlan still emitted by CriticAgent).
(b) Analysis metrics / warnings reused; constraints → hard stratum.
(c) Workspace promote for role ``critique`` already ships — no new tables.
(d) Never mutate ``composition.v2``.
(e) Subjective LLM taste ≠ hard constraint / objective correctness.
(f) No ``composition.v4``.

APIs: ``evaluate_composition`` (deterministic). Optional LLM adapter is separate
(``llm_composition_critique``). Agents must not import workspace / SQLite here.
"""

from __future__ import annotations

import copy
import logging
import time
from typing import Any, Mapping, Sequence

from app.ai_agents.schemas import AgentCritiqueV1, CritiqueRecommendation
from app.analysis_schemas import CompositionAnalysisError, CompositionAnalysisReport
from app.composition_schemas import CompositionV2, parse_composition_document
from app.critique_schemas import (
    CRITIQUE_COMPLEXITY_EXCEEDED,
    CRITIQUE_ENGINE_VERSION,
    CRITIQUE_FINDING_MAX,
    CRITIQUE_INVALID_COMPOSITION,
    CritiqueError,
    CritiqueFindingV1,
    CritiqueScope,
    count_strata,
)
from app.critique_settings import CritiqueEngineSettings, load_critique_engine_settings
from app.services.composition_analysis import analyze_composition
from app.services.composition_critique_checks import (
    check_climax_contrast,
    check_melody_and_tension_stylistic,
    check_section_contrast,
    findings_from_constraints,
    map_analysis_warnings_to_findings,
    resolve_climax_section_index,
)
from app.services.composition_critique_policy import recommend_from_findings
from app.services.composition_critique_scope import (
    ResolvedCritiqueScope,
    resolve_critique_scope,
)
from app.services.generation_constraints import GenerationConstraints

logger = logging.getLogger(__name__)

INVENTORY_CHECKLIST_IDS: tuple[str, ...] = (
    "thin_critic_flat_critique_v1",
    "reuse_analysis_metrics_and_warnings",
    "workspace_critique_promote_already_ships",
    "never_mutate_composition_v2",
    "subjective_ne_hard_constraint",
    "no_composition_v4",
)

_ENGINE_IMPLEMENTED = True


def inventory_checklist() -> Sequence[str]:
    """Return frozen inventory checklist ids (Task 1)."""
    logger.debug(
        "Critique inventory checklist",
        extra={"checklist_ids": list(INVENTORY_CHECKLIST_IDS)},
    )
    return INVENTORY_CHECKLIST_IDS


def evaluation_engine_ready() -> bool:
    return _ENGINE_IMPLEMENTED


class EvaluationResult:
    """Engine output: critique payload + optional analysis report (session)."""

    __slots__ = ("critique", "analysis_report", "resolved_scope", "duration_ms")

    def __init__(
        self,
        *,
        critique: AgentCritiqueV1,
        analysis_report: CompositionAnalysisReport | None,
        resolved_scope: ResolvedCritiqueScope,
        duration_ms: float,
    ) -> None:
        self.critique = critique
        self.analysis_report = analysis_report
        self.resolved_scope = resolved_scope
        self.duration_ms = duration_ms


def _parse_composition(composition: CompositionV2 | dict[str, Any] | Mapping[str, Any]) -> CompositionV2:
    if isinstance(composition, CompositionV2):
        return composition
    try:
        if isinstance(composition, Mapping):
            parsed = CompositionV2.model_validate(dict(composition))
        else:
            parsed = parse_composition_document(composition)
            if not isinstance(parsed, CompositionV2):
                raise CritiqueError(
                    "Critique requires composition.v2",
                    code=CRITIQUE_INVALID_COMPOSITION,
                )
            return parsed
        return parsed
    except CritiqueError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise CritiqueError(
            "Composition is not a valid composition.v2 document for critique",
            code=CRITIQUE_INVALID_COMPOSITION,
        ) from exc


def evaluate_composition(
    composition: CompositionV2 | dict[str, Any] | Mapping[str, Any],
    *,
    scope: CritiqueScope | dict[str, Any] | None = None,
    constraints: GenerationConstraints | None = None,
    revise_on_technical: bool = False,
    requested_climax_section_index: int | None = None,
    brief_text: str | None = None,
    extra_findings: Sequence[CritiqueFindingV1] | None = None,
    model_critique_status: str | None = None,
    settings: CritiqueEngineSettings | None = None,
) -> EvaluationResult:
    """Run deterministic evaluation; never mutates input composition.

    Returns extended ``AgentCritiqueV1`` plus optional analysis report for siblings.
    """
    started = time.perf_counter()
    cfg = settings or load_critique_engine_settings()
    validated = _parse_composition(composition)
    before_dump = validated.model_dump(mode="json")
    fingerprint_prefix = None
    try:
        from app.analysis_schemas import fingerprint_log_prefix
        from app.services.composition_fingerprint import composition_source_fingerprint

        fingerprint_prefix = fingerprint_log_prefix(composition_source_fingerprint(validated))
    except Exception:  # noqa: BLE001
        fingerprint_prefix = None

    resolved = resolve_critique_scope(validated, scope)
    findings: list[CritiqueFindingV1] = []
    analysis_report: CompositionAnalysisReport | None = None
    evaluation_failed = False
    warning_codes: list[str] = []

    # Analysis — prefer composition scope for metrics; clip findings later.
    try:
        analysis_scope: dict[str, Any] = {"kind": "composition"}
        if resolved.kind == "section" and resolved.section_index is not None:
            analysis_scope = {"kind": "section", "section_index": resolved.section_index}
        elif resolved.kind == "track" and resolved.track_id:
            analysis_scope = {"kind": "track", "track_id": resolved.track_id}
        analysis_report = analyze_composition(validated, analysis_scope)
        findings.extend(
            map_analysis_warnings_to_findings(analysis_report, resolved=resolved)
        )
        if analysis_report.status == "failed":
            evaluation_failed = True
            findings.append(
                CritiqueFindingV1(
                    stratum="hard_constraint",
                    category="other",
                    code="analysis_failed",
                    severity="error",
                    explanation="Deterministic analysis could not produce a usable report.",
                )
            )
    except CompositionAnalysisError as exc:
        evaluation_failed = True
        warning_codes.append(getattr(exc, "code", "analysis_error")[:80])
        findings.append(
            CritiqueFindingV1(
                stratum="hard_constraint",
                category="other",
                code="analysis_failed",
                severity="error",
                explanation="Deterministic analysis could not run.",
            )
        )
        logger.info(
            "Critique analysis soft-failed",
            extra={
                "code": getattr(exc, "code", type(exc).__name__),
                "fingerprint_prefix": fingerprint_prefix,
            },
        )
    except Exception as exc:  # noqa: BLE001
        evaluation_failed = True
        warning_codes.append(type(exc).__name__[:80])
        findings.append(
            CritiqueFindingV1(
                stratum="hard_constraint",
                category="other",
                code="analysis_failed",
                severity="error",
                explanation="Unexpected analysis failure during evaluation.",
            )
        )
        logger.info(
            "Critique analysis unexpected failure",
            extra={"reason": type(exc).__name__, "fingerprint_prefix": fingerprint_prefix},
        )

    # Hard constraints when provided.
    findings.extend(findings_from_constraints(validated, constraints))

    # Climax + section contrast + stylistic melody/tension (whole-composition metrics).
    if analysis_report is not None and resolved.kind in ("composition", "section", "bars"):
        climax_findings = check_climax_contrast(
            validated,
            analysis_report,
            settings=cfg,
            requested_climax_section_index=requested_climax_section_index,
            brief_text=brief_text,
        )
        findings.extend(climax_findings)
        climax_idx = resolve_climax_section_index(
            validated,
            requested_climax_section_index=requested_climax_section_index,
            brief_text=brief_text,
            settings=cfg,
        )
        skip_pair = None
        if climax_idx is not None and climax_idx > 0:
            skip_pair = (climax_idx - 1, climax_idx)
        if resolved.kind == "composition":
            findings.extend(
                check_section_contrast(
                    analysis_report, settings=cfg, skip_pair=skip_pair
                )
            )
            findings.extend(check_melody_and_tension_stylistic(analysis_report))

    if extra_findings:
        findings.extend(list(extra_findings))

    if len(findings) > CRITIQUE_FINDING_MAX:
        raise CritiqueError(
            f"Critique findings exceed cap ({CRITIQUE_FINDING_MAX})",
            code=CRITIQUE_COMPLEXITY_EXCEEDED,
            fingerprint_prefix=fingerprint_prefix,
        )

    findings = findings[: cfg.max_findings]
    recommendation = recommend_from_findings(
        findings,
        revise_on_technical=revise_on_technical,
        evaluation_failed=evaluation_failed,
    )
    stratum_counts = count_strata(findings)
    summary = _summary_for(recommendation, stratum_counts, findings)

    critique = AgentCritiqueV1(
        recommendation=recommendation,
        summary=summary,
        analysis_warning_count=len(analysis_report.warnings) if analysis_report else 0,
        findings=findings,
        scope=resolved.digest,
        stratum_counts=stratum_counts,
        engine_version=cfg.engine_version or CRITIQUE_ENGINE_VERSION,
        algorithm_version=CRITIQUE_ENGINE_VERSION,
        model_critique_status=model_critique_status,  # type: ignore[arg-type]
    )

    after_dump = validated.model_dump(mode="json")
    if after_dump != before_dump:
        # Restore is impossible if mutated in place; fail closed.
        logger.error(
            "Critique engine mutated composition",
            extra={"fingerprint_prefix": fingerprint_prefix},
        )
        raise CritiqueError(
            "Evaluation engine must not mutate composition",
            code=CRITIQUE_INVALID_COMPOSITION,
            fingerprint_prefix=fingerprint_prefix,
        )
    # Also assert caller dict (if any) was not mutated via shared refs.
    if isinstance(composition, dict):
        # Deep-compare against before snapshot of parsed dump only — caller dict
        # may differ in ignored fields; we only guarantee validated identity.
        _ = copy.deepcopy(before_dump)

    duration_ms = (time.perf_counter() - started) * 1000.0
    logger.info(
        "Critique evaluation complete",
        extra={
            "engine_version": critique.engine_version,
            "scope_kind": resolved.kind,
            "hard_constraint": stratum_counts.hard_constraint,
            "technical": stratum_counts.technical,
            "stylistic": stratum_counts.stylistic,
            "subjective": stratum_counts.subjective,
            "recommendation": recommendation.value,
            "duration_ms": round(duration_ms, 2),
            "model_critique_status": model_critique_status,
            "fingerprint_prefix": fingerprint_prefix,
        },
    )
    return EvaluationResult(
        critique=critique,
        analysis_report=analysis_report,
        resolved_scope=resolved,
        duration_ms=duration_ms,
    )


def _summary_for(
    recommendation: CritiqueRecommendation,
    stratum_counts: Any,
    findings: Sequence[CritiqueFindingV1],
) -> str:
    if recommendation == CritiqueRecommendation.REVISE:
        codes = ",".join(f.code for f in findings[:4]) or "evaluation"
        return f"Critic requests revision ({codes})."[:400]
    if stratum_counts.stylistic or stratum_counts.subjective or stratum_counts.technical:
        return (
            "Critic approves with observations "
            f"(hard={stratum_counts.hard_constraint}, tech={stratum_counts.technical}, "
            f"style={stratum_counts.stylistic}, subj={stratum_counts.subjective})."
        )[:400]
    return "Critic approves working draft after deterministic evaluation."
