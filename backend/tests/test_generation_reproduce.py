"""Seeded hybrid reproduce: same provenance pipeline/seed → equal composition fingerprint.

Uses fake:symbolic-tiny only — no remote LLM, no GPU, no checkpoint download.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging

from app.llm_settings import FAKE_PROVIDER, LLMProviderSettings, LLMSettings
from app.schemas import (
    LLMGenerationOptions,
    LLMModelSelection,
    LLMMusicGenerationRequest,
    LLMPromptParameters,
)
from app.services.composition_snapshot_encoding import composition_snapshot_fingerprint
from app.services.generation_provenance import (
    generation_options_overrides_from_provenance,
    log_reproduce_attempt,
    stage_model_ids_from_provenance,
)
from app.services.llm_music_generator import generate_music_json

logger = logging.getLogger(__name__)

_SEED = 42
_PIPELINE = "hybrid_plan_symbolic"


def _settings() -> LLMSettings:
    return LLMSettings(
        providers=(
            LLMProviderSettings(
                provider=FAKE_PROVIDER,
                model="fake-v1",
                api_key="x",
                base_url=None,
                is_default=True,
            ),
        ),
        default_provider=FAKE_PROVIDER,
        request_timeout_seconds=60,
        temperature=0.2,
    )


def _prompt() -> LLMPromptParameters:
    return LLMPromptParameters(
        mood="calm",
        genre="classical",
        key="C major",
        time_signature="4/4",
        tempo_min=100,
        tempo_max=140,
        duration_bars=8,
        instruments=["piano", "bass"],
        complexity="simple",
    )


def _request(*, pipeline: str, seed: int | None) -> LLMMusicGenerationRequest:
    return LLMMusicGenerationRequest(
        prompt=_prompt(),
        selection=LLMModelSelection(provider=FAKE_PROVIDER, model="fake-v1"),
        options=LLMGenerationOptions(pipeline=pipeline, seed=seed, max_retries=0),  # type: ignore[arg-type]
    )


def _payload_fingerprint(music) -> str:
    encoded = json.dumps(music.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def test_generation_options_overrides_from_provenance():
    overrides = generation_options_overrides_from_provenance(
        {
            "generation_parameters": {
                "provenance_schema": "generation.provenance.v1",
                "pipeline_id": _PIPELINE,
                "seed": _SEED,
                "stages": [{"model_id": "fake:symbolic-tiny"}],
            }
        }
    )
    assert overrides == {"pipeline": _PIPELINE, "seed": _SEED}
    assert generation_options_overrides_from_provenance(None) == {}
    assert generation_options_overrides_from_provenance({"seed": 1}) == {}


def test_hybrid_seeded_reproduce_fingerprint_match(caplog):
    """Re-drive generate from durable provenance → same snapshot fingerprint under fake mode."""
    settings = _settings()
    music_a, _warnings_a, _provider_a, validation_a, provenance_a = asyncio.run(
        generate_music_json(_request(pipeline=_PIPELINE, seed=_SEED), settings)
    )
    assert validation_a is None or validation_a.ok
    assert provenance_a.get("pipeline_id") == _PIPELINE
    assert provenance_a.get("seed") == _SEED
    gen_params = provenance_a.get("generation_parameters") or provenance_a
    assert gen_params.get("provenance_schema") == "generation.provenance.v1"

    overrides = generation_options_overrides_from_provenance(provenance_a)
    assert overrides["pipeline"] == _PIPELINE
    assert overrides["seed"] == _SEED
    model_ids = stage_model_ids_from_provenance(provenance_a)
    assert "fake:symbolic-tiny" in model_ids

    music_b, _warnings_b, _provider_b, validation_b, provenance_b = asyncio.run(
        generate_music_json(
            _request(pipeline=overrides["pipeline"], seed=overrides["seed"]),
            settings,
        )
    )
    assert validation_b is None or validation_b.ok

    fp_a = composition_snapshot_fingerprint(music_a)
    fp_b = composition_snapshot_fingerprint(music_b)
    payload_a = _payload_fingerprint(music_a)
    payload_b = _payload_fingerprint(music_b)
    match = fp_a == fp_b and payload_a == payload_b

    with caplog.at_level(logging.INFO, logger="app.services.generation_provenance"):
        log_reproduce_attempt(
            revision_id="rev-test-seeded",
            seed=overrides["seed"],
            model_ids=model_ids,
            fingerprint_match=match,
            fingerprint_prefix=fp_a,
        )

    assert match, (
        f"seeded reproduce mismatch: a={fp_a[:16]} b={fp_b[:16]} "
        f"seed={_SEED} models={model_ids}"
    )
    assert provenance_b.get("seed") == _SEED
    assert "fake:symbolic-tiny" in stage_model_ids_from_provenance(provenance_b)

    # Different seed must diverge (guards against accidental constant output).
    music_other, *_rest = asyncio.run(
        generate_music_json(_request(pipeline=_PIPELINE, seed=_SEED + 1), settings)
    )
    assert composition_snapshot_fingerprint(music_other) != fp_a

    logger.info(
        "Hybrid seeded reproduce gate passed",
        extra={
            "pipeline_id": _PIPELINE,
            "seed": _SEED,
            "model_ids": model_ids,
            "fingerprint_prefix": fp_a[:16],
        },
    )
