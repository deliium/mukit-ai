"""Pure(ish) layered arbitration: validate → critic annotate → rank → select.

No FastAPI. No SQLite. Never calls ``score_pending``. Critic is annotate-only
in ship-1 (subjective findings never hard-reject survivors).
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from typing import Any, Sequence

from app.composition_schemas import CompositionV2
from app.ensemble_arbitration_schemas import (
    EnsembleArbitrationV1,
    EnsembleCandidateProvenanceV1,
    EnsembleCandidateV1,
    EnsembleCriticSummaryV1,
    EnsemblePolicyV1,
    EnsembleRejectedAttemptV1,
    EnsembleSelectionMode,
    EnsembleStrategy,
)
from app.preference_schemas import PreferenceCandidateFeaturesV1, PreferenceRankerV1
from app.services.composition_critique import evaluate_composition
from app.services.composition_fingerprint import composition_source_fingerprint
from app.services.composition_validator import validate_composition_integrity
from app.services.generation_constraints import (
    GenerationConstraints,
    validate_generation_constraints,
)
from app.services.preference_features import project_preference_features
from app.services.preference_ranker import LinearPairwiseRanker

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EnsembleGenerateAttempt:
    """One model attempt before validation (composition may be invalid)."""

    model_id: str
    seed: int
    attempt_ordinal: int
    strategy: EnsembleStrategy
    composition: CompositionV2 | dict[str, Any] | None
    engine: str = "symbolic_composer"
    runtime: str | None = None
    model_version: str | None = None
    generate_error_code: str | None = None
    generate_error_message: str | None = None


def new_candidate_id() -> str:
    """Return an ``ens_<hex>`` id long enough for preference feature rows."""
    return f"ens_{secrets.token_hex(8)}"


def _validation_digest(
    *,
    integrity_ok: bool,
    constraint_ok: bool,
    codes: Sequence[str],
) -> str:
    payload = f"{integrity_ok}|{constraint_ok}|{','.join(codes)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _critic_digest(finding_codes: Sequence[str], recommendation: str | None) -> str:
    payload = f"{recommendation or ''}|{','.join(finding_codes)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _parse_composition(
    raw: CompositionV2 | dict[str, Any],
) -> CompositionV2 | None:
    if isinstance(raw, CompositionV2):
        return raw
    try:
        return CompositionV2.model_validate(raw)
    except Exception:  # noqa: BLE001 — validator stage owns the reject code
        return None


def partition_validate_attempts(
    attempts: Sequence[EnsembleGenerateAttempt],
    constraints: GenerationConstraints,
) -> tuple[list[EnsembleCandidateV1], list[EnsembleRejectedAttemptV1]]:
    """Hard-validate each attempt; rejected never enter critic/rank."""
    survivors: list[EnsembleCandidateV1] = []
    rejected: list[EnsembleRejectedAttemptV1] = []

    for attempt in attempts:
        provenance = EnsembleCandidateProvenanceV1(
            model_id=attempt.model_id,
            seed=attempt.seed,
            engine=attempt.engine,
            strategy=attempt.strategy,
            attempt_ordinal=attempt.attempt_ordinal,
            runtime=attempt.runtime,
            model_version=attempt.model_version,
        )
        if attempt.composition is None or attempt.generate_error_code:
            rejected.append(
                EnsembleRejectedAttemptV1(
                    model_id=attempt.model_id,
                    stage="generate",
                    code=attempt.generate_error_code or "ensemble_model_unready",
                    message=(attempt.generate_error_message or "generate failed")[:200],
                    provenance=provenance,
                )
            )
            continue

        music = _parse_composition(attempt.composition)
        if music is None:
            digest = _validation_digest(
                integrity_ok=False,
                constraint_ok=False,
                codes=["composition_parse_failed"],
            )
            rejected.append(
                EnsembleRejectedAttemptV1(
                    model_id=attempt.model_id,
                    stage="validator",
                    code="composition_parse_failed",
                    message="Candidate composition failed schema parse.",
                    provenance=provenance.model_copy(
                        update={"validation_digest": digest}
                    ),
                )
            )
            continue

        integrity = validate_composition_integrity(
            music, complexity="simple", profile="generation"
        )
        if not integrity.ok:
            codes = [item.code for item in integrity.errors] or ["integrity_failed"]
            digest = _validation_digest(
                integrity_ok=False, constraint_ok=False, codes=codes
            )
            rejected.append(
                EnsembleRejectedAttemptV1(
                    model_id=attempt.model_id,
                    stage="validator",
                    code=codes[0][:80],
                    message="Candidate failed composition integrity.",
                    provenance=provenance.model_copy(
                        update={"validation_digest": digest}
                    ),
                )
            )
            continue

        constraint_report = validate_generation_constraints(music, constraints)
        if not constraint_report.ok:
            codes = [item.code for item in constraint_report.errors] or [
                "constraint_failed"
            ]
            digest = _validation_digest(
                integrity_ok=True, constraint_ok=False, codes=codes
            )
            rejected.append(
                EnsembleRejectedAttemptV1(
                    model_id=attempt.model_id,
                    stage="validator",
                    code=codes[0][:80],
                    message="Candidate failed hard generation constraints.",
                    provenance=provenance.model_copy(
                        update={"validation_digest": digest}
                    ),
                )
            )
            continue

        digest = _validation_digest(
            integrity_ok=True, constraint_ok=True, codes=["ok"]
        )
        survivors.append(
            EnsembleCandidateV1(
                candidate_id=new_candidate_id(),
                composition=music,
                provenance=provenance.model_copy(
                    update={"validation_digest": digest}
                ),
                validation_ok=True,
                rank_index=len(survivors),
            )
        )

    logger.debug(
        "Ensemble validator partition complete",
        extra={
            "generated": len(attempts),
            "rejected": len(rejected),
            "survived": len(survivors),
        },
    )
    return survivors, rejected


def annotate_survivors_with_critic(
    survivors: Sequence[EnsembleCandidateV1],
    *,
    constraints: GenerationConstraints | None = None,
) -> list[EnsembleCandidateV1]:
    """Attach critic summaries; never drop a survivor (ship-1 annotate only)."""
    annotated: list[EnsembleCandidateV1] = []
    for candidate in survivors:
        try:
            result = evaluate_composition(
                candidate.composition, constraints=constraints
            )
            critique = result.critique
            findings = list(critique.findings or [])
            stratum_summary = {
                "hard_constraint": int(critique.stratum_counts.hard_constraint),
                "technical": int(critique.stratum_counts.technical),
                "stylistic": int(critique.stratum_counts.stylistic),
                "subjective": int(critique.stratum_counts.subjective),
            }
            codes = [f.code for f in findings[:16]]
            recommendation = (
                critique.recommendation.value if critique.recommendation else None
            )
            digest = _critic_digest(codes, recommendation)
            error_count = sum(1 for f in findings if f.severity == "error")
            warning_count = sum(1 for f in findings if f.severity == "warning")
            critic = EnsembleCriticSummaryV1(
                finding_count=len(findings),
                error_count=error_count,
                warning_count=warning_count,
                recommendation=recommendation,
                stratum_summary=stratum_summary,
                digest=digest,
            )
            annotated.append(
                candidate.model_copy(
                    update={
                        "critic": critic,
                        "provenance": candidate.provenance.model_copy(
                            update={"critic_digest": digest}
                        ),
                    }
                )
            )
        except Exception as exc:  # noqa: BLE001 — soft annotate failure
            logger.debug(
                "Ensemble critic annotate soft-failed",
                extra={
                    "candidate_id_prefix": candidate.candidate_id[:12],
                    "error_type": type(exc).__name__,
                },
            )
            annotated.append(
                candidate.model_copy(
                    update={
                        "critic": EnsembleCriticSummaryV1(
                            finding_count=0,
                            recommendation=None,
                            digest=None,
                        )
                    }
                )
            )
    return annotated


def rank_survivors(
    survivors: Sequence[EnsembleCandidateV1],
    *,
    ranking_enabled: bool,
    ranker_model: PreferenceRankerV1 | None,
) -> tuple[list[EnsembleCandidateV1], bool]:
    """Reorder survivors via in-process LinearPairwiseRanker when warm + enabled."""
    ordered = list(survivors)
    if not ordered:
        return [], False
    if not ranking_enabled or ranker_model is None or ranker_model.pair_count == 0:
        logger.debug(
            "Ensemble ranking skipped",
            extra={"ranking_applied": False, "survivor_count": len(ordered)},
        )
        return [
            item.model_copy(update={"rank_index": index, "preference_score": None})
            for index, item in enumerate(ordered)
        ], False

    feature_rows: list[PreferenceCandidateFeaturesV1] = []
    by_id: dict[str, EnsembleCandidateV1] = {}
    for index, candidate in enumerate(ordered):
        try:
            vector = project_preference_features(
                candidate.composition, candidate_id=candidate.candidate_id
            )
            fingerprint = composition_source_fingerprint(candidate.composition)
            feature_rows.append(
                PreferenceCandidateFeaturesV1(
                    candidate_id=candidate.candidate_id,
                    candidate_fingerprint=fingerprint,
                    original_index=min(index, 3),
                    feature_vector=vector,
                )
            )
            by_id[candidate.candidate_id] = candidate
        except Exception as exc:  # noqa: BLE001 — keep validator order on feature fail
            logger.debug(
                "Ensemble preference feature projection failed",
                extra={
                    "candidate_id_prefix": candidate.candidate_id[:12],
                    "error_type": type(exc).__name__,
                },
            )
            return [
                item.model_copy(update={"rank_index": i, "preference_score": None})
                for i, item in enumerate(ordered)
            ], False

    ranking = LinearPairwiseRanker().rank(feature_rows, ranker_model)
    score_by_id = {
        cid: ranking.scores[i]
        for i, cid in enumerate(ranking.ordered_candidate_ids)
    }
    reordered: list[EnsembleCandidateV1] = []
    for index, cid in enumerate(ranking.ordered_candidate_ids):
        base = by_id[cid]
        reordered.append(
            base.model_copy(
                update={
                    "rank_index": index,
                    "preference_score": float(score_by_id.get(cid, 0.0)),
                }
            )
        )
    logger.debug(
        "Ensemble ranking complete",
        extra={
            "ranking_applied": ranking.ranking_applied,
            "survivor_count": len(reordered),
        },
    )
    return reordered, bool(ranking.ranking_applied)


def apply_selection_mode(
    survivors: Sequence[EnsembleCandidateV1],
    *,
    selection_mode: EnsembleSelectionMode,
    top_n: int,
) -> tuple[list[EnsembleCandidateV1], str | None]:
    """Truncate for top_n and set suggested_candidate_id per selection mode."""
    ordered = list(survivors)
    if not ordered:
        return [], None

    if selection_mode == "top_n":
        limit = max(1, min(int(top_n), len(ordered)))
        ordered = ordered[:limit]

    suggested: str | None
    if selection_mode == "human":
        suggested = None
    else:
        # auto_suggest and top_n both surface the first after rank.
        suggested = ordered[0].candidate_id

    logger.info(
        "Ensemble selection applied",
        extra={
            "selection_mode": selection_mode,
            "suggested_id_prefix": (suggested or "")[:12] or None,
            "returned_count": len(ordered),
        },
    )
    return ordered, suggested


def build_arbitration_report(
    *,
    policy: EnsemblePolicyV1,
    attempts: Sequence[EnsembleGenerateAttempt],
    constraints: GenerationConstraints,
    ranking_enabled: bool = False,
    ranker_model: PreferenceRankerV1 | None = None,
    wall_ms: int = 0,
) -> EnsembleArbitrationV1:
    """Run the locked pipeline and stamp honesty booleans."""
    survivors, rejected = partition_validate_attempts(attempts, constraints)
    survivors = annotate_survivors_with_critic(survivors, constraints=constraints)
    survivors, ranking_applied = rank_survivors(
        survivors,
        ranking_enabled=ranking_enabled,
        ranker_model=ranker_model,
    )
    survivors, suggested = apply_selection_mode(
        survivors,
        selection_mode=policy.selection_mode,
        top_n=policy.top_n,
    )
    report = EnsembleArbitrationV1(
        policy=policy,
        candidates=survivors,
        rejected_attempts=rejected,
        suggested_candidate_id=suggested,
        ranking_applied=ranking_applied,
        musical_quality_claim=False,
        critic_is_subjective_layer=True,
        ranking_is_preference_not_quality=True,
        wall_ms=max(0, int(wall_ms)),
    )
    logger.debug(
        "Ensemble arbitration report built",
        extra={
            "survivor_count": len(survivors),
            "rejected_count": len(rejected),
            "ranking_applied": ranking_applied,
            "selection_mode": policy.selection_mode,
        },
    )
    return report
