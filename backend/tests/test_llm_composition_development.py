"""LLM orchestration tests for composition development (Task 4)."""

from __future__ import annotations

import asyncio

import pytest

from app.composition_development_schemas import (
    CompositionDevelopmentError,
    CompositionDevelopmentPreviewRequest,
)
from app.llm_settings import load_llm_settings
from app.schemas import LLMModelSelection
from app.services.llm_composition_development import run_composition_development_preview
from app.services.llm_music_generator import NoLLMProviderConfiguredError
from tests.test_composition_development_patch import _sixteen_bar_a


def _continue_request(**overrides) -> CompositionDevelopmentPreviewRequest:
    payload = {
        "composition": _sixteen_bar_a(),
        "operation": "continue",
        "output_bars": 8,
        "development_intent": "continue",
        "variation_strength": "balanced",
        "candidate_count": 1,
        "selection": LLMModelSelection(provider="fake", model="fake-deterministic"),
    }
    payload.update(overrides)
    return CompositionDevelopmentPreviewRequest.model_validate(payload)


def test_fake_continue_16_plus_8(monkeypatch):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    response = asyncio.run(
        run_composition_development_preview(_continue_request(), settings=load_llm_settings())
    )
    assert len(response.candidates) == 1
    cand = response.candidates[0]
    assert cand.composition.bar_count == 24
    assert cand.composition.duration_ticks == 24 * 1920
    assert response.edit_source_fingerprint == cand.edit_source_fingerprint
    prefix = _sixteen_bar_a()
    for src_ev, out_ev in zip(
        prefix.tracks[0].events,
        cand.composition.tracks[0].events[: len(prefix.tracks[0].events)],
        strict=True,
    ):
        assert src_ev.model_dump() == out_ev.model_dump()


def test_three_candidates_are_distinct(monkeypatch):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    response = asyncio.run(
        run_composition_development_preview(
            _continue_request(candidate_count=3),
            settings=load_llm_settings(),
        )
    )
    assert len(response.candidates) == 3
    ids = {c.candidate_id for c in response.candidates}
    fps = {c.candidate_fingerprint for c in response.candidates}
    assert len(ids) == 3
    assert len(fps) == 3


def test_add_section_fake(monkeypatch):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    response = asyncio.run(
        run_composition_development_preview(
            _continue_request(
                operation="add_section",
                target_section_type="verse",
                development_intent="develop",
            ),
            settings=load_llm_settings(),
        )
    )
    assert response.candidates[0].composition.bar_count == 24
    assert any(s.type == "verse" for s in response.candidates[0].composition.sections)


def test_vary_section_fake(monkeypatch):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    response = asyncio.run(
        run_composition_development_preview(
            CompositionDevelopmentPreviewRequest.model_validate(
                {
                    "composition": _sixteen_bar_a(),
                    "operation": "vary_section",
                    "source": {"start_bar": 9, "end_bar": 16},
                    "variation_strength": "balanced",
                    "candidate_count": 1,
                    "selection": {"provider": "fake", "model": "fake-deterministic"},
                }
            ),
            settings=load_llm_settings(),
        )
    )
    assert response.candidates[0].composition.bar_count == 16
    assert response.candidates[0].source_range.start_bar == 9


def test_malformed_injection_exhausts_candidates(monkeypatch):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.setenv("LLM_FAKE_INJECT_MALFORMED", "development")
    with pytest.raises(CompositionDevelopmentError) as exc:
        asyncio.run(
            run_composition_development_preview(_continue_request(), settings=load_llm_settings())
        )
    assert exc.value.code == "development_candidate_exhausted"


def test_missing_provider_raises(monkeypatch):
    monkeypatch.delenv("LLM_FAKE_MODE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(NoLLMProviderConfiguredError):
        asyncio.run(
            run_composition_development_preview(
                _continue_request(selection=LLMModelSelection(provider="openai", model="gpt-4o-mini")),
                settings=load_llm_settings(),
            )
        )
