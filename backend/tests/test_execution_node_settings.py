"""Settings parsing for execution nodes."""

from __future__ import annotations

import logging

import pytest

from app.execution_node_schemas import ExecutionNodeError
from app.execution_node_settings import load_execution_node_settings


def test_default_disabled() -> None:
    settings = load_execution_node_settings({})
    assert settings.enabled is False
    assert settings.token is None
    assert settings.role == "controller"
    assert settings.fake is False
    assert settings.max_nodes == 8
    assert settings.max_concurrency == 2
    assert settings.heartbeat_ttl_seconds == 30


def test_enabled_requires_token() -> None:
    with pytest.raises(ExecutionNodeError) as exc:
        load_execution_node_settings({"AI_EXECUTION_NODES_ENABLED": "1"})
    assert exc.value.code == "execution_node_token_missing"


def test_enabled_with_token(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    token = "shared-execution-token-fixture"
    settings = load_execution_node_settings(
        {
            "AI_EXECUTION_NODES_ENABLED": "true",
            "AI_EXECUTION_NODE_TOKEN": token,
            "AI_EXECUTION_NODE_ROLE": "both",
            "AI_EXECUTION_NODE_FAKE": "on",
            "AI_EXECUTION_NODE_MAX_NODES": "4",
            "AI_EXECUTION_NODE_MAX_CONCURRENCY": "3",
            "AI_EXECUTION_NODE_ALLOW_HOSTNAME": "1",
            "AI_EXECUTION_NODE_ADDRESS_ALLOW_CIDRS": "100.64.0.0/10, 198.18.0.0/15",
        }
    )
    assert settings.enabled is True
    assert settings.token == token
    assert settings.role == "both"
    assert settings.fake is True
    assert settings.max_nodes == 4
    assert settings.max_concurrency == 3
    assert settings.allow_hostname is True
    assert settings.address_allow_cidrs == ("100.64.0.0/10", "198.18.0.0/15")
    assert token not in caplog.text


def test_unrecognized_flag_off(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    settings = load_execution_node_settings({"AI_EXECUTION_NODES_ENABLED": "maybe"})
    assert settings.enabled is False
    assert any(
        getattr(record, "code", None) == "execution_nodes_flag_unrecognized"
        for record in caplog.records
    )
    assert "maybe" not in caplog.text


def test_ttl_stays_above_interval() -> None:
    settings = load_execution_node_settings(
        {
            "AI_EXECUTION_NODES_ENABLED": "1",
            "AI_EXECUTION_NODE_TOKEN": "t",
            "AI_EXECUTION_NODE_HEARTBEAT_INTERVAL_SECONDS": "40",
            "AI_EXECUTION_NODE_HEARTBEAT_TTL_SECONDS": "15",
        }
    )
    assert settings.heartbeat_interval_seconds == 40
    assert settings.heartbeat_ttl_seconds > settings.heartbeat_interval_seconds
