"""Tests for secret-safe generation.provenance.v1 fragments."""

from __future__ import annotations

import pytest

from app.services.generation_provenance import (
    PROVENANCE_SCHEMA,
    PersistenceSecretError,
    attach_provenance_fragment,
    build_generation_provenance_v1,
    checkpoint_basename_prefix,
    compact_generation_config,
    infer_model_version,
)


def test_checkpoint_basename_prefix_strips_absolute_paths():
    assert checkpoint_basename_prefix("/home/user/models/ckpt/card.json") == "card.json"
    assert checkpoint_basename_prefix("C:\\Models\\ckpt\\weights.bin") == "weights.bin"
    assert checkpoint_basename_prefix("../escape/weights.pt") == "weights.pt"
    long_name = "a" * 80
    assert len(checkpoint_basename_prefix(long_name) or "") == 32


def test_infer_model_version_for_fake_runtimes():
    assert infer_model_version(model_id="fake:fake-v1", runtime="fake") == "fake-v1"
    assert (
        infer_model_version(model_id="fake:symbolic-tiny", runtime="fake_symbolic")
        == "fake-symbolic-tiny"
    )
    assert infer_model_version(model_id="openai:gpt-4o", runtime="openai_compatible_chat") is None


def test_build_generation_provenance_v1_includes_schema_and_sanitized_stages():
    fragment = build_generation_provenance_v1(
        pipeline_id="hybrid_plan_symbolic",
        seed=42,
        stages=[
            {
                "operation": "generate_planner",
                "model_id": "fake:fake-v1",
                "runtime": "fake",
                "capability": "language_planner",
            },
            {
                "operation": "generate_composer",
                "model_id": "fake:symbolic-tiny",
                "runtime": "fake_symbolic",
                "capability": "symbolic_composer",
                "seed": 42,
                "checkpoint_card_prefix": "/var/models/tiny/card.json",
                "tokenizer_version": "tokenizer.v1",
            },
        ],
        generation_config={"temperature": 0.4, "sample_greedy": True, "prompt": "SECRET"},
    )
    assert fragment["provenance_schema"] == PROVENANCE_SCHEMA
    assert fragment["pipeline_id"] == "hybrid_plan_symbolic"
    assert fragment["seed"] == 42
    assert fragment["generation_config"] == {"temperature": 0.4, "sample_greedy": True}
    assert "prompt" not in fragment["generation_config"]
    composer = fragment["stages"][1]
    assert composer["model_version"] == "fake-symbolic-tiny"
    assert composer["checkpoint_card_prefix"] == "card.json"
    assert composer["tokenizer_version"] == "tokenizer.v1"


def test_build_generation_provenance_rejects_secret_values():
    with pytest.raises(PersistenceSecretError):
        build_generation_provenance_v1(
            pipeline_id="llm_only",
            stages=[
                {
                    "operation": "generate",
                    "model_id": "sk-proj-abcdefghijklmnopqrstuvwxyz1234",
                    "runtime": "openai_compatible_chat",
                }
            ],
        )


def test_attach_provenance_fragment_mirrors_sanitized_stages():
    enriched = attach_provenance_fragment(
        {
            "pipeline_id": "llm_only",
            "stages": [
                {
                    "operation": "generate",
                    "model_id": "fake:fake-v1",
                    "runtime": "fake",
                    "capability": "language_planner",
                }
            ],
            "seed": None,
        },
        generation_config={"timeout_seconds": 30},
    )
    assert enriched["generation_parameters"]["provenance_schema"] == PROVENANCE_SCHEMA
    assert enriched["stages"][0]["model_version"] == "fake-v1"
    assert enriched["generation_parameters"]["generation_config"] == {"timeout_seconds": 30}


def test_compact_generation_config_drops_non_scalars():
    assert compact_generation_config(
        {
            "temperature": 0.2,
            "candidate_count": 2,
            "nested": {"a": 1},
            "prompt": "nope",
        }
    ) == {"temperature": 0.2, "candidate_count": 2}
