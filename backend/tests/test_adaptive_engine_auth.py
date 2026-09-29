"""Loopback and bearer decisions. The token string stays out of the log."""

from __future__ import annotations

import logging

import pytest

from app.services.adaptive_engine_auth import authorize_engine_request

_TOKEN = "engine-token-fixture-9f3c"


def _decide(
    *,
    token: str | None,
    header: str | None,
    host: str | None,
    query: bool = False,
):
    return authorize_engine_request(
        configured_token=token,
        authorization_header=header,
        peer_host=host,
        query_token_present=query,
    )


def test_unset_token_allows_only_loopback(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    allow = _decide(token=None, header=None, host="127.0.0.1")
    assert allow.verdict == "allow"
    assert allow.peer_class == "loopback"
    ipv6 = _decide(token=None, header=None, host="::1")
    assert ipv6.verdict == "allow"
    lan = _decide(token=None, header=None, host="10.0.0.8")
    assert lan.verdict == "unauthorized"
    assert lan.code == "engine_unauthorized"
    assert lan.peer_class == "other"
    testclient = _decide(token=None, header=None, host="testclient")
    assert testclient.verdict == "unauthorized"
    missing = _decide(token=None, header=f"Bearer {_TOKEN}", host=None)
    assert missing.verdict == "unauthorized"
    assert _TOKEN not in caplog.text


def test_configured_token_matches_on_loopback_and_testclient(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    header = f"Bearer {_TOKEN}"
    loopback = _decide(token=_TOKEN, header=header, host="127.0.0.1")
    assert loopback.verdict == "allow"
    starlette = _decide(token=_TOKEN, header=header, host="testclient")
    assert starlette.verdict == "allow"
    missing_header = _decide(token=_TOKEN, header=None, host="127.0.0.1")
    assert missing_header.verdict == "unauthorized"
    basic = _decide(token=_TOKEN, header=f"Basic {_TOKEN}", host="testclient")
    assert basic.verdict == "unauthorized"
    wrong = _decide(token=_TOKEN, header="Bearer other-secret", host="testclient")
    assert wrong.verdict == "unauthorized"
    query = _decide(token=_TOKEN, header="", host="127.0.0.1", query=True)
    assert query.verdict == "unauthorized"
    assert query.code == "engine_unauthorized"
    assert _TOKEN not in caplog.text
    refused = [record for record in caplog.records if record.levelno == logging.INFO]
    assert refused
    assert all(getattr(record, "code", None) == "engine_unauthorized" for record in refused)
    assert all(getattr(record, "peer_class", None) in {"loopback", "other"} for record in refused)
