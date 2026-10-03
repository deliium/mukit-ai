"""Outbound prepare for Ardour exchange packages."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest

from app.ardour_exchange_schemas import (
    ArdourExchangePrepareRequestV1,
    ArdourExchangeRealizeRequestV1,
)
from app.ardour_exchange_settings import load_ardour_exchange_settings
from app.services import ardour_exchange_store as store
from app.services.ardour_exchange_ingest import ingest_package_id
from app.services.ardour_exchange_prepare import prepare_outbound_package
from app.services.ardour_exchange_realize import realize_exchange_intent
from app.services.composition_midi_import import import_midi_bytes

_FIXTURE_DIR = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "ardour_exchange"
    / "eight_bar_idea"
)
_EXPECTED = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "ardour_exchange"
    / "cello_counter_melody_outbound"
    / "expected_alignment.json"
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
    ingest_package_id(settings, manifest["package_id"])
    return settings


def test_prepare_preserves_inbound_alignment(tmp_path: Path) -> None:
    settings = _ingest(tmp_path)

    result = prepare_outbound_package(
        settings,
        ArdourExchangePrepareRequestV1.model_validate(
            {"schema_version": "ardour.exchange.prepare.v1", "use_preview_alignment": True}
        ),
    )
    expected = json.loads(_EXPECTED.read_text(encoding="utf-8"))
    assert result.manifest.direction == "outbound"
    assert result.manifest.start_bar == expected["start_bar"]
    assert result.manifest.bar_count == expected["bar_count"]
    assert result.manifest.tempo_bpm == expected["tempo_bpm"]
    assert result.manifest.time_signature == expected["time_signature"]
    assert result.manifest.start_samples == expected["start_samples"]
    assert result.manifest.track_name == expected["track_name"]
    assert result.has_audio is False
    assert result.byte_size > 0
    assert (settings.exchange_root / result.package_id / "material.mid").is_file()


def test_prepare_with_working_composition_exports_realized_material(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    settings = _ingest(tmp_path)
    realize = asyncio.run(
        realize_exchange_intent(
            ArdourExchangeRealizeRequestV1.model_validate(
                {
                    "schema_version": "ardour.exchange.realize_request.v1",
                    "intent": "counter_melody",
                    "candidate_count": 1,
                }
            )
        )
    )
    candidate = realize["preview"]["candidates"][0]["composition"]
    roles_before = {track["role"] for track in candidate["tracks"]}
    assert "countermelody" in roles_before

    result = prepare_outbound_package(
        settings,
        ArdourExchangePrepareRequestV1.model_validate(
            {
                "schema_version": "ardour.exchange.prepare.v1",
                "use_preview_alignment": True,
                "composition": candidate,
            }
        ),
    )
    expected = json.loads(_EXPECTED.read_text(encoding="utf-8"))
    assert result.manifest.start_bar == expected["start_bar"]
    assert result.manifest.bar_count == expected["bar_count"]
    assert result.manifest.start_samples == expected["start_samples"]

    midi_path = settings.exchange_root / result.package_id / "material.mid"
    midi_bytes = midi_path.read_bytes()
    imported = import_midi_bytes(midi_bytes, display_filename="outbound.mid")
    composition = imported.composition
    assert composition.schema_version == "composition.v2"
    assert len(composition.tracks) >= 2
    note_count = sum(len(track.events) for track in composition.tracks)
    assert note_count >= 2
