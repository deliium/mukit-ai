"""Realize intents for Ardour exchange (arrangement/development bridges)."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest

from app.ardour_exchange_schemas import ArdourExchangeRealizeRequestV1
from app.ardour_exchange_settings import load_ardour_exchange_settings
from app.services import ardour_exchange_store as store
from app.services.ardour_exchange_ingest import ingest_package_id
from app.services.ardour_exchange_realize import realize_exchange_intent

_FIXTURE_DIR = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "ardour_exchange"
    / "eight_bar_idea"
)


def _env(tmp_path: Path) -> dict[str, str]:
    root = tmp_path / "exchange"
    root.mkdir(exist_ok=True)
    return {
        "ARDOUR_EXCHANGE_ENABLED": "1",
        "ARDOUR_EXCHANGE_ROOT": str(root),
        "PROJECT_DB_PATH": str(tmp_path / "projects.db"),
        "LLM_FAKE_MODE": "1",
    }


def _ingest(tmp_path: Path):
    store.clear_exchange_preview()
    settings = load_ardour_exchange_settings(_env(tmp_path))
    assert settings.exchange_root is not None
    manifest = json.loads((_FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))
    dest = settings.exchange_root / manifest["package_id"]
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(_FIXTURE_DIR, dest)
    return ingest_package_id(settings, manifest["package_id"])


def test_counter_melody_uses_create_countermelody(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    _ingest(tmp_path)
    request = ArdourExchangeRealizeRequestV1.model_validate(
        {
            "schema_version": "ardour.exchange.realize_request.v1",
            "intent": "counter_melody",
            "candidate_count": 1,
        }
    )
    result = asyncio.run(realize_exchange_intent(request))
    assert result["operation"] == "create_countermelody"
    assert result["surface"] == "arrangement"
    preview = result["preview"]
    assert preview["operation"] == "create_countermelody"
    assert len(preview["candidates"]) >= 1
    composition = preview["candidates"][0]["composition"]
    assert composition["bar_count"] == 8
    roles = {track["role"] for track in composition["tracks"]}
    instruments = {str(track.get("instrument", "")).lower() for track in composition["tracks"]}
    programs = {track.get("midi_program") for track in composition["tracks"]}
    assert "countermelody" in roles
    assert any("cello" in name for name in instruments) or 42 in programs


def test_add_accompaniment_adds_harmony_piano(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    _ingest(tmp_path)
    request = ArdourExchangeRealizeRequestV1.model_validate(
        {
            "schema_version": "ardour.exchange.realize_request.v1",
            "intent": "add_accompaniment",
            "candidate_count": 1,
        }
    )
    result = asyncio.run(realize_exchange_intent(request))
    assert result["operation"] == "add_accompaniment"
    assert result["surface"] == "arrangement"
    composition = result["preview"]["candidates"][0]["composition"]
    roles = {track["role"] for track in composition["tracks"]}
    assert "melody" in roles
    assert "harmony" in roles
    assert len(composition["tracks"]) >= 2


def test_orchestrate_selection_uses_string_parts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    _ingest(tmp_path)
    request = ArdourExchangeRealizeRequestV1.model_validate(
        {
            "schema_version": "ardour.exchange.realize_request.v1",
            "intent": "orchestrate_selection",
            "candidate_count": 1,
        }
    )
    result = asyncio.run(realize_exchange_intent(request))
    assert result["operation"] == "orchestrate_selected_tracks"
    assert result["surface"] == "arrangement"
    composition = result["preview"]["candidates"][0]["composition"]
    roles = {track["role"] for track in composition["tracks"]}
    instruments = {str(track.get("instrument", "")).lower() for track in composition["tracks"]}
    programs = {track.get("midi_program") for track in composition["tracks"]}
    assert "melody" in roles
    assert "bass" in roles or "harmony" in roles
    assert len(composition["tracks"]) >= 2
    assert (
        any("violin" in name or "cello" in name or "string" in name for name in instruments)
        or {40, 42, 48} & programs
    )


def test_reharmonize_selection_returns_harmony_surface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    _ingest(tmp_path)
    request = ArdourExchangeRealizeRequestV1.model_validate(
        {
            "schema_version": "ardour.exchange.realize_request.v1",
            "intent": "reharmonize_selection",
            "candidate_count": 1,
        }
    )
    result = asyncio.run(realize_exchange_intent(request))
    assert result["operation"] == "reharmonize"
    assert result["surface"] == "harmony"
    preview = result["preview"]
    assert preview["content_policy"] == "preserve_harmony_adapt_melody"
    assert len(preview["candidates"]) == 1
    candidate = preview["candidates"][0]
    assert candidate["candidate_id"].startswith("harmony-")
    composition = candidate["composition"]
    assert composition["schema_version"] == "composition.v2"
    assert len(composition.get("harmony") or []) >= 1
    assert composition["bar_count"] == 8
