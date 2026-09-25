"""Neural render and stem-set correlation with an operation run."""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ai_runtime.registry import reload_registry
from app.ai_runtime.runtimes.fake_neural_audio import (
    FAKE_NEURAL_AUDIO_MODEL_ID,
    FakeNeuralAudioGenerationModel,
)
from app.db.connection import get_connection, reset_database_initialization_cache, run_alembic_upgrade
from app.main import app
from app.operation_trace import (
    mark_run_cancelled,
    remember_run_clock,
    reset_operation_trace_for_tests,
)
from tests.test_composition_v2_schema import minimal_v2
from tests.test_neural_audio_stems import _multi_stem_composition


def _composition() -> dict:
    return minimal_v2(
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


@pytest.fixture
def neural_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("NEURAL_AUDIO_RENDER_ROOT", str(tmp_path / "neural_audio_renders"))
    monkeypatch.setenv("NEURAL_AUDIO_FAKE_MODE", "1")
    monkeypatch.setenv("NEURAL_AUDIO_ENGINE", "fake:neural-audio")
    monkeypatch.setenv("AI_OP_AUDIO_RENDER", FAKE_NEURAL_AUDIO_MODEL_ID)
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.setenv("NEURAL_AUDIO_MAX_ATTEMPTS", "2")
    reset_database_initialization_cache()
    reset_operation_trace_for_tests()
    run_alembic_upgrade(db_path)
    reload_registry()
    return db_path


@pytest.fixture
def client(neural_env: Path):
    with TestClient(app) as test_client:
        yield test_client
    reset_operation_trace_for_tests()


def test_migration_adds_operation_columns(neural_env: Path) -> None:
    with get_connection(neural_env) as conn:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(neural_audio_renders)").fetchall()
        }
        stem_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(neural_audio_stem_sets)").fetchall()
        }
    assert "operation_run_id" in columns
    assert "attempt_count" in columns
    assert "operation_run_id" in stem_columns
    assert "attempt_count" in stem_columns


def test_cancelled_run_does_not_call_engine(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    async def boom(self, composition_or_spec):  # noqa: ANN001
        calls["n"] += 1
        raise AssertionError("engine should not run")

    monkeypatch.setattr(FakeNeuralAudioGenerationModel, "render", boom)
    run_id = str(uuid.uuid4())
    mark_run_cancelled(run_id)
    response = client.post(
        "/neural-audio/renders",
        json={
            "composition": _composition(),
            "model_id": FAKE_NEURAL_AUDIO_MODEL_ID,
            "operation_run_id": run_id,
            "instructions": "SECRET-RENDER-PROMPT",
        },
    )
    assert response.status_code == 200, response.text
    job = response.json()
    assert job["status"] == "failed"
    assert job["error_code"] == "operation_cancelled"
    assert job["operation_run_id"] == run_id
    assert job["attempt_count"] == 0
    assert calls["n"] == 0


def test_runtime_budget_does_not_call_engine(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}

    async def boom(self, composition_or_spec):  # noqa: ANN001
        calls["n"] += 1
        raise AssertionError("engine should not run")

    monkeypatch.setattr(FakeNeuralAudioGenerationModel, "render", boom)
    run_id = str(uuid.uuid4())
    remember_run_clock(run_id, started_at=time.perf_counter() - 5, wall_ms=1)
    response = client.post(
        "/neural-audio/renders",
        json={
            "composition": _composition(),
            "model_id": FAKE_NEURAL_AUDIO_MODEL_ID,
            "operation_run_id": run_id,
            "instructions": "SECRET-RENDER-PROMPT",
        },
    )
    assert response.status_code == 200, response.text
    job = response.json()
    assert job["status"] == "failed"
    assert job["error_code"] == "operation_runtime_budget"
    assert job["attempt_count"] == 0
    assert calls["n"] == 0


def test_other_run_id_still_renders(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    mark_run_cancelled(str(uuid.uuid4()))
    run_id = str(uuid.uuid4())
    with caplog.at_level(logging.INFO):
        response = client.post(
            "/neural-audio/renders",
            json={
                "composition": _composition(),
                "model_id": FAKE_NEURAL_AUDIO_MODEL_ID,
                "operation_run_id": run_id,
                "seed": 1,
                "instructions": "SECRET-RENDER-PROMPT",
            },
        )
    assert response.status_code == 200, response.text
    job = response.json()
    assert job["status"] == "complete"
    assert job["operation_run_id"] == run_id
    assert job["attempt_count"] == 1
    info_text = " ".join(record.getMessage() for record in caplog.records if record.levelno == logging.INFO)
    assert "SECRET-RENDER-PROMPT" not in info_text
    joined_extra = " ".join(str(getattr(record, "instructions", "")) for record in caplog.records)
    assert "SECRET-RENDER-PROMPT" not in joined_extra


def test_attempt_cap_stops_without_extra_engine_call(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}

    async def boom(self, composition_or_spec):  # noqa: ANN001
        calls["n"] += 1
        raise RuntimeError("engine-down")

    monkeypatch.setattr(FakeNeuralAudioGenerationModel, "render", boom)
    response = client.post(
        "/neural-audio/renders",
        json={
            "composition": _composition(),
            "model_id": FAKE_NEURAL_AUDIO_MODEL_ID,
            "operation_run_id": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 200, response.text
    job = response.json()
    assert job["status"] == "failed"
    assert job["error_code"] == "operation_render_attempt_budget"
    assert job["attempt_count"] == 2
    assert calls["n"] == 2


def test_late_engine_result_does_not_complete_cancelled_job(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = str(uuid.uuid4())
    original = FakeNeuralAudioGenerationModel.render

    async def late(self, composition_or_spec):  # noqa: ANN001
        mark_run_cancelled(run_id)
        return await original(self, composition_or_spec)

    monkeypatch.setattr(FakeNeuralAudioGenerationModel, "render", late)
    response = client.post(
        "/neural-audio/renders",
        json={
            "composition": _composition(),
            "model_id": FAKE_NEURAL_AUDIO_MODEL_ID,
            "operation_run_id": run_id,
            "seed": 2,
        },
    )
    assert response.status_code == 200, response.text
    job = response.json()
    assert job["status"] == "failed"
    assert job["error_code"] == "operation_cancelled"
    audio = client.get(f"/neural-audio/renders/{job['id']}/audio")
    assert audio.status_code != 200


def test_invalid_operation_run_id_is_422(client: TestClient) -> None:
    response = client.post(
        "/neural-audio/renders",
        json={
            "composition": _composition(),
            "model_id": FAKE_NEURAL_AUDIO_MODEL_ID,
            "operation_run_id": "not-a-uuid",
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "operation_run_id_invalid"


def test_omitted_run_id_stays_null(client: TestClient) -> None:
    response = client.post(
        "/neural-audio/renders",
        json={
            "composition": _composition(),
            "model_id": FAKE_NEURAL_AUDIO_MODEL_ID,
            "seed": 4,
        },
    )
    assert response.status_code == 200, response.text
    job = response.json()
    assert job["status"] == "complete"
    assert job["operation_run_id"] is None
    assert job["attempt_count"] == 1


def test_cancelled_stem_set_does_not_call_engine(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}

    async def boom(self, composition_or_spec):  # noqa: ANN001
        calls["n"] += 1
        raise AssertionError("stem engine should not run")

    monkeypatch.setattr(FakeNeuralAudioGenerationModel, "render_stems", boom)
    monkeypatch.setattr(FakeNeuralAudioGenerationModel, "render", boom)
    run_id = str(uuid.uuid4())
    mark_run_cancelled(run_id)
    response = client.post(
        "/neural-audio/stem-sets",
        json={
            "composition": _multi_stem_composition(),
            "engine": "neural",
            "model_id": FAKE_NEURAL_AUDIO_MODEL_ID,
            "stem_roles": ["piano", "bass"],
            "operation_run_id": run_id,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "failed"
    assert body["error_code"] == "operation_cancelled"
    assert body["operation_run_id"] == run_id
    assert body["attempt_count"] == 0
    assert calls["n"] == 0
