"""Health, readiness, and CORS configuration tests."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.main import app, health_check, readiness_check
from app.ready import build_readiness_report, parse_cors_allow_origins


def test_health_check_liveness():
    response = asyncio.run(health_check())
    assert response == {"status": "healthy"}


def test_parse_cors_allow_origins_defaults():
    origins = parse_cors_allow_origins("")
    assert "http://localhost:3000" in origins
    assert "http://127.0.0.1:3000" in origins


def test_parse_cors_allow_origins_custom():
    origins = parse_cors_allow_origins("https://composer.example, http://localhost:5173")
    assert origins == ["https://composer.example", "http://localhost:5173"]


def test_ready_report_has_no_secret_fields(monkeypatch, tmp_path):
    monkeypatch.setenv("PROJECT_DB_PATH", str(tmp_path / "projects.db"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("LLM_FAKE_MODE", raising=False)
    monkeypatch.delenv("ARRANGEMENT_INSTRUMENT_CATALOG_PATH", raising=False)

    report = build_readiness_report()
    payload = str(report)
    assert "api_key" not in payload.lower()
    assert "secret" not in payload.lower()
    assert report["llm"]["configured"] is False
    assert report["llm"]["provider_count"] == 0
    assert "providers" in report["llm"]
    assert "database" in report
    assert "wav" in report
    assert report["arrangement_catalog"]["ok"] is True
    assert report["arrangement_catalog"]["fingerprint"]
    assert report["arrangement_catalog"]["catalog_version"]
    assert "path" not in report["arrangement_catalog"]


def test_ready_endpoint_ok_without_llm(monkeypatch, tmp_path):
    monkeypatch.setenv("PROJECT_DB_PATH", str(tmp_path / "ready.db"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("LLM_FAKE_MODE", raising=False)
    monkeypatch.delenv("ARRANGEMENT_INSTRUMENT_CATALOG_PATH", raising=False)

    report = asyncio.run(readiness_check())
    assert report["ready"] is True
    assert report["llm"]["configured"] is False
    assert report["arrangement_catalog"]["ok"] is True


def test_ready_false_when_arrangement_catalog_invalid(monkeypatch, tmp_path):
    monkeypatch.setenv("PROJECT_DB_PATH", str(tmp_path / "ready-catalog.db"))
    bad = tmp_path / "invalid-catalog.json"
    bad.write_text("{not-json", encoding="utf-8")
    monkeypatch.setenv("ARRANGEMENT_INSTRUMENT_CATALOG_PATH", str(bad))
    from app.services.instrument_catalog import clear_catalog_cache

    clear_catalog_cache()
    try:
        report = build_readiness_report()
        assert report["ready"] is False
        assert report["arrangement_catalog"]["ok"] is False
        assert report["arrangement_catalog"]["error_code"] == "catalog_invalid_json"
        assert str(bad) not in str(report)
    finally:
        clear_catalog_cache()


def test_cors_allows_configured_origin(monkeypatch, tmp_path):
    monkeypatch.setenv("PROJECT_DB_PATH", str(tmp_path / "cors.db"))
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "http://localhost:3000")

    # Middleware was configured at import time; exercise preflight against live app.
    client = TestClient(app)
    response = client.options(
        "/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code in {200, 204}
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_cors_denies_unknown_origin(tmp_path, monkeypatch):
    monkeypatch.setenv("PROJECT_DB_PATH", str(tmp_path / "cors2.db"))
    client = TestClient(app)
    response = client.options(
        "/health",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    # Starlette CORSMiddleware omits ACAO for disallowed origins
    assert response.headers.get("access-control-allow-origin") != "https://evil.example"
