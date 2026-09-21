"""Tests for generation pipeline options and LangGraph routing helpers."""

from __future__ import annotations

from app.schemas import LLMGenerationOptions, LLMMusicGenerationRequest, LLMPromptParameters
from app.services.llm_music_generator import (
    PIPELINE_HYBRID,
    PIPELINE_LLM_ONLY,
    _route_after_plan_themes,
    resolve_generation_pipeline,
)


def _request(**options):
    return LLMMusicGenerationRequest(
        prompt=LLMPromptParameters(
            mood="calm",
            genre="classical",
            duration_bars=8,
            instruments=["piano"],
        ),
        options=LLMGenerationOptions(**options),
    )


def test_generation_options_default_pipeline_is_llm_only() -> None:
    opts = LLMGenerationOptions()
    assert opts.pipeline == "llm_only"
    assert opts.seed is None


def test_resolve_generation_pipeline_hybrid() -> None:
    request = _request(pipeline="hybrid_plan_symbolic", seed=42)
    assert resolve_generation_pipeline(request) == PIPELINE_HYBRID
    assert request.options.seed == 42


def test_route_after_plan_themes_branches() -> None:
    assert (
        _route_after_plan_themes({"pipeline_id": PIPELINE_LLM_ONLY, "request": _request()})
        == "llm_compose"
    )
    assert (
        _route_after_plan_themes({"pipeline_id": PIPELINE_HYBRID, "request": _request()})
        == "hybrid"
    )
    assert (
        _route_after_plan_themes(
            {"pipeline_id": "symbolic_continuation", "request": _request()}
        )
        == "symbolic_prefix"
    )
