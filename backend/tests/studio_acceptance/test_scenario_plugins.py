"""Scenario F: example plugins stay isolated from project create."""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ai_agents import registry as agent_registry
from app.ai_runtime import registry as model_registry
from app.composition_plan_schemas import parse_composition_plan
from app.db.connection import reset_database_initialization_cache
from app.main import app
from app.plugin_host.catalog import clear_plugins_for_tests
from app.plugin_host.invoke import PluginHost
from app.services.symbolic_composition_generate import generate_symbolic_composition
from tests.studio_acceptance.conftest import FAKE_BUNDLE
from tests.studio_acceptance.invariants import assert_playable_v2

_EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "plugins"
_PLAN = Path(__file__).resolve().parents[1] / "fixtures" / "composition_plan" / "valid_minimal.json"
_SAMPLE_ID = "plugin:sample_symbolic_generator"
_SENTINEL = "plugin-config-sentinel"


@pytest.fixture
def plugin_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "projects.db"
    plugin_root = tmp_path / "extra_plugins" / "net"
    plugin_root.mkdir(parents=True)
    manifest = {
        "schema_version": "plugin.manifest.v1",
        "id": "net_plugin",
        "name": "net_plugin",
        "version": "1.0.0",
        "api_compatibility": "1.0.0",
        "category": "analyzer",
        "capabilities": ["analyzer"],
        "dependencies": [],
        "entry": "plugin:register",
        "resources": ["network"],
    }
    (plugin_root / "plugin.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (plugin_root / "plugin.py").write_text(
        "class Analyzer:\n"
        "    def analyze(self, ctx, composition):\n"
        "        return {'note_count': 0}\n"
        "def register(ctx):\n"
        "    return Analyzer()\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    for key, value in FAKE_BUNDLE.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("COLLABORATION_ENABLED", raising=False)
    monkeypatch.delenv("RUN_LLM_SMOKE", raising=False)
    monkeypatch.setenv(
        "PLUGIN_PATHS",
        os.pathsep.join(
            [
                str(_EXAMPLES / "deterministic_analyzer"),
                str(_EXAMPLES / "sample_symbolic_generator"),
                str(tmp_path / "extra_plugins"),
            ]
        ),
    )
    reset_database_initialization_cache()
    agent_registry.clear_registry_for_tests()
    model_registry.clear_registry_for_tests()
    clear_plugins_for_tests()
    with TestClient(app) as client:
        yield client
    clear_plugins_for_tests()
    agent_registry.clear_registry_for_tests()
    model_registry.clear_registry_for_tests()


def test_plugins_enable_generate_and_disable(
    plugin_client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    installed = plugin_client.post("/plugins/sample_symbolic_generator/install")
    assert installed.status_code == 200, installed.text
    enabled = plugin_client.post("/plugins/sample_symbolic_generator/enable")
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["model_id"] == _SAMPLE_ID
    models = plugin_client.get("/ai/models")
    assert _SAMPLE_ID in {item["id"] for item in models.json()["models"]}

    plan = parse_composition_plan(json.loads(_PLAN.read_text(encoding="utf-8")))
    generated = generate_symbolic_composition(plan, model_id=_SAMPLE_ID, mood="bright", env={})
    body = generated.composition.model_dump(mode="json")
    assert_playable_v2(body)

    analyzer = plugin_client.post("/plugins/deterministic_analyzer/install")
    assert analyzer.status_code == 200, analyzer.text
    analyzer_on = plugin_client.post("/plugins/deterministic_analyzer/enable")
    assert analyzer_on.status_code == 200, analyzer_on.text
    composition = {"tracks": [{"events": [{"pitch": "C4"}]}]}
    original = json.dumps(composition)
    PluginHost().analyze("deterministic_analyzer", composition)
    assert json.dumps(composition) == original

    disabled = plugin_client.post("/plugins/sample_symbolic_generator/disable")
    assert disabled.status_code == 200, disabled.text
    after = plugin_client.get("/ai/models")
    assert _SAMPLE_ID not in {item["id"] for item in after.json()["models"]}
    created = plugin_client.post("/projects", json={"name": "After plugins"})
    assert created.status_code == 201, created.text
    ready = plugin_client.get("/ready")
    assert ready.status_code == 200

    net_install = plugin_client.post("/plugins/net_plugin/install")
    assert net_install.status_code == 200, net_install.text
    denied = plugin_client.post("/plugins/net_plugin/enable")
    assert denied.status_code == 409
    assert denied.json()["detail"]["code"] == "plugin_isolation_unavailable"
    assert "mukit_plugin_net_plugin" not in sys.modules
    assert _SENTINEL not in caplog.text
    assert original not in caplog.text
