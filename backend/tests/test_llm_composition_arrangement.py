"""LLM orchestration tests for composition arrangement (Task 6)."""

from __future__ import annotations

import asyncio
import socket
from unittest.mock import AsyncMock, patch

import pytest

from app.arrangement_schemas import (
    CompositionArrangementDraft,
    CompositionArrangementError,
    CompositionArrangementPreviewRequest,
)
from app.llm_settings import LLMProviderSettings, LLMSettings, load_llm_settings
from app.schemas import LLMModelSelection
from app.services.composition_edit_fingerprint import composition_edit_fingerprint
from app.services.llm_composition_arrangement import run_composition_arrangement_preview
from tests.test_composition_arrangement_context import (
    _acceptance_instrumentation,
    _part,
    _piano_sketch_v2,
)


def _settings(*providers: LLMProviderSettings, default: str | None = None) -> LLMSettings:
    return LLMSettings(
        providers=providers,
        default_provider=default or (providers[0].provider if providers else None),
        request_timeout_seconds=30,
        temperature=0.4,
    )


def _fake_settings() -> LLMSettings:
    return _settings(
        LLMProviderSettings(provider="fake", model="fake-deterministic", api_key="unused", is_default=True),
        default="fake",
    )


def _base_request(**overrides) -> CompositionArrangementPreviewRequest:
    composition = overrides.pop("composition", _piano_sketch_v2())
    payload = {
        "composition": composition,
        "operation": "piano_to_ensemble",
        "source_track_ids": ["piano-melody", "piano-accomp", "bass-1"],
        "instrumentation": _acceptance_instrumentation(),
        "candidate_count": 1,
        "selection": LLMModelSelection(provider="fake", model="fake-deterministic"),
        "options": {"max_repairs": 1},
    }
    payload.update(overrides)
    return CompositionArrangementPreviewRequest.model_validate(payload)


def _operation_request(operation: str, **overrides) -> CompositionArrangementPreviewRequest:
    composition = _piano_sketch_v2()
    builders = {
        "change_instrumentation": {
            "source_track_ids": ["piano-melody"],
            "instrumentation": {
                "before": [
                    _part(
                        "b-melody",
                        "acoustic_grand_piano",
                        role="melody",
                        source_track_ids=["piano-melody"],
                    )
                ],
                "after": [_part("a-melody", "violin", role="melody")],
            },
        },
        "add_accompaniment": {
            "source_track_ids": ["piano-melody", "piano-accomp", "bass-1"],
            "instrumentation": {
                "before": _acceptance_instrumentation()["before"],
                "after": _acceptance_instrumentation()["before"]
                + [_part("a-pad", "synth_pad_new_age", role="pad")],
            },
        },
        "remove_accompaniment": {
            "source_track_ids": ["piano-accomp"],
            "protected_track_ids": ["piano-melody"],
            "instrumentation": {
                "before": [
                    _part(
                        "b-accomp",
                        "acoustic_grand_piano",
                        role="harmony",
                        source_track_ids=["piano-accomp"],
                    )
                ],
                "after": [_part("a-kept", "acoustic_grand_piano", role="melody")],
            },
            "allow_unlisted_after": True,
        },
        "orchestrate_selected_tracks": {
            "source_track_ids": ["piano-melody", "piano-accomp", "bass-1"],
            "instrumentation": {
                "before": _acceptance_instrumentation()["before"],
                "after": [
                    _part("a-v", "violin", role="melody"),
                    _part("a-c", "cello", role="bass"),
                    _part("a-s", "string_ensemble_1", role="harmony"),
                ],
            },
        },
        "piano_to_ensemble": {
            "source_track_ids": ["piano-melody", "piano-accomp", "bass-1"],
            "instrumentation": _acceptance_instrumentation(),
        },
        "simplify_arrangement": {
            "source_track_ids": ["piano-accomp"],
            "protected_track_ids": ["piano-melody"],
            "instrumentation": {
                "before": [
                    _part(
                        "b-a",
                        "acoustic_grand_piano",
                        role="harmony",
                        source_track_ids=["piano-accomp"],
                    )
                ],
                "after": [_part("a-a", "acoustic_grand_piano", role="harmony")],
            },
        },
        "increase_texture_density": {
            "source_track_ids": ["piano-accomp"],
            "instrumentation": {
                "before": [
                    _part(
                        "b-a",
                        "acoustic_grand_piano",
                        role="harmony",
                        source_track_ids=["piano-accomp"],
                    )
                ],
                "after": [_part("a-a", "acoustic_grand_piano", role="harmony")],
            },
        },
        "decrease_texture_density": {
            "source_track_ids": ["piano-accomp"],
            "protected_track_ids": ["piano-melody"],
            "instrumentation": {
                "before": [
                    _part(
                        "b-a",
                        "acoustic_grand_piano",
                        role="harmony",
                        source_track_ids=["piano-accomp"],
                    )
                ],
                "after": [_part("a-a", "acoustic_grand_piano", role="harmony")],
            },
        },
        "create_countermelody": {
            "source_track_ids": ["piano-melody"],
            "instrumentation": {
                "before": [
                    _part(
                        "b1",
                        "acoustic_grand_piano",
                        role="melody",
                        source_track_ids=["piano-melody"],
                    )
                ],
                "after": [
                    _part("a-mel", "acoustic_grand_piano", role="melody"),
                    _part("a-cm", "flute", role="countermelody"),
                ],
            },
        },
        "double_melody": {
            "source_track_ids": ["piano-melody"],
            "instrumentation": {
                "before": [
                    _part(
                        "b-m",
                        "acoustic_grand_piano",
                        role="melody",
                        source_track_ids=["piano-melody"],
                    )
                ],
                "after": [
                    _part("a-m", "acoustic_grand_piano", role="melody"),
                    {
                        "part_id": "a-d",
                        "instrument_id": "violin",
                        "role": "melody",
                        "source_track_ids": ["piano-melody"],
                        "doubling_policy": "octave",
                    },
                ],
            },
        },
    }
    payload = {
        "composition": composition,
        "operation": operation,
        "candidate_count": 1,
        "selection": LLMModelSelection(provider="fake", model="fake-deterministic"),
        **builders[operation],
    }
    payload.update(overrides)
    return CompositionArrangementPreviewRequest.model_validate(payload)


@pytest.mark.parametrize(
    "operation",
    [
        "change_instrumentation",
        "add_accompaniment",
        "remove_accompaniment",
        "orchestrate_selected_tracks",
        "piano_to_ensemble",
        "simplify_arrangement",
        "increase_texture_density",
        "decrease_texture_density",
        "create_countermelody",
        "double_melody",
    ],
)
def test_fake_every_arrangement_operation(monkeypatch, operation):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    response = asyncio.run(
        run_composition_arrangement_preview(_operation_request(operation), settings=_fake_settings())
    )
    assert len(response.candidates) == 1
    assert response.operation == operation
    assert response.candidates[0].operation == operation
    assert response.candidates[0].composition.schema_version == "composition.v2"
    assert all(track.events for track in response.candidates[0].composition.tracks) or operation == "remove_accompaniment"


def test_acceptance_example_piano_cello_strings(monkeypatch):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    source = _piano_sketch_v2()
    source_fp = composition_edit_fingerprint(source)
    response = asyncio.run(
        run_composition_arrangement_preview(_base_request(composition=source), settings=_fake_settings())
    )
    candidate = response.candidates[0]
    assert response.edit_source_fingerprint == source_fp
    assert candidate.edit_source_fingerprint == source_fp
    instruments = {track.instrument.lower() for track in candidate.composition.tracks}
    assert any("piano" in name for name in instruments)
    assert any("cello" in name for name in instruments)
    assert any("string" in name for name in instruments)
    by_role = {track.role: track for track in candidate.composition.tracks}
    assert [event.pitch for event in by_role["melody"].events] == ["E4", "G4", "C5", "D5"]
    assert len(by_role["bass"].events) == 2
    assert len(by_role["harmony"].events) == 5
    assert candidate.composition.harmony == source.harmony
    assert candidate.composition.key == source.key
    # Never a preassembled fixture: source fingerprint differs from candidate.
    assert candidate.candidate_fingerprint != source_fp


def test_one_to_four_separate_candidate_calls(monkeypatch):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    calls: list[int] = []

    from app.services import llm_composition_arrangement as module

    original = module._generate_one_candidate_draft

    async def wrapped(**kwargs):
        calls.append(kwargs["candidate_ordinal"])
        return await original(**kwargs)

    with patch.object(module, "_generate_one_candidate_draft", side_effect=wrapped):
        response = asyncio.run(
            run_composition_arrangement_preview(
                _base_request(candidate_count=4),
                settings=_fake_settings(),
            )
        )
    assert calls == [1, 2, 3, 4]
    assert len(response.candidates) == 4
    ids = {item.candidate_id for item in response.candidates}
    fps = {item.candidate_fingerprint for item in response.candidates}
    assert len(ids) == 4
    assert len(fps) == 4


def test_candidate_independence_failed_repair_does_not_contaminate(monkeypatch):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    from app.services import llm_composition_arrangement as module

    original = module._generate_one_candidate_draft
    call_count = {"n": 0}

    async def flaky(**kwargs):
        call_count["n"] += 1
        ordinal = kwargs["candidate_ordinal"]
        # Fail first candidate's first attempt only.
        if ordinal == 1 and kwargs.get("repair_codes") is None:
            raise CompositionArrangementError(
                "arrangement_draft_invalid",
                details={"error_codes": ["arrangement_draft_invalid"]},
            )
        return await original(**kwargs)

    with patch.object(module, "_generate_one_candidate_draft", side_effect=flaky):
        response = asyncio.run(
            run_composition_arrangement_preview(
                _base_request(candidate_count=2, options={"max_repairs": 1}),
                settings=_fake_settings(),
            )
        )
    assert len(response.candidates) == 2
    assert "candidate_repaired" in response.warning_codes or any(
        "candidate_repaired" in c.warning_codes for c in response.candidates
    )


def test_openai_and_deepseek_selection(monkeypatch):
    monkeypatch.delenv("LLM_FAKE_MODE", raising=False)
    draft = CompositionArrangementDraft.model_validate(
        {
            "parts": [
                {
                    "action": "redistribute",
                    "part_id": "a-piano",
                    "source_track_ids": ["piano-melody"],
                    "source_note_refs": ["snr-0001"],
                }
            ]
        }
    )

    async def fake_invoke(**_kwargs):
        return draft

    openai_settings = _settings(
        LLMProviderSettings(provider="openai", model="gpt-4o-mini", api_key="sk-test", is_default=True),
        LLMProviderSettings(
            provider="deepseek",
            model="deepseek-chat",
            api_key="sk-deep",
            base_url="https://api.deepseek.com",
        ),
        default="openai",
    )

    with patch(
        "app.services.llm_composition_arrangement._invoke_structured_draft",
        new=AsyncMock(side_effect=fake_invoke),
    ) as mocked:
        # OpenAI selection — may fail realization due to incomplete draft; still proves selection path.
        with pytest.raises(CompositionArrangementError) as exc:
            asyncio.run(
                run_composition_arrangement_preview(
                    _base_request(
                        selection=LLMModelSelection(provider="openai", model="gpt-override"),
                        options={"max_repairs": 0},
                    ),
                    settings=openai_settings,
                )
            )
        assert exc.value.code == "arrangement_candidate_exhausted"
        assert mocked.await_count >= 1

        mocked.reset_mock()
        with pytest.raises(CompositionArrangementError):
            asyncio.run(
                run_composition_arrangement_preview(
                    _base_request(
                        selection=LLMModelSelection(provider="deepseek", model="deepseek-chat"),
                        options={"max_repairs": 0},
                    ),
                    settings=openai_settings,
                )
            )
        assert mocked.await_count >= 1


def test_no_provider_raises(monkeypatch):
    monkeypatch.delenv("LLM_FAKE_MODE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(CompositionArrangementError) as exc:
        asyncio.run(
            run_composition_arrangement_preview(
                _base_request(selection=LLMModelSelection(provider="openai", model="gpt-4o-mini")),
                settings=load_llm_settings(),
            )
        )
    assert exc.value.code == "arrangement_provider_unavailable"
    assert exc.value.http_status == 503


def test_malformed_draft_exhausts(monkeypatch):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.setenv("LLM_FAKE_INJECT_MALFORMED", "arrangement")
    with pytest.raises(CompositionArrangementError) as exc:
        asyncio.run(
            run_composition_arrangement_preview(
                _base_request(options={"max_repairs": 0}),
                settings=_fake_settings(),
            )
        )
    assert exc.value.code == "arrangement_candidate_exhausted"
    assert exc.value.http_status == 502


def test_repair_success_then_ok(monkeypatch):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    from app.services import llm_composition_arrangement as module

    original = module._generate_one_candidate_draft
    state = {"failed": False}

    async def once_bad(**kwargs):
        if not state["failed"] and kwargs.get("repair_codes") is None:
            state["failed"] = True
            raise CompositionArrangementError(
                "arrangement_draft_invalid",
                details={"error_codes": ["arrangement_draft_invalid"]},
            )
        return await original(**kwargs)

    with patch.object(module, "_generate_one_candidate_draft", side_effect=once_bad):
        response = asyncio.run(
            run_composition_arrangement_preview(
                _base_request(options={"max_repairs": 1}),
                settings=_fake_settings(),
            )
        )
    assert len(response.candidates) == 1
    assert "candidate_repaired" in response.warning_codes


def test_repair_exhaustion(monkeypatch):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    from app.services import llm_composition_arrangement as module

    async def always_bad(**_kwargs):
        raise CompositionArrangementError(
            "arrangement_draft_invalid",
            details={"error_codes": ["arrangement_draft_invalid"]},
        )

    with patch.object(module, "_generate_one_candidate_draft", side_effect=always_bad):
        with pytest.raises(CompositionArrangementError) as exc:
            asyncio.run(
                run_composition_arrangement_preview(
                    _base_request(options={"max_repairs": 1}),
                    settings=_fake_settings(),
                )
            )
    assert exc.value.code == "arrangement_candidate_exhausted"
    assert exc.value.http_status == 502


def test_partial_success_with_rejected_attempt_summaries(monkeypatch):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    from app.services import llm_composition_arrangement as module

    original = module._generate_one_candidate_draft

    async def second_fails(**kwargs):
        if kwargs["candidate_ordinal"] == 2:
            raise CompositionArrangementError(
                "arrangement_draft_invalid",
                details={"error_codes": ["arrangement_draft_invalid"]},
            )
        return await original(**kwargs)

    with patch.object(module, "_generate_one_candidate_draft", side_effect=second_fails):
        response = asyncio.run(
            run_composition_arrangement_preview(
                _base_request(candidate_count=2, options={"max_repairs": 0}),
                settings=_fake_settings(),
            )
        )
    assert len(response.candidates) == 1
    assert len(response.rejected_attempts) >= 1
    rejected = response.rejected_attempts[0]
    assert rejected.ordinal == 2
    assert rejected.codes
    assert "candidate_partial_success" in response.warning_codes
    # Rejected attempt never carries a composition body.
    assert not hasattr(rejected, "composition") or getattr(rejected, "composition", None) is None


def test_timeout_error_sanitized(monkeypatch):
    monkeypatch.delenv("LLM_FAKE_MODE", raising=False)
    settings = _settings(
        LLMProviderSettings(provider="openai", model="gpt-4o-mini", api_key="sk-test", is_default=True),
    )

    async def boom(**_kwargs):
        raise TimeoutError("secret-endpoint timed out with api_key=sk-leak")

    with patch(
        "app.services.llm_composition_arrangement._invoke_structured_draft",
        new=AsyncMock(side_effect=boom),
    ):
        with pytest.raises(CompositionArrangementError) as exc:
            asyncio.run(
                run_composition_arrangement_preview(
                    _base_request(
                        selection=LLMModelSelection(provider="openai", model="gpt-4o-mini"),
                        options={"max_repairs": 0},
                    ),
                    settings=settings,
                )
            )
    assert exc.value.code == "arrangement_candidate_exhausted"
    assert "sk-leak" not in str(exc.value)
    assert "sk-leak" not in str(exc.value.details)


def test_context_caps_before_invocation(monkeypatch):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    from app.services import llm_composition_arrangement as module

    called = {"n": 0}

    async def should_not_run(**_kwargs):
        called["n"] += 1
        raise AssertionError("provider must not be invoked")

    with patch.object(module, "_generate_one_candidate_draft", side_effect=should_not_run):
        with pytest.raises(CompositionArrangementError) as exc:
            asyncio.run(
                run_composition_arrangement_preview(
                    _base_request(options={"context_budget_chars": 1000, "max_repairs": 0}),
                    settings=_fake_settings(),
                )
            )
    assert called["n"] == 0
    assert exc.value.code == "arrangement_request_too_large"
    assert exc.value.http_status == 422


def test_no_network_in_fake_mode(monkeypatch):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    original_create = socket.socket

    class GuardedSocket(original_create):
        def connect(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise AssertionError("network connect attempted in fake mode")

        def connect_ex(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise AssertionError("network connect_ex attempted in fake mode")

    monkeypatch.setattr(socket, "socket", GuardedSocket)
    response = asyncio.run(
        run_composition_arrangement_preview(_base_request(), settings=_fake_settings())
    )
    assert len(response.candidates) == 1
