"""Stem-aware neural audio schemas, partition, and HTTP coverage."""

from __future__ import annotations

import copy
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.ai_runtime.registry import reload_registry
from app.ai_runtime.runtimes.fake_neural_audio import FAKE_NEURAL_AUDIO_MODEL_ID
from app.db.connection import get_connection, reset_database_initialization_cache, run_alembic_upgrade
from app.main import app
from app.neural_audio_schemas import (
    NeuralAudioStemEnqueueRequest,
    NeuralAudioStemSetResponse,
)
from app.services.neural_audio_stem_partition import (
    filter_composition_tracks,
    infer_stem_role,
    partition_tracks_to_stems,
)
from tests.test_composition_v2_schema import minimal_v2


def _multi_stem_composition() -> dict:
    base = minimal_v2(
        tracks=[
            {
                "id": "piano-1",
                "name": "Piano",
                "instrument": "acoustic piano",
                "role": "melody",
                "midi_program": 0,
                "channel": 1,
                "events": [
                    {
                        "pitch": "C4",
                        "start_tick": 0,
                        "duration_ticks": 480,
                        "velocity": 80,
                    }
                ],
            },
            {
                "id": "bass-1",
                "name": "Bass",
                "instrument": "acoustic bass",
                "role": "bass",
                "midi_program": 32,
                "channel": 2,
                "events": [
                    {
                        "pitch": "C2",
                        "start_tick": 0,
                        "duration_ticks": 480,
                        "velocity": 70,
                    }
                ],
            },
            {
                "id": "strings-1",
                "name": "Strings",
                "instrument": "string ensemble",
                "role": "harmony",
                "midi_program": 48,
                "channel": 3,
                "events": [
                    {
                        "pitch": "E4",
                        "start_tick": 0,
                        "duration_ticks": 960,
                        "velocity": 60,
                    }
                ],
            },
        ]
    )
    return base


@pytest.fixture
def neural_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    db_path = tmp_path / "projects.db"
    render_root = tmp_path / "neural_audio_renders"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("NEURAL_AUDIO_RENDER_ROOT", str(render_root))
    monkeypatch.setenv("NEURAL_AUDIO_FAKE_MODE", "1")
    monkeypatch.setenv("NEURAL_AUDIO_ENGINE", "fake:neural-audio")
    monkeypatch.setenv("AI_OP_AUDIO_RENDER", FAKE_NEURAL_AUDIO_MODEL_ID)
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    reset_database_initialization_cache()
    run_alembic_upgrade(db_path)
    reload_registry()
    return tmp_path


@pytest.fixture
def client(neural_env: Path):
    with TestClient(app) as test_client:
        yield test_client


def test_stem_enqueue_schema_accepts_composition() -> None:
    req = NeuralAudioStemEnqueueRequest.model_validate(
        {
            "composition": _multi_stem_composition(),
            "engine": "neural",
            "stem_roles": ["piano", "bass", "strings"],
        }
    )
    assert req.engine == "neural"
    assert req.stem_roles == ["piano", "bass", "strings"]


def test_stem_enqueue_rejects_missing_source() -> None:
    with pytest.raises(ValidationError):
        NeuralAudioStemEnqueueRequest.model_validate({"engine": "neural"})


def test_infer_stem_roles() -> None:
    assert infer_stem_role({"instrument": "acoustic piano", "role": "melody"}) == "piano"
    assert infer_stem_role({"instrument": "bass", "role": "bass"}) == "bass"
    assert infer_stem_role({"instrument": "string ensemble", "role": "harmony"}) == "strings"
    assert infer_stem_role({"is_drum": True, "role": "drums", "instrument": "kit"}) == "drums"


def test_partition_and_filter_tracks() -> None:
    composition = _multi_stem_composition()
    groups = partition_tracks_to_stems(composition)
    roles = {role for role, _ in groups}
    assert "piano" in roles
    assert "bass" in roles
    assert "strings" in roles
    piano_ids = next(ids for role, ids in groups if role == "piano")
    sliced = filter_composition_tracks(composition, piano_ids)
    assert len(sliced["tracks"]) == 1
    assert sliced["tracks"][0]["id"] == "piano-1"


def test_migration_creates_stem_tables(neural_env: Path) -> None:
    path = Path(os.environ["PROJECT_DB_PATH"])
    with get_connection(path) as conn:
        sets = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='neural_audio_stem_sets'"
        ).fetchone()
        stems = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='neural_audio_stems'"
        ).fetchone()
    assert sets is not None
    assert stems is not None


def test_stem_set_render_and_selective_strings_rerender(client: TestClient) -> None:
    composition = _multi_stem_composition()
    before = copy.deepcopy(composition)
    response = client.post(
        "/neural-audio/stem-sets",
        json={
            "composition": composition,
            "engine": "neural",
            "model_id": FAKE_NEURAL_AUDIO_MODEL_ID,
            "stem_roles": ["piano", "bass", "strings"],
            "seed": 3,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    set_body = NeuralAudioStemSetResponse.model_validate(payload)
    assert set_body.mutates_composition is False
    assert set_body.status == "complete"
    roles = {stem.stem_role for stem in set_body.stems if stem.status == "complete"}
    assert roles == {"piano", "bass", "strings"}
    strings = next(stem for stem in set_body.stems if stem.stem_role == "strings")
    piano = next(stem for stem in set_body.stems if stem.stem_role == "piano")
    assert strings.sha256_prefix
    assert piano.sha256_prefix
    assert strings.sync_class == "generative_independent"
    assert all(s.capability_used == "direct_stems" for s in set_body.stems)

    audio = client.get(f"/neural-audio/stems/{strings.id}/audio")
    assert audio.status_code == 200
    assert audio.content[:4] == b"RIFF"
    piano_audio = client.get(f"/neural-audio/stems/{piano.id}/audio")
    assert piano_audio.status_code == 200
    assert piano_audio.content != audio.content
    assert piano.sha256_prefix != strings.sha256_prefix

    rerender = client.post(
        f"/neural-audio/stem-sets/{set_body.id}/stems/{strings.id}/rerender",
        json={
            "composition": composition,
            "seed": 99,
            "model_id": FAKE_NEURAL_AUDIO_MODEL_ID,
        },
    )
    assert rerender.status_code == 200, rerender.text
    after = NeuralAudioStemSetResponse.model_validate(rerender.json())
    string_stems = [s for s in after.stems if s.stem_role == "strings"]
    assert len(string_stems) >= 2
    newest = max(string_stems, key=lambda s: s.created_at)
    assert newest.supersedes_stem_id == strings.id
    assert newest.status == "complete"
    # Prior piano stem still present and complete.
    assert any(
        s.stem_role == "piano" and s.id == piano.id and s.status == "complete"
        for s in after.stems
    )
    # Symbolic composition unchanged by renders.
    assert composition == before


def test_ai_models_expose_stem_capabilities(client: TestClient) -> None:
    response = client.get("/ai/models", params={"operation": "audio_render"})
    assert response.status_code == 200
    models = response.json()["models"]
    fake = next(m for m in models if m["id"] == FAKE_NEURAL_AUDIO_MODEL_ID)
    assert "direct_stems" in fake["stem_capabilities"]
    assert "per_track" in fake["stem_capabilities"]


def test_stem_set_response_never_includes_pcm(client: TestClient) -> None:
    response = client.post(
        "/neural-audio/stem-sets",
        json={
            "composition": _multi_stem_composition(),
            "model_id": FAKE_NEURAL_AUDIO_MODEL_ID,
            "stem_roles": ["piano", "bass"],
        },
    )
    assert response.status_code == 200
    raw = response.text.lower()
    assert "riff" not in raw
    assert "audio_bytes" not in raw


def test_stem_capability_unsupported_422(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.services.neural_audio_stems.resolve_capability_for_partition",
        lambda **_kwargs: "",
    )
    response = client.post(
        "/neural-audio/stem-sets",
        json={
            "composition": _multi_stem_composition(),
            "model_id": FAKE_NEURAL_AUDIO_MODEL_ID,
            "stem_roles": ["piano"],
        },
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "stem_capability_unsupported"


def test_section_symbolic_filter_bar_range(client: TestClient) -> None:
    from app.services.neural_audio_stem_partition import filter_composition_bar_range

    composition = _multi_stem_composition()
    # Bar 2 event (4/4 @ 480 tpq → bar 2 starts at 1920).
    composition["tracks"][0]["events"].append(
        {
            "pitch": "D4",
            "start_tick": 1920,
            "duration_ticks": 480,
            "velocity": 70,
        }
    )
    filtered = filter_composition_bar_range(
        composition, {"start_bar": 1, "end_bar": 1}
    )
    piano_events = filtered["tracks"][0]["events"]
    assert len(piano_events) == 1
    assert piano_events[0]["start_tick"] == 0

    response = client.post(
        "/neural-audio/stem-sets",
        json={
            "composition": composition,
            "model_id": FAKE_NEURAL_AUDIO_MODEL_ID,
            "stem_roles": ["piano"],
            "bar_range": {"start_bar": 1, "end_bar": 1},
        },
    )
    assert response.status_code == 200, response.text
    set_body = NeuralAudioStemSetResponse.model_validate(response.json())
    assert set_body.status == "complete"
    assert len(set_body.stems) == 1
    assert set_body.stems[0].capability_used == "section_symbolic_filter"
    assert set_body.stems[0].bar_range is not None
    assert set_body.stems[0].bar_range.start_bar == 1
    assert set_body.stems[0].bar_range.end_bar == 1


def test_per_track_capability_when_direct_stems_unavailable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import neural_audio_stems as stems_mod

    real_resolve = stems_mod._resolve_audio_model

    def resolve_without_direct(model_id, settings=None, env=None):
        model, fidelity, adapter = real_resolve(model_id, settings=settings, env=env)
        model.descriptor.limits["stem_capabilities"] = [
            "per_track",
            "grouped_tracks",
            "section_symbolic_filter",
        ]
        return model, fidelity, adapter

    monkeypatch.setattr(stems_mod, "_resolve_audio_model", resolve_without_direct)
    monkeypatch.setattr(
        stems_mod,
        "stem_capabilities_for_engine",
        lambda **_kwargs: frozenset(
            {"per_track", "grouped_tracks", "section_symbolic_filter"}
        ),
    )
    response = client.post(
        "/neural-audio/stem-sets",
        json={
            "composition": _multi_stem_composition(),
            "model_id": FAKE_NEURAL_AUDIO_MODEL_ID,
            "stem_roles": ["piano", "bass"],
            "seed": 7,
        },
    )
    assert response.status_code == 200, response.text
    set_body = NeuralAudioStemSetResponse.model_validate(response.json())
    assert set_body.status == "complete"
    assert {s.capability_used for s in set_body.stems} == {"per_track"}
    piano = next(s for s in set_body.stems if s.stem_role == "piano")
    bass = next(s for s in set_body.stems if s.stem_role == "bass")
    assert piano.sha256_prefix != bass.sha256_prefix


def test_fluidsynth_stem_path_skip_or_complete(client: TestClient) -> None:
    from app.services.composition_wav import load_wav_renderer_config

    config = load_wav_renderer_config()
    if not config.fluidsynth_exists or not config.soundfont_exists:
        pytest.skip(
            "FluidSynth binary or SoundFont missing "
            f"(bin={config.fluidsynth_basename}, sf={config.soundfont_basename})"
        )
    response = client.post(
        "/neural-audio/stem-sets",
        json={
            "composition": _multi_stem_composition(),
            "engine": "fluidsynth",
            "stem_roles": ["piano"],
        },
    )
    assert response.status_code == 200, response.text
    set_body = NeuralAudioStemSetResponse.model_validate(response.json())
    assert set_body.engine == "fluidsynth"
    assert set_body.stems[0].capability_used == "fluidsynth_deterministic"
    assert set_body.stems[0].sync_class == "deterministic_midi"
    assert set_body.stems[0].fidelity_class == "deterministic"


def test_fluidsynth_stem_unavailable_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.composition_wav import CompositionWavError

    def _boom(_composition):
        raise CompositionWavError("FluidSynth missing", unavailable=True)

    monkeypatch.setattr(
        "app.services.neural_audio_stems._render_fluidsynth_stem",
        _boom,
    )
    response = client.post(
        "/neural-audio/stem-sets",
        json={
            "composition": _multi_stem_composition(),
            "engine": "fluidsynth",
            "stem_roles": ["piano"],
        },
    )
    # Inline run marks set failed; enqueue itself still returns 200 with failed status,
    # or maps availability at render time — assert no silent neural swap.
    assert response.status_code == 200, response.text
    set_body = NeuralAudioStemSetResponse.model_validate(response.json())
    assert set_body.engine == "fluidsynth"
    assert set_body.status == "failed"
    assert set_body.stems[0].status == "failed"
    assert set_body.stems[0].error_code == "neural_audio_engine_unavailable"
