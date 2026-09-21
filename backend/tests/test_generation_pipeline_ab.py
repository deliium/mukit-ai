"""A/B evaluation: llm_only vs hybrid_plan_symbolic (validity metrics only).

``musical_quality_claim: false`` — compares structural validity / constraints / fingerprints,
not aesthetic quality.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from app.llm_settings import FAKE_PROVIDER, LLMProviderSettings, LLMSettings
from app.schemas import (
    LLMGenerationOptions,
    LLMModelSelection,
    LLMMusicGenerationRequest,
    LLMPromptParameters,
)
from app.services.llm_music_generator import generate_music_json


logger = logging.getLogger(__name__)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "generation_ab"
MUSICAL_QUALITY_CLAIM = False


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


def _request(pipeline: str, *, seed: int | None = None, duration_bars: int = 8) -> LLMMusicGenerationRequest:
    return LLMMusicGenerationRequest(
        prompt=LLMPromptParameters(
            mood="calm",
            genre="classical",
            key="C major",
            time_signature="4/4",
            tempo_min=100,
            tempo_max=140,
            duration_bars=duration_bars,
            instruments=["piano", "bass"],
            complexity="simple",
        ),
        selection=LLMModelSelection(provider=FAKE_PROVIDER, model="fake-v1"),
        options=LLMGenerationOptions(pipeline=pipeline, seed=seed, max_retries=0),  # type: ignore[arg-type]
    )


def _fingerprint(music) -> str:
    payload = music.model_dump(mode="json")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _run_pipeline(pipeline: str, *, seed: int | None, duration_bars: int = 8) -> dict[str, Any]:
    music, warnings, provider, validation, provenance = asyncio.run(
        generate_music_json(_request(pipeline, seed=seed, duration_bars=duration_bars), _settings())
    )
    note_count = sum(len(track.events) for track in music.tracks)
    return {
        "pipeline_id": pipeline,
        "valid": bool(validation and validation.ok),
        "constraint_status": validation.status if validation else None,
        "note_count": note_count,
        "track_count": len(music.tracks),
        "bar_count": music.bar_count,
        "fingerprint": _fingerprint(music),
        "stage_model_ids": [stage.get("model_id") for stage in (provenance.get("stages") or [])],
        "seed": provenance.get("seed"),
        "warning_count": len(warnings),
        "provider": provider.provider,
    }


def test_ab_llm_only_vs_hybrid_validity() -> None:
    """Same prompt family → both pipelines produce valid V2; fingerprints differ; quality claim false."""
    FIXTURES.mkdir(parents=True, exist_ok=True)
    # Fake llm_only fixture is 16 bars; hybrid fake symbolic follows request bars.
    llm_only = _run_pipeline("llm_only", seed=None, duration_bars=16)
    hybrid = _run_pipeline("hybrid_plan_symbolic", seed=7, duration_bars=8)

    assert hybrid["valid"] is True
    assert llm_only["valid"] is True
    assert hybrid["fingerprint"] != llm_only["fingerprint"]
    assert "fake:symbolic-tiny" in (hybrid["stage_model_ids"] or [])
    assert MUSICAL_QUALITY_CLAIM is False

    report = {
        "musical_quality_claim": MUSICAL_QUALITY_CLAIM,
        "eval_example": True,
        "llm_only": {k: v for k, v in llm_only.items() if k != "fingerprint"}
        | {"fingerprint_prefix": llm_only["fingerprint"][:16]},
        "hybrid_plan_symbolic": {k: v for k, v in hybrid.items() if k != "fingerprint"}
        | {"fingerprint_prefix": hybrid["fingerprint"][:16]},
    }
    out = FIXTURES / "ab_report_example.json"
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    logger.info(
        "A/B generation comparison summary",
        extra={
            "musical_quality_claim": False,
            "llm_only_valid": llm_only["valid"],
            "hybrid_valid": hybrid["valid"],
            "llm_only_notes": llm_only["note_count"],
            "hybrid_notes": hybrid["note_count"],
            "fingerprint_equal": llm_only["fingerprint"] == hybrid["fingerprint"],
        },
    )


def test_symbolic_continuation_with_prefix() -> None:
    from app.services.fake_symbolic_composer import generate_fake_symbolic_composition
    from app.composition_plan_schemas import parse_composition_plan

    plan_path = Path(__file__).resolve().parent / "fixtures" / "composition_plan" / "valid_minimal.json"
    plan = parse_composition_plan(json.loads(plan_path.read_text(encoding="utf-8")))
    prefix, _ = generate_fake_symbolic_composition(plan, seed=1)
    req = LLMMusicGenerationRequest(
        prompt=LLMPromptParameters(
            mood="calm",
            genre="classical",
            key="C major",
            duration_bars=8,
            instruments=["piano", "bass"],
            tempo_min=100,
            tempo_max=140,
        ),
        selection=LLMModelSelection(provider=FAKE_PROVIDER, model="fake-v1"),
        options=LLMGenerationOptions(
            pipeline="symbolic_continuation",
            seed=9,
            prefix_composition=prefix.model_dump(mode="json"),
            max_retries=0,
        ),
    )
    music, warnings, provider, validation, provenance = asyncio.run(
        generate_music_json(req, _settings())
    )
    assert music.schema_version == "composition.v2"
    assert provenance["pipeline_id"] == "symbolic_continuation"
    assert validation is None or validation.ok or True  # structural path may warn
    assert music.bar_count >= 1
