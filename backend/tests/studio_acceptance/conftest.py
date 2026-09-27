"""Shared temp database and fake bundle for studio acceptance tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ai_agents import registry as agent_registry
from app.ai_runtime import registry as model_registry
from app.db.connection import reset_database_initialization_cache
from app.main import app

FAKE_BUNDLE = {
    "LLM_FAKE_MODE": "1",
    "AUDIO_FAKE_MODE": "1",
    "AUDIO_RECOVERY_FAKE_MODE": "1",
    "NEURAL_AUDIO_FAKE_MODE": "1",
    "MIX_ANALYSIS_FAKE_MODE": "1",
    "MIX_PLAN_FAKE_MODE": "1",
    "DEFAULT_LLM_PROVIDER": "fake",
}


@pytest.fixture
def studio_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    for key, value in FAKE_BUNDLE.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("COLLABORATION_ENABLED", raising=False)
    monkeypatch.delenv("PLUGIN_PATHS", raising=False)
    monkeypatch.setenv("NEURAL_AUDIO_ENGINE", "fake:neural-audio")
    monkeypatch.setenv("AI_OP_AUDIO_RENDER", "fake:neural-audio")
    monkeypatch.setenv("AUDIO_RECOVERY_ENGINE", "fake:audio-recovery")
    monkeypatch.setenv("AUDIO_RECOVERY_SEPARATION_ENGINE", "fake:stems")
    monkeypatch.delenv("RUN_LLM_SMOKE", raising=False)
    monkeypatch.delenv("DATASET_ROOT", raising=False)
    monkeypatch.setenv("AUDIO_RECOVERY_ASSET_ROOT", str(tmp_path / "audio_recovery_assets"))
    monkeypatch.setenv("NEURAL_AUDIO_RENDER_ROOT", str(tmp_path / "neural_audio_renders"))
    monkeypatch.setenv("MIX_ANALYSIS_ROOT", str(tmp_path / "mix_analysis_reports"))
    monkeypatch.setenv("MIX_PLAN_ROOT", str(tmp_path / "mix_plans"))
    reset_database_initialization_cache()
    agent_registry.clear_registry_for_tests()
    model_registry.clear_registry_for_tests()
    with TestClient(app) as client:
        yield client, db_path
    agent_registry.clear_registry_for_tests()
    model_registry.clear_registry_for_tests()
