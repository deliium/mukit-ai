"""Contract tests for the in-process plugin SDK and host."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ai_runtime import registry as registry_mod
from app.ai_runtime.capabilities import ModelCapability
from app.ai_runtime.routing import default_operation_routes
from app.ai_runtime.types import ModelDescriptor, ModelHealth
from app.composition_plan_schemas import parse_composition_plan
from app.db.connection import ensure_database
from app.main import app
from app.plugin_host.bridge import plugin_model_descriptors, reload_plugins
from app.plugin_host.catalog import clear_plugins_for_tests, get_record, list_records
from app.plugin_host.invoke import PluginHost
from app.plugin_sdk import PluginError, parse_manifest
from app.plugin_sdk.errors import PluginError as SdkPluginError
from app.services.plugin_lifecycle import PluginLifecycleError, enable_plugin, install_plugin
from app.services.symbolic_composition_generate import (
    SymbolicCompositionGenerateError,
    generate_symbolic_composition,
    resolve_symbolic_backend,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "plugins"
PLAN_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "composition_plan" / "valid_minimal.json"
SAMPLE_MODEL_ID = "plugin:sample_symbolic_generator"


@pytest.fixture(autouse=True)
def _isolate_plugins(monkeypatch, tmp_path):
    monkeypatch.setenv("PROJECT_DB_PATH", str(tmp_path / "projects.db"))
    monkeypatch.delenv("PLUGIN_PATHS", raising=False)
    monkeypatch.delenv("AI_OP_GENERATE_COMPOSER", raising=False)
    ensure_database()
    registry_mod.clear_registry_for_tests()
    clear_plugins_for_tests()
    yield
    clear_plugins_for_tests()
    registry_mod.clear_registry_for_tests()


def _plan():
    return parse_composition_plan(json.loads(PLAN_FIXTURE.read_text(encoding="utf-8")))


def _manifest(**overrides):
    document = {
        "schema_version": "plugin.manifest.v1",
        "id": "sample_plugin",
        "name": "Sample",
        "version": "1.0.0",
        "api_compatibility": "1.0.0",
        "category": "analyzer",
        "capabilities": ["analyzer"],
        "dependencies": [],
        "entry": "plugin:register",
    }
    document.update(overrides)
    return document


def _write_plugin(
    directory: Path,
    *,
    plugin_id: str,
    category: str,
    capabilities: list[str],
    body: str,
    version: str = "1.0.0",
    api_compatibility: str = "1.0.0",
    dependencies: list[dict] | None = None,
    configuration_schema: dict | None = None,
    config: dict | None = None,
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    manifest = _manifest(
        id=plugin_id,
        name=plugin_id,
        version=version,
        api_compatibility=api_compatibility,
        category=category,
        capabilities=capabilities,
        dependencies=dependencies or [],
    )
    if configuration_schema is not None:
        manifest["configuration_schema"] = configuration_schema
    (directory / "plugin.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (directory / "plugin.py").write_text(body, encoding="utf-8")
    if config is not None:
        (directory / "config.json").write_text(json.dumps(config), encoding="utf-8")


def _load(monkeypatch, path: Path):
    monkeypatch.setenv("PLUGIN_PATHS", str(path))
    return reload_plugins()


def _install_and_enable(plugin_id: str):
    install_plugin(plugin_id)
    return enable_plugin(plugin_id)


def test_manifest_accept_and_reject():
    manifest = parse_manifest(_manifest(), source_name="/tmp/hidden/plugin.manifest.json")
    assert manifest.id == "sample_plugin"
    assert manifest.entry == "plugin:register"

    with pytest.raises(SdkPluginError) as missing:
        broken = _manifest()
        del broken["name"]
        parse_manifest(broken)
    assert missing.value.code == "plugin_manifest_invalid"

    with pytest.raises(SdkPluginError) as semver:
        parse_manifest(_manifest(version="1.0"))
    assert semver.value.code == "plugin_manifest_invalid"

    with pytest.raises(SdkPluginError) as category:
        parse_manifest(_manifest(category="daw_plugin"))
    assert category.value.code == "plugin_manifest_invalid"

    with pytest.raises(SdkPluginError) as capability:
        parse_manifest(_manifest(category="symbolic_composer", capabilities=["analyzer"]))
    assert capability.value.code == "plugin_manifest_invalid"


def test_incompatible_api_leaves_builtin_models(monkeypatch, tmp_path):
    _write_plugin(
        tmp_path / "old",
        plugin_id="old_api",
        category="analyzer",
        capabilities=["analyzer"],
        api_compatibility="2.0.0",
        body="def register(ctx):\n    return object()\n",
    )
    _load(monkeypatch, tmp_path)
    record = get_record("old_api")
    assert record is not None
    assert record.status == "incompatible"
    assert record.code == "plugin_api_incompatible"
    registry_mod.reload_registry({})
    ids = {model.id for model in registry_mod.list_models()}
    assert "local:embedding-stub" in ids
    assert "plugin:old_api" not in ids


def test_config_required_missing_and_valid_value(monkeypatch, tmp_path):
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["min_notes"],
        "properties": {"min_notes": {"type": "integer", "minimum": 0}},
    }
    _write_plugin(
        tmp_path / "needs_config",
        plugin_id="needs_config",
        category="analyzer",
        capabilities=["analyzer"],
        configuration_schema=schema,
        body=(
            "class Analyzer:\n"
            "    def analyze(self, ctx, composition):\n"
            "        return {'min_notes': ctx.config['min_notes']}\n"
            "def register(ctx):\n"
            "    return Analyzer()\n"
        ),
    )
    _load(monkeypatch, tmp_path / "needs_config")
    discovered = get_record("needs_config")
    assert discovered is not None
    assert discovered.status == "discovered"
    assert discovered.code is None
    installed = _install_and_enable("needs_config")
    assert installed.status == "failed"
    assert installed.code == "plugin_config_invalid"

    clear_plugins_for_tests()
    _write_plugin(
        tmp_path / "ok_config",
        plugin_id="ok_config",
        category="analyzer",
        capabilities=["analyzer"],
        configuration_schema=schema,
        config={"min_notes": 4},
        body=(
            "class Analyzer:\n"
            "    def analyze(self, ctx, composition):\n"
            "        return {'min_notes': ctx.config['min_notes']}\n"
            "def register(ctx):\n"
            "    return Analyzer()\n"
        ),
    )
    _load(monkeypatch, tmp_path / "ok_config")
    _install_and_enable("ok_config")
    fragment = PluginHost().analyze("ok_config", {"tracks": []})
    assert fragment["schema_version"] == "plugin.analysis.fragment.v1"
    assert fragment["data"]["min_notes"] == 4


def test_secret_config_absent_from_logs(monkeypatch, tmp_path, caplog):
    secret = "UNIQUE_SECRET_VALUE_991"
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"label": {"type": "string"}},
    }
    _write_plugin(
        tmp_path / "secretive",
        plugin_id="secretive",
        category="analyzer",
        capabilities=["analyzer"],
        configuration_schema=schema,
        config={"label": secret},
        body=(
            "class Analyzer:\n"
            "    def analyze(self, ctx, composition):\n"
            "        return {'note_count': 0}\n"
            "def register(ctx):\n"
            "    ctx.logger.warning('config loaded', extra={'api_key': ctx.config.get('label')})\n"
            "    return Analyzer()\n"
        ),
    )
    caplog.set_level(logging.DEBUG)
    _load(monkeypatch, tmp_path / "secretive")
    _install_and_enable("secretive")
    blob = caplog.text + "".join(str(getattr(record, "api_key", "")) for record in caplog.records)
    assert secret not in blob
    assert any(getattr(record, "api_key", None) == "[redacted]" for record in caplog.records)


def test_forbidden_import_is_rejected(monkeypatch, tmp_path, caplog):
    _write_plugin(
        tmp_path / "leaky",
        plugin_id="leaky",
        category="analyzer",
        capabilities=["analyzer"],
        body="import app.services.fake_llm\n\ndef register(ctx):\n    return object()\n",
    )
    caplog.set_level(logging.WARNING)
    _load(monkeypatch, tmp_path / "leaky")
    enabled = _install_and_enable("leaky")
    record = get_record("leaky")
    assert record is not None
    assert enabled.status == "failed"
    assert record.status == "failed"
    assert record.code == "plugin_forbidden_import"
    assert record.instance is None
    assert "plugin_forbidden_import" in caplog.text
    assert "mukit_plugin_leaky" not in sys.modules


def test_duplicate_missing_version_and_cycle(monkeypatch, tmp_path):
    parent = tmp_path / "plugins"
    shared = (
        "class Analyzer:\n"
        "    def analyze(self, ctx, composition):\n"
        "        return {'note_count': 0}\n"
        "def register(ctx):\n"
        "    return Analyzer()\n"
    )
    _write_plugin(parent / "aaa", plugin_id="shared_id", category="analyzer", capabilities=["analyzer"], body=shared)
    _write_plugin(parent / "zzz", plugin_id="shared_id", category="analyzer", capabilities=["analyzer"], body=shared)
    _write_plugin(
        parent / "needs_missing",
        plugin_id="needs_missing",
        category="analyzer",
        capabilities=["analyzer"],
        dependencies=[{"id": "not_installed"}],
        body=shared,
    )
    _write_plugin(
        parent / "helper",
        plugin_id="helper_plugin",
        category="analyzer",
        capabilities=["analyzer"],
        version="1.0.0",
        body=shared,
    )
    _write_plugin(
        parent / "needs_version",
        plugin_id="needs_version",
        category="analyzer",
        capabilities=["analyzer"],
        dependencies=[{"id": "helper_plugin", "version": "9.9.9"}],
        body=shared,
    )
    _write_plugin(
        parent / "cycle_left",
        plugin_id="cycle_left",
        category="analyzer",
        capabilities=["analyzer"],
        dependencies=[{"id": "cycle_right"}],
        body=shared,
    )
    _write_plugin(
        parent / "cycle_right",
        plugin_id="cycle_right",
        category="analyzer",
        capabilities=["analyzer"],
        dependencies=[{"id": "cycle_left"}],
        body=shared,
    )
    _load(monkeypatch, parent)
    assert get_record("shared_id").status == "discovered"
    duplicates = [record for record in list_records() if record.code == "plugin_duplicate_id"]
    assert len(duplicates) == 1
    assert get_record("needs_missing").status == "discovered"
    assert get_record("needs_version").status == "discovered"
    assert get_record("helper_plugin").status == "discovered"
    assert get_record("cycle_left").status == "discovered"
    assert get_record("cycle_right").status == "discovered"
    assert _install_and_enable("shared_id").status == "enabled"
    assert get_record("shared_id").status == "enabled"
    install_plugin("needs_missing")
    with pytest.raises(PluginLifecycleError) as missing_dep:
        enable_plugin("needs_missing")
    assert missing_dep.value.code == "plugin_dependency_missing"
    assert get_record("needs_missing").status == "installed"
    assert _install_and_enable("helper_plugin").status == "enabled"
    install_plugin("needs_version")
    with pytest.raises(PluginLifecycleError) as version_dep:
        enable_plugin("needs_version")
    assert version_dep.value.code == "plugin_dependency_missing"
    install_plugin("cycle_left")
    with pytest.raises(PluginLifecycleError) as cycle:
        enable_plugin("cycle_left")
    assert cycle.value.code == "plugin_dependency_cycle"
    assert get_record("cycle_left").status == "installed"
    assert get_record("cycle_right").status == "discovered"


def test_symlink_escape_is_not_loaded(monkeypatch, tmp_path):
    outside = tmp_path / "outside"
    parent = tmp_path / "parent"
    parent.mkdir()
    _write_plugin(
        outside,
        plugin_id="escaped_plugin",
        category="analyzer",
        capabilities=["analyzer"],
        body="def register(ctx):\n    return object()\n",
    )
    (parent / "link").symlink_to(outside, target_is_directory=True)
    _load(monkeypatch, parent)
    assert get_record("escaped_plugin") is None
    assert all(record.status != "enabled" for record in list_records())


def test_empty_plugin_paths_leaves_symbolic_backend(monkeypatch):
    monkeypatch.delenv("PLUGIN_PATHS", raising=False)
    reload_plugins({})
    assert list_records() == []
    before = resolve_symbolic_backend(env={})
    reload_plugins({})
    assert resolve_symbolic_backend(env={}) == before == "fake"


def test_example_analyzer_does_not_mutate(monkeypatch):
    _load(monkeypatch, EXAMPLES / "deterministic_analyzer")
    _install_and_enable("deterministic_analyzer")
    composition = {
        "tracks": [
            {"events": [{"type": "note", "pitch": "C4"}, {"type": "rest"}]},
            {"events": [{"type": "note", "pitch": "E4"}]},
        ]
    }
    original = json.dumps(composition)
    fragment = PluginHost().analyze("deterministic_analyzer", composition)
    assert json.dumps(composition) == original
    assert fragment["data"]["note_count"] == 2
    assert fragment["data"]["track_count"] == 2


def test_example_generator_is_listed_and_not_the_silent_default(monkeypatch):
    _load(monkeypatch, EXAMPLES / "sample_symbolic_generator")
    _install_and_enable("sample_symbolic_generator")
    registry_mod.reload_registry({})
    ids = [model.id for model in registry_mod.list_models(capability=ModelCapability.SYMBOLIC_COMPOSER)]
    assert SAMPLE_MODEL_ID in ids
    module = sys.modules["mukit_plugin_sample_symbolic_generator"]
    calls = module.REGISTER_CALLS
    registry_mod.reload_registry({})
    assert module.REGISTER_CALLS == calls
    explicit = (
        ModelDescriptor(
            id="fake:test-only",
            display_name="test",
            provider="fake",
            runtime="fake",
            primary_capability=ModelCapability.LANGUAGE_PLANNER,
            locality="local",
            model_version="0",
            supported_operations=(),
            status="ready",
            health=ModelHealth(status="ready", credentials_present=False),
        ),
    )
    registry_mod.reload_registry(descriptors=explicit)
    assert SAMPLE_MODEL_ID not in {model.id for model in registry_mod.list_models()}
    assert plugin_model_descriptors()
    registry_mod.reload_registry({})
    routes = default_operation_routes({})
    assert routes.get("generate_composer") != SAMPLE_MODEL_ID
    result = generate_symbolic_composition(_plan(), env={})
    assert result.backend == "fake"


def test_explicit_plugin_composer_validates_c4(monkeypatch):
    _load(monkeypatch, EXAMPLES / "sample_symbolic_generator")
    _install_and_enable("sample_symbolic_generator")
    registry_mod.reload_registry({"AI_OP_GENERATE_COMPOSER": SAMPLE_MODEL_ID})
    first = generate_symbolic_composition(_plan(), model_id=SAMPLE_MODEL_ID, mood="bright", env={})
    second = generate_symbolic_composition(_plan(), model_id=SAMPLE_MODEL_ID, mood="dark", env={})
    first_notes = [event.pitch for track in first.composition.tracks for event in track.events]
    second_notes = [event.pitch for track in second.composition.tracks for event in track.events]
    assert first.backend == "plugin"
    assert first.model_id == SAMPLE_MODEL_ID
    assert first_notes == ["C4"]
    assert second_notes == first_notes


def test_empty_plugin_composition_is_invalid(monkeypatch, tmp_path):
    _write_plugin(
        tmp_path / "empty_composer",
        plugin_id="empty_composer",
        category="symbolic_composer",
        capabilities=["symbolic_composer"],
        body=(
            "class Composer:\n"
            "    def compose(self, ctx, request):\n"
            "        return {}\n"
            "def register(ctx):\n"
            "    return Composer()\n"
        ),
    )
    _load(monkeypatch, tmp_path / "empty_composer")
    _install_and_enable("empty_composer")
    registry_mod.reload_registry({})
    with pytest.raises(SymbolicCompositionGenerateError) as exc:
        generate_symbolic_composition(_plan(), model_id="plugin:empty_composer", env={})
    assert exc.value.code == "plugin_output_invalid"
    assert get_record("empty_composer").status == "enabled"


def test_agent_mutation_and_export_cap(monkeypatch, tmp_path):
    _write_plugin(
        tmp_path / "mutating_agent",
        plugin_id="mutating_agent",
        category="music_agent",
        capabilities=["music_agent"],
        body=(
            "class Agent:\n"
            "    def run(self, ctx, request):\n"
            "        return {'mutates_composition': True}\n"
            "def register(ctx):\n"
            "    return Agent()\n"
        ),
    )
    _write_plugin(
        tmp_path / "fat_export",
        plugin_id="fat_export",
        category="export_format",
        capabilities=["export_format"],
        body=(
            "class Exporter:\n"
            "    def export(self, ctx, composition):\n"
            "        return (b'x' * 32, 'application/octet-stream')\n"
            "def register(ctx):\n"
            "    return Exporter()\n"
        ),
    )
    monkeypatch.setenv("PLUGIN_MAX_EXPORT_BYTES", "8")
    monkeypatch.setenv("PLUGIN_PATHS", str(tmp_path))
    reload_plugins()
    _install_and_enable("mutating_agent")
    _install_and_enable("fat_export")
    with pytest.raises(PluginError) as agent_error:
        PluginHost().run_agent("mutating_agent", {})
    assert agent_error.value.code == "plugin_output_invalid"
    with pytest.raises(PluginError) as export_error:
        PluginHost().export("fat_export", {"tracks": []})
    assert export_error.value.code == "plugin_output_invalid"


def test_http_discovery_and_reload(monkeypatch, tmp_path):
    monkeypatch.setenv("PLUGIN_PATHS", str(EXAMPLES))
    with TestClient(app) as client:
        listed = client.get("/plugins")
        assert listed.status_code == 200
        ids = {item["id"] for item in listed.json()["plugins"]}
        assert "deterministic_analyzer" in ids
        assert "sample_symbolic_generator" in ids
        detail = client.get("/plugins/sample_symbolic_generator")
        assert detail.status_code == 200
        assert detail.json()["status"] == "discovered"
        assert detail.json()["model_id"] is None
        installed = client.post("/plugins/sample_symbolic_generator/install")
        assert installed.status_code == 200
        enabled = client.post("/plugins/sample_symbolic_generator/enable")
        assert enabled.status_code == 200
        assert enabled.json()["status"] == "enabled"
        assert enabled.json()["model_id"] == SAMPLE_MODEL_ID
        missing = client.get("/plugins/plugin:sample_symbolic_generator")
        assert missing.status_code == 404
        assert missing.json()["detail"]["code"] == "plugin_not_found"
        models = client.get("/ai/models", params={"capability": "symbolic_composer"})
        assert models.status_code == 200
        model_ids = {item["id"] for item in models.json()["models"]}
        assert SAMPLE_MODEL_ID in model_ids

    parent = tmp_path / "reload_root"
    parent.mkdir()
    monkeypatch.setenv("PLUGIN_PATHS", str(parent))
    with TestClient(app) as client:
        created = parent / "fresh_plugin"
        _write_plugin(
            created,
            plugin_id="fresh_plugin",
            category="analyzer",
            capabilities=["analyzer"],
            body=(
                "class Analyzer:\n"
                "    def analyze(self, ctx, composition):\n"
                "        return {'note_count': 1}\n"
                "def register(ctx):\n"
                "    return Analyzer()\n"
            ),
        )
        reloaded = client.post("/plugins/reload")
        assert reloaded.status_code == 200
        assert "fresh_plugin" in {item["id"] for item in reloaded.json()["plugins"]}

    monkeypatch.setenv("PLUGIN_RELOAD_ENABLED", "0")
    monkeypatch.setenv("PLUGIN_PATHS", str(EXAMPLES))
    with TestClient(app) as client:
        denied = client.post("/plugins/reload")
        assert denied.status_code == 403
        assert denied.json()["detail"]["code"] == "plugin_reload_disabled"
