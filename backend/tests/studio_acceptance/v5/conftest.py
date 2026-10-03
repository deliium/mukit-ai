"""Fake-mode client with V5 flag overlay for the studio matrix."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ai_agents import registry as agent_registry
from app.ai_runtime import registry as model_registry
from app.db.connection import reset_database_initialization_cache
from app.main import app
from app.services.adaptive_playback_runtime import reset_default_playback_registry
from app.services.adaptive_runtime_continuation_runtime import (
    reset_default_continuation_registry,
)
from app.services.adaptive_runtime_continuation_service import reset_continuation_model
from app.services.adaptive_score_transition_pending import reset_default_registry
from tests.studio_acceptance.conftest import FAKE_BUNDLE

V5_FLAG_OVERLAY = {
    "ADAPTIVE_CONTINUOUS_ENABLED": "1",
    "RIGHTS_GOVERNANCE_ENABLED": "1",
    "MODEL_LAB_ENABLED": "1",
    "MODEL_LAB_FAKE_MODE": "1",
    "ENSEMBLE_ARBITRATION_ENABLED": "1",
    "ARDOUR_EXCHANGE_ENABLED": "1",
    "ARDOUR_COMPANION_ENABLED": "0",
    "AI_EXECUTION_NODES_ENABLED": "1",
    "AI_EXECUTION_NODES_FAKE": "1",
    "PREFERENCE_LEARNING_ENABLED": "1",
    "PERSONAL_COMPOSER_FAKE_MODE": "1",
}


@pytest.fixture
def v5_studio_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    for key, value in FAKE_BUNDLE.items():
        monkeypatch.setenv(key, value)
    for key, value in V5_FLAG_OVERLAY.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("VIDEO_ASSET_ROOT", str(tmp_path / "video_assets"))
    monkeypatch.setenv("ARDOUR_EXCHANGE_ROOT", str(tmp_path / "ardour_exchange"))
    monkeypatch.setenv("MODEL_LAB_ROOT", str(tmp_path / "model_lab"))
    monkeypatch.setenv("PERSONAL_COMPOSER_ROOT", str(tmp_path / "personal_composer"))
    monkeypatch.delenv("COLLABORATION_ENABLED", raising=False)
    monkeypatch.delenv("DATASET_ROOT", raising=False)
    monkeypatch.delenv("ADAPTIVE_ENGINE_CONTINUOUS_ENABLED", raising=False)
    reset_database_initialization_cache()
    reset_default_registry()
    reset_default_playback_registry()
    reset_default_continuation_registry()
    reset_continuation_model()
    agent_registry.clear_registry_for_tests()
    model_registry.clear_registry_for_tests()
    with TestClient(app) as client:
        yield client, db_path, tmp_path
    agent_registry.clear_registry_for_tests()
    model_registry.clear_registry_for_tests()
    reset_continuation_model()
    reset_default_continuation_registry()
    reset_default_playback_registry()
    reset_default_registry()
