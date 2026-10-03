"""Bearer decisions for execution nodes. The token string stays out of the log."""

from __future__ import annotations

import logging

import pytest

from app.services.execution_node_auth import authorize_execution_node_request

_TOKEN = "execution-token-fixture-9f3c"


def _decide(
    *,
    token: str | None,
    header: str | None,
    host: str | None,
    query: bool = False,
):
    return authorize_execution_node_request(
        configured_token=token,
        authorization_header=header,
        peer_host=host,
        query_token_present=query,
    )


def test_missing_token_refuses_all_peers(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    loopback = _decide(token=None, header=None, host="127.0.0.1")
    assert loopback.verdict == "unauthorized"
    assert loopback.code == "execution_node_unauthorized"
    assert loopback.peer_class == "loopback"
    lan = _decide(token=None, header=f"Bearer {_TOKEN}", host="10.0.0.8")
    assert lan.verdict == "unauthorized"
    assert _TOKEN not in caplog.text


def test_configured_token_required_even_on_loopback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    header = f"Bearer {_TOKEN}"
    loopback = _decide(token=_TOKEN, header=header, host="127.0.0.1")
    assert loopback.verdict == "allow"
    assert loopback.peer_class == "loopback"
    starlette = _decide(token=_TOKEN, header=header, host="testclient")
    assert starlette.verdict == "allow"
    missing_header = _decide(token=_TOKEN, header=None, host="127.0.0.1")
    assert missing_header.verdict == "unauthorized"
    basic = _decide(token=_TOKEN, header=f"Basic {_TOKEN}", host="testclient")
    assert basic.verdict == "unauthorized"
    wrong = _decide(token=_TOKEN, header="Bearer other-secret", host="testclient")
    assert wrong.verdict == "unauthorized"
    query = _decide(token=_TOKEN, header=header, host="127.0.0.1", query=True)
    assert query.verdict == "unauthorized"
    assert query.code == "execution_node_unauthorized"
    missing_host = _decide(token=_TOKEN, header=header, host=None)
    assert missing_host.verdict == "unauthorized"
    assert _TOKEN not in caplog.text
    refused = [record for record in caplog.records if record.levelno == logging.INFO]
    assert refused
    assert all(getattr(record, "code", None) == "execution_node_unauthorized" for record in refused)
