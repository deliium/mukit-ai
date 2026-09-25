"""Security matrix for plugin discovery, install, and enable gates."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ai_runtime import registry as registry_mod
from app.db.connection import ensure_database
from app.main import app
from app.plugin_host.catalog import clear_plugins_for_tests, get_record
from app.plugin_host.invoke import PluginHost
from app.plugin_sdk.errors import PluginError
from app.services.plugin_installation_store import get_installation
from app.services.plugin_lifecycle import derive_lifecycle
from app.services.symbolic_composition_generate import generate_symbolic_composition

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "plugins"
SAMPLE_MODEL_ID = "plugin:sample_symbolic_generator"
ANALYZER_BODY = (
    "class Analyzer:\n"
    "    def analyze(self, ctx, composition):\n"
    "        return {'note_count': 0}\n"
    "def register(ctx):\n"
    "    return Analyzer()\n"
)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("PROJECT_DB_PATH", str(tmp_path / "projects.db"))
    monkeypatch.delenv("PLUGIN_PATHS", raising=False)
    monkeypatch.delenv("AI_OP_GENERATE_COMPOSER", raising=False)
    ensure_database()
    registry_mod.clear_registry_for_tests()
    clear_plugins_for_tests()
    yield
    clear_plugins_for_tests()
    registry_mod.clear_registry_for_tests()


def test_derive_lifecycle_without_loading_plugin_modules():
    assert derive_lifecycle(
        scan_status="discovered",
        scan_code=None,
        resources=(),
        manifest_version="1.0.0",
        desired_state=None,
        installed_version=None,
    ) == ("discovered", None)
    assert derive_lifecycle(
        scan_status="discovered",
        scan_code=None,
        resources=("network",),
        manifest_version="1.0.0",
        desired_state="enabled",
        installed_version="1.0.0",
    ) == ("failed", "plugin_isolation_unavailable")
    assert derive_lifecycle(
        scan_status="incompatible",
        scan_code="plugin_api_incompatible",
        resources=(),
        manifest_version="1.0.0",
        desired_state="enabled",
        installed_version="1.0.0",
    ) == ("incompatible", "plugin_api_incompatible")
    assert "mukit_plugin_sample_symbolic_generator" not in sys.modules


def _write_plugin(
    directory: Path,
    *,
    plugin_id: str,
    body: str,
    version: str = "1.0.0",
    api_compatibility: str = "1.0.0",
    resources: list[str] | None = None,
    category: str = "analyzer",
    capabilities: list[str] | None = None,
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": "plugin.manifest.v1",
        "id": plugin_id,
        "name": plugin_id,
        "version": version,
        "api_compatibility": api_compatibility,
        "category": category,
        "capabilities": capabilities or ["analyzer"],
        "dependencies": [],
        "entry": "plugin:register",
    }
    if resources is not None:
        manifest["resources"] = resources
    (directory / "plugin.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (directory / "plugin.py").write_text(body, encoding="utf-8")


def test_discovery_does_not_import_or_publish_a_model(monkeypatch):
    monkeypatch.setenv("PLUGIN_PATHS", str(EXAMPLES))
    with TestClient(app) as client:
        listed = client.get("/plugins")
        assert listed.status_code == 200
        by_id = {item["id"]: item for item in listed.json()["plugins"]}
        sample = by_id["sample_symbolic_generator"]
        assert sample["status"] == "discovered"
        assert sample["model_id"] is None
        assert sample["installed_version"] is None
        assert "mukit_plugin_sample_symbolic_generator" not in sys.modules
        models = client.get("/ai/models")
        ids = {item["id"] for item in models.json()["models"]}
        assert SAMPLE_MODEL_ID not in ids


def test_enable_before_install_does_not_import(monkeypatch, tmp_path):
    plugin_root = tmp_path / "plugins" / "plain"
    _write_plugin(plugin_root, plugin_id="plain_plugin", body=ANALYZER_BODY)
    monkeypatch.setenv("PLUGIN_PATHS", str(tmp_path / "plugins"))
    with TestClient(app) as client:
        denied = client.post("/plugins/plain_plugin/enable")
        assert denied.status_code == 409
        assert denied.json()["detail"]["code"] == "plugin_not_installed"
        assert "mukit_plugin_plain_plugin" not in sys.modules


def test_incompatible_api_still_serves_projects(monkeypatch, tmp_path):
    _write_plugin(
        tmp_path / "plugins" / "old",
        plugin_id="old_api",
        api_compatibility="2.0.0",
        body=ANALYZER_BODY,
    )
    monkeypatch.setenv("PLUGIN_PATHS", str(tmp_path / "plugins"))
    with TestClient(app) as client:
        listed = client.get("/plugins/old_api")
        assert listed.status_code == 200
        assert listed.json()["status"] == "incompatible"
        denied = client.post("/plugins/old_api/enable")
        assert denied.status_code == 409
        assert denied.json()["detail"]["code"] == "plugin_api_incompatible"
        assert "mukit_plugin_old_api" not in sys.modules
        assert client.get("/health").status_code == 200
        created = client.post("/projects", json={"name": "Still opens"})
        assert created.status_code == 201
        project_id = created.json()["id"]
        opened = client.get(f"/projects/{project_id}")
        assert opened.status_code == 200


def test_register_system_exit_is_failed_and_process_stays_up(monkeypatch, tmp_path, caplog):
    _write_plugin(
        tmp_path / "plugins" / "boom",
        plugin_id="boom_plugin",
        body="def register(ctx):\n    raise SystemExit(3)\n",
    )
    monkeypatch.setenv("PLUGIN_PATHS", str(tmp_path / "plugins"))
    caplog.set_level(logging.WARNING)
    with TestClient(app) as client:
        assert client.post("/plugins/boom_plugin/install").status_code == 200
        enabled = client.post("/plugins/boom_plugin/enable")
        assert enabled.status_code == 200
        body = enabled.json()
        assert body["status"] == "failed"
        assert body["code"] == "plugin_register_failed"
        assert body["health"]["status"] == "unhealthy"
        assert "mukit_plugin_boom_plugin" not in sys.modules
        assert client.get("/health").status_code == 200
        created = client.post("/projects", json={"name": "After crash"})
        assert created.status_code == 201
        assert client.get(f"/projects/{created.json()['id']}").status_code == 200
    assert any(getattr(record, "code", None) == "plugin_register_failed" for record in caplog.records)


def test_one_register_failure_does_not_block_a_second_plugin(monkeypatch, tmp_path):
    parent = tmp_path / "plugins"
    _write_plugin(
        parent / "bad",
        plugin_id="bad_plugin",
        body="def register(ctx):\n    raise RuntimeError('nope')\n",
    )
    _write_plugin(parent / "good", plugin_id="good_plugin", body=ANALYZER_BODY)
    monkeypatch.setenv("PLUGIN_PATHS", str(parent))
    with TestClient(app) as client:
        client.post("/plugins/bad_plugin/install")
        failed = client.post("/plugins/bad_plugin/enable")
        assert failed.status_code == 200
        assert failed.json()["status"] == "failed"
        client.post("/plugins/good_plugin/install")
        enabled = client.post("/plugins/good_plugin/enable")
        assert enabled.status_code == 200
        assert enabled.json()["status"] == "enabled"


def test_declared_network_resource_is_not_imported(monkeypatch, tmp_path, caplog):
    _write_plugin(
        tmp_path / "plugins" / "net",
        plugin_id="net_plugin",
        resources=["network"],
        body=ANALYZER_BODY,
    )
    monkeypatch.setenv("PLUGIN_PATHS", str(tmp_path / "plugins"))
    caplog.set_level(logging.WARNING)
    with TestClient(app) as client:
        assert client.post("/plugins/net_plugin/install").status_code == 200
        denied = client.post("/plugins/net_plugin/enable")
        assert denied.status_code == 409
        assert denied.json()["detail"]["code"] == "plugin_isolation_unavailable"
        current = client.get("/plugins/net_plugin").json()
        assert current["status"] == "installed"
        assert current["resources"] == ["network"]
        assert "mukit_plugin_net_plugin" not in sys.modules
    row = get_installation("net_plugin")
    assert row is not None
    assert row.desired_state == "installed"
    assert any(getattr(record, "code", None) == "plugin_isolation_unavailable" for record in caplog.records)


def test_unknown_resource_fails_discovery(monkeypatch, tmp_path):
    _write_plugin(
        tmp_path / "plugins" / "shell",
        plugin_id="shell_plugin",
        resources=["shell"],
        body=ANALYZER_BODY,
    )
    monkeypatch.setenv("PLUGIN_PATHS", str(tmp_path / "plugins"))
    with TestClient(app) as client:
        listed = client.get("/plugins/shell_plugin")
        assert listed.status_code == 200
        assert listed.json()["status"] == "failed"
        assert listed.json()["code"] == "plugin_manifest_invalid"
        assert "mukit_plugin_shell_plugin" not in sys.modules


def test_version_mismatch_blocks_import_until_reinstall(monkeypatch, tmp_path):
    root = tmp_path / "plugins" / "versioned"
    _write_plugin(root, plugin_id="versioned", version="1.0.0", body=ANALYZER_BODY)
    monkeypatch.setenv("PLUGIN_PATHS", str(tmp_path / "plugins"))
    with TestClient(app) as client:
        client.post("/plugins/versioned/install")
        assert client.post("/plugins/versioned/enable").json()["status"] == "enabled"
        manifest_path = root / "plugin.manifest.json"
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
        document["version"] = "1.0.1"
        manifest_path.write_text(json.dumps(document), encoding="utf-8")
        reloaded = client.post("/plugins/reload")
        assert reloaded.status_code == 200
        current = next(item for item in reloaded.json()["plugins"] if item["id"] == "versioned")
        assert current["status"] == "failed"
        assert current["code"] == "plugin_version_mismatch"
        assert "mukit_plugin_versioned" not in sys.modules
        installed = client.post("/plugins/versioned/install")
        assert installed.status_code == 200
        assert installed.json()["status"] == "installed"
        assert installed.json()["installed_version"] == "1.0.1"
        assert "mukit_plugin_versioned" not in sys.modules


def test_invoke_system_exit_stays_enabled_and_unhealthy(monkeypatch, tmp_path, caplog):
    body = (
        "class Analyzer:\n"
        "    def analyze(self, ctx, composition):\n"
        "        raise SystemExit(2)\n"
        "def register(ctx):\n"
        "    return Analyzer()\n"
    )
    _write_plugin(tmp_path / "plugins" / "exit_call", plugin_id="exit_call", body=body)
    monkeypatch.setenv("PLUGIN_PATHS", str(tmp_path / "plugins"))
    caplog.set_level(logging.WARNING)
    with TestClient(app) as client:
        client.post("/plugins/exit_call/install")
        enabled = client.post("/plugins/exit_call/enable")
        assert enabled.json()["status"] == "enabled"
        with pytest.raises(PluginError) as exc:
            PluginHost().analyze("exit_call", {"tracks": []})
        assert exc.value.code == "plugin_output_invalid"
        record = get_record("exit_call")
        assert record is not None
        assert record.status == "enabled"
        assert record.health_status == "unhealthy"
        assert record.health_code == "plugin_output_invalid"
        assert record.invocation_failure_count == 1
        detail = client.get("/plugins/exit_call").json()
        assert detail["status"] == "enabled"
        assert detail["health"]["status"] == "unhealthy"
        assert client.get("/health").status_code == 200
    assert "plugin_output_invalid" in caplog.text or "plugin crashed" in caplog.text


def test_startup_reconcile_of_broken_enabled_plugin_keeps_ready(monkeypatch, tmp_path, caplog):
    root = tmp_path / "plugins" / "later_boom"
    _write_plugin(root, plugin_id="later_boom", body=ANALYZER_BODY)
    monkeypatch.setenv("PLUGIN_PATHS", str(tmp_path / "plugins"))
    with TestClient(app) as client:
        client.post("/plugins/later_boom/install")
        assert client.post("/plugins/later_boom/enable").json()["status"] == "enabled"
    (root / "plugin.py").write_text("def register(ctx):\n    raise SystemExit(5)\n", encoding="utf-8")
    caplog.set_level(logging.WARNING)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        detail = client.get("/plugins/later_boom").json()
        assert detail["status"] == "failed"
        assert detail["code"] == "plugin_register_failed"
        created = client.post("/projects", json={"name": "Reconcile"})
        assert created.status_code == 201
        assert client.get(f"/projects/{created.json()['id']}").status_code == 200
    assert any(getattr(record, "code", None) == "plugin_register_failed" for record in caplog.records)


def test_disable_drops_module_and_invoke(monkeypatch):
    monkeypatch.setenv("PLUGIN_PATHS", str(EXAMPLES))
    with TestClient(app) as client:
        client.post("/plugins/sample_symbolic_generator/install")
        enabled = client.post("/plugins/sample_symbolic_generator/enable")
        assert enabled.json()["model_id"] == SAMPLE_MODEL_ID
        assert "mukit_plugin_sample_symbolic_generator" in sys.modules
        disabled = client.post("/plugins/sample_symbolic_generator/disable")
        assert disabled.status_code == 200
        assert disabled.json()["status"] == "disabled"
        assert "mukit_plugin_sample_symbolic_generator" not in sys.modules
        models = client.get("/ai/models")
        ids = {item["id"] for item in models.json()["models"]}
        assert SAMPLE_MODEL_ID not in ids
        with pytest.raises(PluginError) as exc:
            PluginHost().compose(SAMPLE_MODEL_ID, {})
        assert exc.value.code == "plugin_not_enabled"


def test_config_values_are_not_logged_or_returned(monkeypatch, tmp_path, caplog):
    marker = "config-marker-zelda"
    root = tmp_path / "plugins" / "configured"
    _write_plugin(root, plugin_id="configured", body=ANALYZER_BODY)
    manifest = json.loads((root / "plugin.manifest.json").read_text(encoding="utf-8"))
    manifest["configuration_schema"] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"label": {"type": "string"}},
    }
    (root / "plugin.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setenv("PLUGIN_PATHS", str(tmp_path / "plugins"))
    caplog.set_level(logging.DEBUG)
    with TestClient(app) as client:
        client.post("/plugins/configured/install")
        saved = client.put("/plugins/configured/config", json={"label": marker})
        assert saved.status_code == 200
        assert saved.json()["config_present"] is True
        assert marker not in json.dumps(saved.json())
        detail = client.get("/plugins/configured")
        payload = detail.json()
        assert "label" not in payload
        assert marker not in json.dumps(payload)
    assert marker not in caplog.text


def test_enabled_plugin_is_not_the_implicit_composer(monkeypatch):
    plan_path = Path(__file__).resolve().parent / "fixtures" / "composition_plan" / "valid_minimal.json"
    from app.composition_plan_schemas import parse_composition_plan

    plan = parse_composition_plan(json.loads(plan_path.read_text(encoding="utf-8")))
    monkeypatch.setenv("PLUGIN_PATHS", str(EXAMPLES))
    with TestClient(app) as client:
        client.post("/plugins/sample_symbolic_generator/install")
        assert client.post("/plugins/sample_symbolic_generator/enable").json()["status"] == "enabled"
    result = generate_symbolic_composition(plan, env={})
    assert result.backend == "fake"
    assert result.model_id != SAMPLE_MODEL_ID


def test_empty_plugin_paths_leave_builtins(monkeypatch):
    monkeypatch.delenv("PLUGIN_PATHS", raising=False)
    registry_mod.reload_registry({})
    before = {model.id for model in registry_mod.list_models()}
    with TestClient(app) as client:
        listed = client.get("/plugins")
        assert listed.status_code == 200
        assert listed.json()["plugins"] == []
    registry_mod.reload_registry({})
    after = {model.id for model in registry_mod.list_models()}
    assert before == after
    assert SAMPLE_MODEL_ID not in after
    assert any(model_id.startswith("local:") or model_id.startswith("fake:") for model_id in after)
