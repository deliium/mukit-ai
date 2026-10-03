"""Pure arbitration pipeline: validate → critic → rank → select."""

from __future__ import annotations

import json
from pathlib import Path

from app.composition_plan_schemas import parse_composition_plan
from app.ensemble_arbitration_schemas import (
    FAKE_ENSEMBLE_MODEL_IDS,
    EnsembleHardConstraintsV1,
    EnsemblePolicyV1,
)
from app.preference_schemas import PREFERENCE_FEATURE_DIMS, PreferenceRankerV1
from app.services.ensemble_arbitration_pipeline import (
    EnsembleGenerateAttempt,
    build_arbitration_report,
)
from app.services.fake_symbolic_composer import generate_fake_symbolic_composition
from app.services.preference_ranker import utc_now_iso

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "composition_plan"


def _plan():
    return parse_composition_plan(
        json.loads((FIXTURES / "valid_minimal.json").read_text(encoding="utf-8"))
    )


def _constraints():
    return EnsembleHardConstraintsV1.model_validate(
        {
            "key": "C major",
            "key_user_specified": True,
            "time_signature": "4/4",
            "duration_bars": 8,
            "tempo_min": 100,
            "tempo_max": 140,
            "sections": [
                {"type": "intro", "start_bar": 1, "bar_count": 2},
                {"type": "verse", "start_bar": 3, "bar_count": 4},
                {"type": "outro", "start_bar": 7, "bar_count": 2},
            ],
            "sections_user_specified": True,
            "required_instrument_families": ["piano", "bass"],
            "requested_instruments": ["piano", "bass"],
            "allow_extra_instrument_families": True,
            "complexity": "simple",
        }
    ).to_generation_constraints()


def _policy(**overrides) -> EnsemblePolicyV1:
    payload = {
        "schema_version": "ensemble.policy.v1",
        "model_ids": list(FAKE_ENSEMBLE_MODEL_IDS),
        "strategy": "parallel_once",
        "selection_mode": "human",
        "top_n": 2,
        "base_seed": 0,
        "execution": "sequential",
    }
    payload.update(overrides)
    return EnsemblePolicyV1.model_validate(payload)


def _attempts_with_invalid():
    plan = _plan()
    attempts: list[EnsembleGenerateAttempt] = []
    for ordinal, model_id in enumerate(FAKE_ENSEMBLE_MODEL_IDS):
        music, report = generate_fake_symbolic_composition(
            plan, seed=ordinal, model_id=model_id
        )
        attempts.append(
            EnsembleGenerateAttempt(
                model_id=model_id,
                seed=ordinal,
                attempt_ordinal=ordinal,
                strategy="parallel_once",
                composition=music,
                runtime=report.get("runtime"),
                model_version=report.get("model_version"),
            )
        )
    # Inject a deliberately invalid composition for a fourth logical attempt
    # by replacing the sparse attempt body with a broken dict.
    tiny, sparse, dense = FAKE_ENSEMBLE_MODEL_IDS
    attempts[1] = EnsembleGenerateAttempt(
        model_id=sparse,
        seed=1,
        attempt_ordinal=1,
        strategy="parallel_once",
        composition={"schema_version": "composition.v2", "tracks": "broken"},
    )
    return attempts, tiny, sparse, dense


def _warm_dense_ranker() -> PreferenceRankerV1:
    # Density is preference feature index 3 — prefer denser material.
    weights = [0.0] * PREFERENCE_FEATURE_DIMS
    weights[3] = 4.0
    return PreferenceRankerV1(
        weights=weights,
        pair_count=4,
        updated_at=utc_now_iso(),
    )


def test_invalid_never_ranked_and_honesty_stamps():
    attempts, _tiny, sparse, dense = _attempts_with_invalid()
    report = build_arbitration_report(
        policy=_policy(selection_mode="human"),
        attempts=attempts,
        constraints=_constraints(),
        ranking_enabled=True,
        ranker_model=_warm_dense_ranker(),
    )
    assert report.musical_quality_claim is False
    assert report.critic_is_subjective_layer is True
    assert report.ranking_is_preference_not_quality is True

    rejected_models = {item.model_id for item in report.rejected_attempts}
    assert sparse in rejected_models
    assert all(item.stage == "validator" for item in report.rejected_attempts)

    survivor_models = [c.provenance.model_id for c in report.candidates]
    assert sparse not in survivor_models
    assert dense in survivor_models
    # Warm ranker preferring density → dense first among survivors.
    assert report.candidates[0].provenance.model_id == dense
    assert report.ranking_applied is True
    # Critic annotate present; does not drop survivors.
    for candidate in report.candidates:
        assert candidate.critic is not None
        assert candidate.validation_ok is True


def test_human_returns_all_survivors_top_n_truncates():
    plan = _plan()
    attempts = []
    for ordinal, model_id in enumerate(FAKE_ENSEMBLE_MODEL_IDS):
        music, _report = generate_fake_symbolic_composition(
            plan, seed=ordinal, model_id=model_id
        )
        attempts.append(
            EnsembleGenerateAttempt(
                model_id=model_id,
                seed=ordinal,
                attempt_ordinal=ordinal,
                strategy="parallel_once",
                composition=music,
            )
        )
    human = build_arbitration_report(
        policy=_policy(selection_mode="human"),
        attempts=attempts,
        constraints=_constraints(),
    )
    assert len(human.candidates) == 3
    assert human.suggested_candidate_id is None

    top_n = build_arbitration_report(
        policy=_policy(selection_mode="top_n", top_n=2),
        attempts=attempts,
        constraints=_constraints(),
    )
    assert len(top_n.candidates) == 2
    assert top_n.suggested_candidate_id == top_n.candidates[0].candidate_id
