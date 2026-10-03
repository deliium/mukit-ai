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
