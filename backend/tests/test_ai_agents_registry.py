"""Tests for agent registry, binding, and forbidden-import gate."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ai_agents import registry as agent_registry
from app.ai_agents.errors import AgentNotFoundError
from app.ai_agents.schemas import KNOWN_AGENT_IDS, AgentStatus
from app.ai_runtime import registry as model_registry


@pytest.fixture(autouse=True)
def _clear_registries():
    agent_registry.clear_registry_for_tests()
    model_registry.clear_registry_for_tests()
    yield
    agent_registry.clear_registry_for_tests()
    model_registry.clear_registry_for_tests()


def test_bootstrap_registers_nine_agents(monkeypatch):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    env = dict(**{k: v for k, v in __import__("os").environ.items()})
    model_registry.reload_registry(env)
    agent_registry.reload_agent_registry(env)

    ids = agent_registry.list_agent_ids()
    assert set(ids) == set(KNOWN_AGENT_IDS)
    assert len(ids) == 9
    for agent_id in KNOWN_AGENT_IDS:
        desc = agent_registry.get_descriptor(agent_id)
        assert desc.mutates_composition is False
        assert desc.status in {AgentStatus.READY, AgentStatus.DEGRADED}
        assert desc.bound_model_id


def test_get_unknown_agent_raises():
    monkeypatch_env = {"LLM_FAKE_MODE": "1"}
    model_registry.reload_registry(monkeypatch_env)
    agent_registry.reload_agent_registry(monkeypatch_env)
    with pytest.raises(AgentNotFoundError) as exc:
        agent_registry.get_agent("not_an_agent")
    assert exc.value.code == "agent_not_found"


def test_list_descriptors_filter_by_capability(monkeypatch):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    env = dict(**{k: v for k, v in __import__("os").environ.items()})
    model_registry.reload_registry(env)
    agent_registry.reload_agent_registry(env)
    items = agent_registry.list_descriptors(capability="harmony")
    assert len(items) == 1
    assert items[0].id == "harmony"


def test_no_project_db_imports_in_ai_agents_package():
    package = Path(__file__).resolve().parents[1] / "app" / "ai_agents"
    forbidden_imports = (
        "from app.services.project_store",
        "import project_store",
        "from app.services.project_history",
        "agent_artifact_workspace",
        "agent_artifact_settings",
        'os.environ.get("PROJECT_DB_PATH"',
        "os.environ['PROJECT_DB_PATH']",
        'os.environ.get("PROJECT_DB_PATH"',
    )
    for path in package.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in forbidden_imports:
            assert token not in text, f"{path.relative_to(package)} references {token}"
