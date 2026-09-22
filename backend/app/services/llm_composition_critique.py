"""Optional LLM / model critic adapter — structured subjective findings only.

Never logs prompts or completions at INFO. Findings lacking concrete locus are dropped.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Mapping, Sequence

from app.analysis_schemas import CompositionAnalysisReport
from app.critique_schemas import (
    CritiqueAffectedRange,
    CritiqueFindingEvidence,
    CritiqueFindingV1,
)
from app.services.composition_analysis import build_llm_analysis_context
from app.services.composition_critique_scope import ResolvedCritiqueScope

logger = logging.getLogger(__name__)

ModelCritiqueStatus = str  # skipped | ok | failed | unavailable


def _has_concrete_locus(finding: CritiqueFindingV1) -> bool:
    if finding.affected_tracks:
        return True
    if finding.affected_range is not None:
        ar = finding.affected_range
        if ar.start_bar is not None or ar.start_tick is not None:
            return True
    refs = finding.evidence.refs if finding.evidence else []
    return any(str(r).strip() for r in refs)


def fake_model_critique_findings(
    *,
    resolved: ResolvedCritiqueScope,
) -> list[CritiqueFindingV1]:
    """Deterministic CI subjective finding with concrete bars."""
    start = resolved.start_bar
    end = resolved.end_bar
    return [
        CritiqueFindingV1(
            stratum="subjective",
            category="other",
            code="model_subjective_observation",
            severity="info",
            explanation=(
                f"Fake model notes limited expressive contrast around bars {start}–{end}."
            ),
            suggested_action=f"Consider shaping dynamics across bars {start}–{end}.",
            affected_range=CritiqueAffectedRange(start_bar=start, end_bar=end),
            evidence=CritiqueFindingEvidence(
                refs=[f"bars:{start}-{end}", "fake_model"],
                metrics={"fake": True},
            ),
        )
    ]


def run_model_critique(
    *,
    analysis_report: CompositionAnalysisReport | None,
    resolved: ResolvedCritiqueScope,
    brief_excerpt: str | None = None,
    include_model_critique: bool = False,
    env: Mapping[str, str] | None = None,
    deterministic_findings: Sequence[CritiqueFindingV1] | None = None,
) -> tuple[list[CritiqueFindingV1], ModelCritiqueStatus]:
    """Optional model critic. Returns (subjective findings, status)."""
    source = env if env is not None else os.environ
    if not include_model_critique:
        logger.info(
            "Model critique skipped",
            extra={"model_critique_status": "skipped"},
        )
        return [], "skipped"

    fake_mode = str(source.get("LLM_FAKE_MODE", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if fake_mode:
        findings = fake_model_critique_findings(resolved=resolved)
        logger.info(
            "Model critique fake ok",
            extra={
                "model_critique_status": "ok",
                "subjective_count": len(findings),
                "model_id_prefix": "fake",
            },
        )
        return findings, "ok"

    # Real path: bounded context only; without a wired chat client mark unavailable.
    # Keep stub for future ai_runtime creative_chat — do not fail deterministic engine.
    _ = brief_excerpt
    if analysis_report is not None:
        try:
            _ctx = build_llm_analysis_context(analysis_report, purpose="generation_repair")
            logger.debug(
                "Model critique context built",
                extra={"context_chars": len(_ctx) if isinstance(_ctx, str) else 0},
            )
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "Model critique context failed",
                extra={"model_critique_status": "failed", "reason": type(exc).__name__},
            )
            return [], "failed"

    # No production LLM projection wired yet → unavailable (deterministic still ok).
    logger.info(
        "Model critique unavailable",
        extra={"model_critique_status": "unavailable"},
    )
    return [], "unavailable"


def filter_subjective_findings(raw: Sequence[Any]) -> tuple[list[CritiqueFindingV1], int]:
    """Parse/validate candidate findings; drop those without concrete locus."""
    accepted: list[CritiqueFindingV1] = []
    rejected = 0
    for item in raw:
        try:
            if isinstance(item, CritiqueFindingV1):
                finding = item
            else:
                data = dict(item)
                data["stratum"] = "subjective"
                if data.get("severity") == "error":
                    data["severity"] = "warning"
                finding = CritiqueFindingV1.model_validate(data)
            if finding.stratum != "subjective":
                finding = finding.model_copy(update={"stratum": "subjective"})
            if finding.severity == "error":
                finding = finding.model_copy(update={"severity": "warning"})
            if not _has_concrete_locus(finding):
                rejected += 1
                logger.debug(
                    "Model finding rejected",
                    extra={"reason": "missing_locus", "code": finding.code},
                )
                continue
            accepted.append(finding)
        except Exception as exc:  # noqa: BLE001
            rejected += 1
            logger.debug(
                "Model finding rejected",
                extra={"reason": type(exc).__name__},
            )
    return accepted, rejected
