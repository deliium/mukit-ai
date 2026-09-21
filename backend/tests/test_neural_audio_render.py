"""Neural audio render settings, store, fake engine, and HTTP coverage."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.ai_runtime.registry import reload_registry
from app.ai_runtime.runtimes.fake_neural_audio import (
    FAKE_NEURAL_AUDIO_MODEL_ID,
    build_fake_neural_wav,
    fake_neural_audio_sha256_prefix,
)
from app.composition_schemas import CompositionV2
from app.db.connection import get_connection, reset_database_initialization_cache, run_alembic_upgrade
from app.main import app
from app.neural_audio_schemas import NeuralAudioError
from app.neural_audio_settings import load_neural_audio_settings
from app.services.composition_snapshot_encoding import composition_snapshot_fingerprint
from app.services.neural_audio_adapters import run_adapter
from app.services.neural_audio_render_store import write_audio_bytes
from tests.test_composition_v2_schema import minimal_v2


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


def test_settings_default_root_beside_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db = tmp_path / "data" / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db))
    monkeypatch.delenv("NEURAL_AUDIO_RENDER_ROOT", raising=False)
    settings = load_neural_audio_settings()
    assert settings.render_root == db.parent / "neural_audio_renders"
    assert "datasets" not in str(settings.render_root)


def test_settings_clamp(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setenv("NEURAL_AUDIO_MAX_CONCURRENCY", "99")
    with caplog.at_level(logging.WARNING):
        settings = load_neural_audio_settings()
    assert settings.max_concurrency == 4


def test_fake_wav_golden_hash_stable() -> None:
    a = build_fake_neural_wav(fingerprint="fp1", seed=7)
    b = build_fake_neural_wav(fingerprint="fp1", seed=7)
    assert a == b
    assert fake_neural_audio_sha256_prefix(fingerprint="fp1", seed=7) == hashlib.sha256(a).hexdigest()[
        :16
    ]
    assert a[:4] == b"RIFF"


def test_adapter_text_prompt_preserves_notes_false() -> None:
    composition = minimal_v2(
        tracks=[
            {
                "id": "piano-1",
                "name": "Piano",
                "instrument": "piano",
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
            }
        ]
    )
    artifact = run_adapter(
        "text_prompt",
        composition,
        instructions="soft pad",
        genre="ambient",
        mood="calm",
    )
    assert artifact.preserves_notes is False
    assert "generative_approximation" in artifact.warnings
    assert "C4" not in artifact.prompt_text  # structured metadata only


def test_migration_creates_table(neural_env: Path) -> None:
    db_path = neural_env / "does-not-matter"
    # neural_env already upgraded PROJECT_DB_PATH
    import os

    path = Path(os.environ["PROJECT_DB_PATH"])
    with get_connection(path) as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='neural_audio_renders'"
        ).fetchone()
    assert row is not None


def test_enqueue_fake_completes_and_fingerprint_unchanged(
    client: TestClient, neural_env: Path, caplog: pytest.LogCaptureFixture
) -> None:
    composition = minimal_v2(
        tracks=[
            {
                "id": "piano-1",
                "name": "Piano",
                "instrument": "piano",
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
            }
        ]
    )
    before = composition_snapshot_fingerprint(CompositionV2.model_validate(composition))
    with caplog.at_level(logging.INFO):
        response = client.post(
            "/neural-audio/renders",
            json={
                "composition": composition,
                "instructions": "warm chamber",
                "model_id": FAKE_NEURAL_AUDIO_MODEL_ID,
                "seed": 1,
            },
        )
    assert response.status_code == 200, response.text
    job = response.json()
    assert job["status"] == "complete"
    assert job["mutates_composition"] is False
    assert job["fidelity_class"] == "generative"
    assert job["model_id"] == FAKE_NEURAL_AUDIO_MODEL_ID
    assert job["byte_size"] and job["byte_size"] > 0
    assert job["sha256_prefix"]
    after = composition_snapshot_fingerprint(CompositionV2.model_validate(composition))
    assert before == after

    # INFO logs must not include the full prompt text.
    info_text = " ".join(record.getMessage() for record in caplog.records if record.levelno == logging.INFO)
    assert "warm chamber" not in info_text

    audio = client.get(f"/neural-audio/renders/{job['id']}/audio")
    assert audio.status_code == 200
    assert audio.content[:4] == b"RIFF"

    listed = client.get("/neural-audio/renders", params={"project_id": "missing"})
    assert listed.status_code == 200
    assert listed.json()["total"] == 0

    deleted = client.delete(f"/neural-audio/renders/{job['id']}")
    assert deleted.status_code == 204
    missing = client.get(f"/neural-audio/renders/{job['id']}")
    assert missing.status_code == 404


def test_enqueue_without_engine_503(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("NEURAL_AUDIO_RENDER_ROOT", str(tmp_path / "renders"))
    monkeypatch.setenv("NEURAL_AUDIO_FAKE_MODE", "0")
    monkeypatch.setenv("NEURAL_AUDIO_ENGINE", "auto")
    monkeypatch.delenv("AI_OP_AUDIO_RENDER", raising=False)
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    reset_database_initialization_cache()
    run_alembic_upgrade(db_path)
    reload_registry()
    with TestClient(app) as client:
        response = client.post(
            "/neural-audio/renders",
            json={"composition": minimal_v2(), "instructions": "x"},
        )
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["code"] == "neural_audio_engine_unavailable"


def test_sidecar_http_mocked(neural_env: Path) -> None:
    from app.ai_runtime.runtimes.sidecar_musicgen import SidecarMusicGenAudioModel
    from app.ai_runtime.types import ModelDescriptor, ModelHealth
    from app.ai_runtime.capabilities import ModelCapability
    from app.ai_runtime.operations import AiOperation
    import asyncio
    import base64
    import json

    descriptor = ModelDescriptor(
        id="sidecar:musicgen",
        display_name="MusicGen",
        provider="sidecar",
        runtime="sidecar_musicgen",  # type: ignore[arg-type]
        primary_capability=ModelCapability.AUDIO_GENERATION,
        locality="local",
        model_version="1",
        supported_operations=(AiOperation.AUDIO_RENDER,),
        status="ready",
        health=ModelHealth(status="ready", detail="ok", credentials_present=True),
    )
    model = SidecarMusicGenAudioModel(
        descriptor, base_url="http://neural-audio:8090", timeout_seconds=5
    )
    fake_wav = build_fake_neural_wav(fingerprint="sidecar", seed=0)
    payload = {
        "audio_base64": base64.b64encode(fake_wav).decode("ascii"),
        "content_type": "audio/wav",
        "model_version": "mock-1",
    }

    class _Resp:
        status = 200

        def read(self, _n: int = -1) -> bytes:
            return json.dumps(payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    with patch(
        "app.ai_runtime.runtimes.sidecar_musicgen.urlopen",
        return_value=_Resp(),
    ):
        result = asyncio.run(model.render({"prompt": "x", "adapter_kind": "text_prompt"}))
    assert result["audio_bytes"][:4] == b"RIFF"
    assert result["fidelity_class"] == "generative"


def test_store_write_and_quota(neural_env: Path) -> None:
    import os

    from app.services import project_store as store
    from app.services.neural_audio_render_store import assert_enqueue_quota, insert_queued_job

    settings = load_neural_audio_settings()
    wav = build_fake_neural_wav(fingerprint="q", seed=0)
    path = Path(os.environ["PROJECT_DB_PATH"])
    project = store.create_project("Neural quota", composition=minimal_v2(), db_path=path)
    written = write_audio_bytes(
        settings,
        project_id=project.id,
        render_id="11111111-1111-1111-1111-111111111111",
        payload=wav,
    )
    assert written.byte_size == len(wav)
    with get_connection(path) as conn:
        for i in range(settings.max_renders_per_project):
            insert_queued_job(
                conn,
                render_id=f"00000000-0000-0000-0000-{i:012d}",
                project_id=project.id,
                source_revision_id=None,
                source_fingerprint="fp",
                model_id=FAKE_NEURAL_AUDIO_MODEL_ID,
                model_version="1",
                adapter_kind="text_prompt",
                fidelity_class="generative",
                instructions="",
                genre=None,
                mood=None,
                instrumentation_summary=None,
                tempo_bpm=100.0,
                seed=None,
            )
        with pytest.raises(NeuralAudioError) as exc_info:
            assert_enqueue_quota(conn, settings, project.id)
        assert exc_info.value.code == "neural_audio_quota_exceeded"
