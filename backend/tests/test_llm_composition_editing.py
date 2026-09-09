import asyncio
import json
import logging

import pytest

from app.llm_settings import LLMProviderSettings, LLMSettings
from app.schemas import Composition, LLMCompositionEditRequest
from app.services import llm_composition_editor
from app.services.composition_region_patch import canonical_json_dumps
from app.services.llm_composition_editor import edit_composition_region
from app.services.llm_music_generator import InvalidLLMOutputError, LLMGenerationError, NoLLMProviderConfiguredError
from tests.test_composition_region_patch import BAR_TICKS_4_4, _sixteen_bar_composition


def _settings() -> LLMSettings:
    return LLMSettings(
        providers=(LLMProviderSettings(provider="openai", model="test-model", api_key="secret", is_default=True),),
        default_provider="openai",
        request_timeout_seconds=60,
        temperature=0.2,
    )


def _edit_request(
    instruction: str = "make this phrase more dramatic but keep the harmony",
    *,
    track_ids: list[str] | None = None,
    allow_added_tracks: bool = False,
    allow_harmony_changes: bool = False,
    composition: Composition | None = None,
) -> LLMCompositionEditRequest:
    return LLMCompositionEditRequest.model_validate(
        {
            "composition": (composition or _sixteen_bar_composition()).model_dump(mode="json"),
            "edit": {
                "instruction": instruction,
                "selection": {
                    "start_bar": 9,
                    "end_bar": 12,
                    "track_ids": track_ids if track_ids is not None else ["melody-1"],
                },
                "allow_added_tracks": allow_added_tracks,
                "allow_harmony_changes": allow_harmony_changes,
            },
            "selection": {"provider": "openai", "model": "test-model"},
            "options": {"max_retries": 1},
        }
    )


def _melody_patch_payload(
    pitches: list[str] | None = None,
    *,
    start_bar: int = 9,
    end_bar: int = 12,
    track_id: str = "melody-1",
    added_tracks: list[dict] | None = None,
) -> dict:
    pitches = pitches or ["G4", "A4", "B4", "C5"]
    starts = [8 * BAR_TICKS_4_4, 9 * BAR_TICKS_4_4, 10 * BAR_TICKS_4_4, 11 * BAR_TICKS_4_4]
    return {
        "schema_version": "composition.v1",
        "operation": "replace_region",
        "start_bar": start_bar,
        "end_bar": end_bar,
        "target_track_ids": [track_id],
        "replace_tracks": [
            {
                "track_id": track_id,
                "events": [
                    {
                        "type": "note",
                        "pitch": pitch,
                        "start_tick": start,
                        "duration_ticks": 480,
                        "velocity": 100,
                    }
                    for pitch, start in zip(pitches, starts, strict=True)
                ],
            }
        ],
        "added_tracks": added_tracks or [],
        "harmony_patch": None,
        "warnings": [],
    }


def _outside_melody_events(composition: Composition) -> list[dict]:
    melody = next(track for track in composition.tracks if track.id == "melody-1")
    v1_note_keys = (
        "type",
        "pitch",
        "start_tick",
        "duration_ticks",
        "velocity",
        "id",
        "staff",
        "voice",
    )
    return [
        {key: event.model_dump(mode="json").get(key) for key in v1_note_keys}
        for event in melody.events
        if event.start_tick < 8 * BAR_TICKS_4_4 or event.start_tick >= 12 * BAR_TICKS_4_4
    ]


def test_edit_composition_region_dramatic_melody_preserves_outside_and_harmony(monkeypatch, caplog):
    request = _edit_request()
    original = request.composition.model_copy(deep=True)
    patch_json = json.dumps(_melody_patch_payload(["D5", "E5", "F5", "G5"]))

    async def fake_chat(_state, _prompt):
        return patch_json

    monkeypatch.setattr(llm_composition_editor, "_invoke_edit_chat", fake_chat)

    with caplog.at_level(logging.INFO):
        composition, patch, warnings, provider = asyncio.run(edit_composition_region(request, _settings()))

    assert provider.provider == "openai"
    assert composition.schema_version == "composition.v2"
    assert patch.operation == "replace_region"
    assert patch.start_bar == 9 and patch.end_bar == 12
    assert composition.tempo == original.tempo
    assert composition.key == original.key
    assert composition.time_signature == original.time_signature
    assert composition.bar_count == original.bar_count
    assert composition.duration_ticks == original.duration_ticks
    assert composition.tempo_changes == []
    assert composition.markers == []
    from app.services.composition_normalizer import normalize_composition_json

    original_v2 = normalize_composition_json(original)
    assert canonical_json_dumps([item.model_dump(mode="json") for item in composition.harmony]) == (
        canonical_json_dumps([item.model_dump(mode="json") for item in original_v2.harmony])
    )
    assert canonical_json_dumps(_outside_melody_events(composition)) == canonical_json_dumps(
        _outside_melody_events(original)
    )
    assert "LLM composition region edit started" in caplog.text
    assert "LLM composition region edit completed" in caplog.text


def test_edit_composition_region_accepts_expressive_v2_patch(monkeypatch):
    request = _edit_request()
    starts = [8 * BAR_TICKS_4_4, 9 * BAR_TICKS_4_4, 10 * BAR_TICKS_4_4, 11 * BAR_TICKS_4_4]
    patch_payload = {
        "schema_version": "composition.v2",
        "operation": "replace_region",
        "start_bar": 9,
        "end_bar": 12,
        "target_track_ids": ["melody-1"],
        "replace_tracks": [
            {
                "track_id": "melody-1",
                "events": [
                    {
                        "type": "note",
                        "pitch": pitch,
                        "start_tick": start,
                        "duration_ticks": 480,
                        "velocity": 100,
                        "articulations": ["accent"] if index == 0 else [],
                        "tie": None,
                    }
                    for index, (pitch, start) in enumerate(
                        zip(["D5", "E5", "F5", "G5"], starts, strict=True)
                    )
                ],
            }
        ],
        "added_tracks": [],
        "harmony_patch": None,
        "warnings": [],
    }

    async def fake_chat(_state, _prompt):
        return json.dumps(patch_payload)

    monkeypatch.setattr(llm_composition_editor, "_invoke_edit_chat", fake_chat)
    composition, patch, _warnings, _provider = asyncio.run(edit_composition_region(request, _settings()))
    assert composition.schema_version == "composition.v2"
    assert patch.schema_version == "composition.v2"
    melody = next(track for track in composition.tracks if track.id == "melody-1")
    assert any(event.articulations == ["accent"] for event in melody.events)


@pytest.mark.parametrize(
    ("instruction", "pitches"),
    [
        ("make melody more active", ["C5", "D5", "E5", "G5"]),
        ("regenerate melody in selection", ["A4", "B4", "C5", "D5"]),
        ("increase tension in the selected section", ["Eb5", "F5", "G5", "Ab5"]),
    ],
)
def test_edit_composition_region_supported_melody_scenarios(monkeypatch, instruction, pitches):
    request = _edit_request(instruction)
    patch_json = json.dumps(_melody_patch_payload(pitches))

    async def fake_chat(_state, _prompt):
        return patch_json

    monkeypatch.setattr(llm_composition_editor, "_invoke_edit_chat", fake_chat)
    composition, patch, _warnings, _provider = asyncio.run(edit_composition_region(request, _settings()))
    assert patch.target_track_ids == ["melody-1"]
    melody = next(track for track in composition.tracks if track.id == "melody-1")
    in_region = [event for event in melody.events if 8 * BAR_TICKS_4_4 <= event.start_tick < 12 * BAR_TICKS_4_4]
    assert [event.pitch for event in in_region] == pitches


def test_edit_composition_region_simplify_accompaniment(monkeypatch):
    request = _edit_request("simplify accompaniment", track_ids=["harmony-1"])
    starts = [8 * BAR_TICKS_4_4, 9 * BAR_TICKS_4_4, 10 * BAR_TICKS_4_4, 11 * BAR_TICKS_4_4]
    payload = {
        "schema_version": "composition.v1",
        "operation": "replace_region",
        "start_bar": 9,
        "end_bar": 12,
        "target_track_ids": ["harmony-1"],
        "replace_tracks": [
            {
                "track_id": "harmony-1",
                "events": [
                    {"pitch": "C4", "start_tick": start, "duration_ticks": 480, "velocity": 60}
                    for start in starts
                ],
            }
        ],
        "added_tracks": [],
        "harmony_patch": None,
        "warnings": [],
    }

    async def fake_chat(_state, _prompt):
        return json.dumps(payload)

    monkeypatch.setattr(llm_composition_editor, "_invoke_edit_chat", fake_chat)
    composition, patch, _warnings, _provider = asyncio.run(edit_composition_region(request, _settings()))
    assert patch.target_track_ids == ["harmony-1"]
    harmony = next(track for track in composition.tracks if track.id == "harmony-1")
    assert len([e for e in harmony.events if 8 * BAR_TICKS_4_4 <= e.start_tick < 12 * BAR_TICKS_4_4]) == 4


def test_edit_composition_region_change_bass_line(monkeypatch):
    request = _edit_request("change bass line", track_ids=["bass-1"])
    payload = _melody_patch_payload(["C2", "D2", "E2", "F2"], track_id="bass-1")

    async def fake_chat(_state, _prompt):
        return json.dumps(payload)

    monkeypatch.setattr(llm_composition_editor, "_invoke_edit_chat", fake_chat)
    composition, patch, _warnings, _provider = asyncio.run(edit_composition_region(request, _settings()))
    assert patch.target_track_ids == ["bass-1"]
    bass = next(track for track in composition.tracks if track.id == "bass-1")
    assert [e.pitch for e in bass.events if 8 * BAR_TICKS_4_4 <= e.start_tick < 12 * BAR_TICKS_4_4] == [
        "C2",
        "D2",
        "E2",
        "F2",
    ]


def test_edit_composition_region_added_counter_melody(monkeypatch):
    request = _edit_request("add counter-melody", allow_added_tracks=True)
    added = [
        {
            "id": "counter-1",
            "name": "Counter",
            "instrument": "flute",
            "role": "countermelody",
            "midi_program": 73,
            "channel": 4,
            "staff": "treble",
            "events": [
                {"pitch": "G5", "start_tick": bar * BAR_TICKS_4_4, "duration_ticks": 480, "velocity": 70}
                for bar in range(16)
            ],
        }
    ]
    payload = _melody_patch_payload(added_tracks=added)

    async def fake_chat(_state, _prompt):
        return json.dumps(payload)

    monkeypatch.setattr(llm_composition_editor, "_invoke_edit_chat", fake_chat)
    composition, patch, _warnings, _provider = asyncio.run(edit_composition_region(request, _settings()))
    assert any(track.id == "counter-1" for track in composition.tracks)
    assert len(patch.added_tracks) == 1


def test_edit_composition_region_malformed_json_fails_non_destructively(monkeypatch, caplog):
    request = _edit_request()
    original_dump = request.composition.model_dump(mode="json")

    async def fake_chat(_state, _prompt):
        return "not-json"

    monkeypatch.setattr(llm_composition_editor, "_invoke_edit_chat", fake_chat)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(InvalidLLMOutputError):
            asyncio.run(edit_composition_region(request, _settings()))

    assert request.composition.model_dump(mode="json") == original_dump
    assert "Draft region patch failed" in caplog.text


def test_edit_composition_region_out_of_scope_mutation_triggers_repair_then_fails(monkeypatch, caplog):
    request = _edit_request()
    bad_payload = _melody_patch_payload()
    bad_payload["replace_tracks"][0]["events"][0]["start_tick"] = 0  # outside region
    calls = {"count": 0}

    async def fake_chat(_state, _prompt):
        calls["count"] += 1
        return json.dumps(bad_payload)

    monkeypatch.setattr(llm_composition_editor, "_invoke_edit_chat", fake_chat)

    with caplog.at_level(logging.WARNING):
        with pytest.raises(InvalidLLMOutputError):
            asyncio.run(edit_composition_region(request, _settings()))

    assert calls["count"] == 2  # draft + one repair
    assert "Attempting region patch repair" in caplog.text
    assert "Region patch repair exhausted" in caplog.text


def test_edit_composition_region_provider_failure(monkeypatch, caplog):
    request = _edit_request()

    async def fake_chat(_state, _prompt):
        raise LLMGenerationError("provider down")

    monkeypatch.setattr(llm_composition_editor, "_invoke_edit_chat", fake_chat)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(LLMGenerationError):
            asyncio.run(edit_composition_region(request, _settings()))

    assert "Draft region patch failed" in caplog.text or "LLM provider call failed" in caplog.text


def test_edit_composition_region_no_provider():
    request = _edit_request()
    settings = LLMSettings(providers=(), default_provider=None, request_timeout_seconds=30, temperature=0.2)
    with pytest.raises(NoLLMProviderConfiguredError):
        asyncio.run(edit_composition_region(request, settings))
