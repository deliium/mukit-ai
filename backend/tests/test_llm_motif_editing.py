"""Mocked-provider coverage for creative motif LangGraph drafting/repair."""

from __future__ import annotations

import asyncio
import json
import logging

import pytest

from app.composition_schemas import CompositionV2
from app.llm_settings import LLMProviderSettings, LLMSettings
from app.motif_schemas import MotifApplyRequest, MotifDestinationSelector, MotifSourceSelector
from app.services import llm_motif_editor
from app.services.composition_motif_editor import apply_motif_operation
from app.services.composition_motif_transform import (
    MelodicVariationProposal,
    RhythmicVariationProposal,
    build_motif_destination,
    extract_relative_motif,
)
from app.services.composition_region_patch import canonical_json_dumps
from app.services.llm_motif_editor import draft_creative_motif_transform
from app.services.llm_music_generator import InvalidLLMOutputError, LLMGenerationError
from tests.test_composition_v2_schema import _motif_definition, _motif_source_events, _motif_track, minimal_v2


def _run(coro):
    return asyncio.run(coro)


def _settings() -> LLMSettings:
    return LLMSettings(
        providers=(LLMProviderSettings(provider="openai", model="test-model", api_key="secret", is_default=True),),
        default_provider="openai",
        request_timeout_seconds=30,
        temperature=0.2,
    )


def _fake_settings() -> LLMSettings:
    return LLMSettings(
        providers=(LLMProviderSettings(provider="fake", model="fake-deterministic", api_key="unused", is_default=True),),
        default_provider="fake",
        request_timeout_seconds=30,
        temperature=0.2,
    )


def _two_section_composition(**overrides) -> CompositionV2:
    payload = minimal_v2(
        bar_count=4,
        duration_ticks=7680,
        sections=[
            {
                "id": "verse",
                "type": "verse",
                "start_bar": 1,
                "bar_count": 2,
                "start_tick": 0,
                "duration_ticks": 3840,
            },
            {
                "id": "chorus",
                "type": "chorus",
                "start_bar": 3,
                "bar_count": 2,
                "start_tick": 3840,
                "duration_ticks": 3840,
            },
        ],
        tracks=[_motif_track()],
        motifs=[_motif_definition()],
        harmony=[{"bar": 3, "chord": "C"}, {"bar": 4, "chord": "G"}],
    )
    payload.update(overrides)
    return CompositionV2.model_validate(payload)


def _creative_request(
    composition: CompositionV2,
    *,
    operation: str = "melodic_variation",
    variation_strength: float = 0.5,
    provider: str = "openai",
    model: str = "test-model",
) -> MotifApplyRequest:
    return MotifApplyRequest.model_validate(
        {
            "composition": composition,
            "source": MotifSourceSelector(motif_id="motif-a", occurrence_id="occ-orig"),
            "destination": MotifDestinationSelector(section_id="chorus", track_id="melody-1", start_bar=3),
            "operation": operation,
            "parameters": {},
            "variation_strength": variation_strength,
            "selection": {"provider": provider, "model": model},
            "options": {"max_retries": 1},
        }
    )


def _valid_melodic_proposal(note_count: int = 3) -> dict:
    return MelodicVariationProposal(
        note_count=note_count,
        pitch_semitone_offsets=[0, 1, 0][:note_count]
        if note_count <= 3
        else [0, 1, 0, -1][:note_count],
    ).model_dump(mode="json")


def _valid_rhythmic_proposal(source_notes) -> dict:
    return RhythmicVariationProposal(
        note_count=len(source_notes),
        onset_delta_ticks=[note.relative_start_tick for note in source_notes],
        duration_ticks=[note.duration_ticks for note in source_notes],
    ).model_dump(mode="json")


def _draft_inputs(request: MotifApplyRequest):
    composition = request.composition
    motif = next(item for item in composition.motifs if item.id == request.source.motif_id)
    occurrence = next(item for item in motif.occurrences if item.id == request.source.occurrence_id)
    extracted = extract_relative_motif(
        composition,
        track_id=occurrence.track_id,
        event_ids=occurrence.event_ids,
    )
    destination = build_motif_destination(
        composition,
        track_id=request.destination.track_id,
        start_tick=3840,
        anchor_midi=extracted.anchor_midi,
        allow_overlap=True,
    )
    return extracted.notes, destination


def test_creative_motif_valid_mocked_proposal_succeeds(monkeypatch):
    request = _creative_request(_two_section_composition())
    source_notes, destination = _draft_inputs(request)
    proposal = _valid_melodic_proposal(len(source_notes))

    async def fake_chat(_state, _prompt):
        return json.dumps(proposal)

    monkeypatch.setattr(llm_motif_editor, "_invoke_motif_chat", fake_chat)

    draft = _run(
        draft_creative_motif_transform(
            request=request,
            provider=_settings().providers[0],
            source_notes=source_notes,
            composition=request.composition,
            destination=destination,
            id_seed="seed-valid",
            settings=_settings(),
        )
    )
    assert draft.transform_result.verification.passed
    assert len(draft.transform_result.events) == len(source_notes)


def test_creative_motif_malformed_json_then_repair_success(monkeypatch, caplog):
    request = _creative_request(_two_section_composition(), operation="rhythmic_variation")
    source_notes, destination = _draft_inputs(request)
    proposal = _valid_rhythmic_proposal(source_notes)
    calls = {"count": 0}

    async def fake_chat(_state, _prompt):
        calls["count"] += 1
        if calls["count"] == 1:
            return "not-json{"
        return json.dumps(proposal)

    monkeypatch.setattr(llm_motif_editor, "_invoke_motif_chat", fake_chat)

    with caplog.at_level(logging.WARNING):
        draft = _run(
            draft_creative_motif_transform(
                request=request,
                provider=_settings().providers[0],
                source_notes=source_notes,
                composition=request.composition,
                destination=destination,
                id_seed="seed-repair",
                settings=_settings(),
            )
        )

    assert calls["count"] == 2
    assert draft.transform_result.verification.passed
    assert "Attempting motif variation repair" in caplog.text


def test_creative_motif_exhausted_identity_failure(monkeypatch, caplog):
    request = _creative_request(_two_section_composition(), variation_strength=0.0)
    source_notes, destination = _draft_inputs(request)
    wild = MelodicVariationProposal(
        note_count=len(source_notes),
        pitch_semitone_offsets=[0, 12, -12][: len(source_notes)]
        if len(source_notes) <= 3
        else [0, 12, -12, 12][: len(source_notes)],
    ).model_dump(mode="json")

    async def fake_chat(_state, _prompt):
        return json.dumps(wild)

    monkeypatch.setattr(llm_motif_editor, "_invoke_motif_chat", fake_chat)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(InvalidLLMOutputError):
            _run(
                draft_creative_motif_transform(
                    request=request,
                    provider=_settings().providers[0],
                    source_notes=source_notes,
                    composition=request.composition,
                    destination=destination,
                    id_seed="seed-identity-fail",
                    settings=_settings(),
                )
            )

    assert "Motif variation repair exhausted" in caplog.text


def test_creative_motif_provider_exception(monkeypatch):
    request = _creative_request(_two_section_composition())
    source_notes, destination = _draft_inputs(request)

    async def fake_chat(_state, _prompt):
        raise LLMGenerationError("provider down")

    monkeypatch.setattr(llm_motif_editor, "_invoke_motif_chat", fake_chat)

    with pytest.raises(LLMGenerationError):
        _run(
            draft_creative_motif_transform(
                request=request,
                provider=_settings().providers[0],
                source_notes=source_notes,
                composition=request.composition,
                destination=destination,
                id_seed="seed-provider-fail",
                settings=_settings(),
            )
        )


def test_creative_motif_prompt_bounds_exclude_full_events(monkeypatch):
    composition = _two_section_composition()
    # Add many absolute pitches so a leak would be obvious.
    track = composition.tracks[0]
    template = track.events[0]
    extra_events = [
        template.model_copy(update={"id": f"extra-{index}", "start_tick": 2000 + index * 60, "pitch": pitch})
        for index, pitch in enumerate(["C5", "D5", "E5", "F5", "G5", "A5", "B5", "C6"])
    ]
    composition = composition.model_copy(
        update={"tracks": [track.model_copy(update={"events": list(track.events) + extra_events})]}
    )
    request = _creative_request(composition)
    source_notes, destination = _draft_inputs(request)
    captured: dict[str, str] = {}

    async def fake_chat(_state, prompt):
        captured["prompt"] = prompt
        return json.dumps(_valid_melodic_proposal(len(source_notes)))

    monkeypatch.setattr(llm_motif_editor, "_invoke_motif_chat", fake_chat)
    _run(
        draft_creative_motif_transform(
            request=request,
            provider=_settings().providers[0],
            source_notes=source_notes,
            composition=composition,
            destination=destination,
            id_seed="seed-prompt-bounds",
            settings=_settings(),
        )
    )

    prompt = captured["prompt"]
    assert '"events"' not in prompt
    assert "relative_start_tick" in prompt
    assert "pitch_semitone_offset" in prompt
    for pitch in ["C5", "D5", "E5", "F5", "G5", "A5", "B5", "C6"]:
        assert pitch not in prompt
    assert "C4" not in prompt  # absolute source pitches must not appear


def test_creative_motif_preserves_source_and_destination_outside_span(monkeypatch):
    composition = _two_section_composition()
    original = composition.model_copy(deep=True)
    request = _creative_request(composition)
    proposal = _valid_melodic_proposal(3)

    async def fake_chat(_state, _prompt):
        return json.dumps(proposal)

    monkeypatch.setattr(llm_motif_editor, "_invoke_motif_chat", fake_chat)
    outcome = _run(apply_motif_operation(request, _settings()))

    source_ids = {"n1", "n2", "n3", "n4"}
    for event_id in source_ids:
        original_event = next(event for event in original.tracks[0].events if event.id == event_id)
        updated_event = next(event for event in outcome.composition.tracks[0].events if event.id == event_id)
        assert canonical_json_dumps(updated_event.model_dump(mode="json")) == canonical_json_dumps(
            original_event.model_dump(mode="json")
        )
    assert outcome.result.relationship == "melodic_variation"
    assert outcome.result.provider == "openai"
    assert outcome.result.identity_score >= 0.0


def test_creative_motif_fake_provider_path_success():
    request = _creative_request(
        _two_section_composition(),
        operation="answer",
        provider="fake",
        model="fake-deterministic",
    )
    outcome = _run(apply_motif_operation(request, _fake_settings()))
    assert outcome.result.provider == "fake"
    assert outcome.result.relationship == "answer"
    assert len(outcome.result.created_event_ids) >= 3
    assert outcome.result.diagnostics.identity_threshold is not None
    assert outcome.result.identity_score >= outcome.result.diagnostics.identity_threshold
