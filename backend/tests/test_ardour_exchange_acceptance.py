"""End-to-end fake-mode acceptance: ingest → realize → prepare with material."""

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
_LUA_DIR = Path(__file__).resolve().parents[1] / "examples" / "ardour"


def _env(tmp_path: Path) -> dict[str, str]:
    root = tmp_path / "exchange"
    root.mkdir(exist_ok=True)
    return {
        "ARDOUR_EXCHANGE_ENABLED": "1",
        "ARDOUR_EXCHANGE_ROOT": str(root),
        "PROJECT_DB_PATH": str(tmp_path / "projects.db"),
        "LLM_FAKE_MODE": "1",
    }


def _ingest_fixture(tmp_path: Path):
    store.clear_exchange_preview()
    settings = load_ardour_exchange_settings(_env(tmp_path))
    assert settings.exchange_root is not None
    manifest = json.loads((_FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))
    dest = settings.exchange_root / manifest["package_id"]
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(_FIXTURE_DIR, dest)
    preview = ingest_package_id(settings, manifest["package_id"])
    return settings, preview


def test_eight_bar_countermelody_round_trip_alignment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    settings, preview = _ingest_fixture(tmp_path)
    assert preview.alignment.bar_count == 8
    assert preview.alignment.tempo_bpm == 120
    assert preview.import_report.note_count > 0

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
    assert realize["operation"] == "create_countermelody"
    candidate = realize["preview"]["candidates"][0]["composition"]
    assert candidate["bar_count"] == 8
    roles = {track["role"] for track in candidate["tracks"]}
    programs = {track.get("midi_program") for track in candidate["tracks"]}
    instruments = {str(track.get("instrument", "")).lower() for track in candidate["tracks"]}
    assert "countermelody" in roles
    assert any("cello" in name for name in instruments) or 42 in programs

    # Working composition prepare must export realized material + keep alignment.
    prepared = prepare_outbound_package(
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
    assert prepared.manifest.direction == "outbound"
    assert prepared.manifest.start_bar == expected["start_bar"]
    assert prepared.manifest.bar_count == expected["bar_count"]
    assert prepared.manifest.tempo_bpm == expected["tempo_bpm"]
    assert prepared.manifest.time_signature == expected["time_signature"]
    assert prepared.manifest.start_samples == expected["start_samples"]
    assert prepared.manifest.track_name == expected["track_name"]

    midi_bytes = (settings.exchange_root / prepared.package_id / "material.mid").read_bytes()
    imported = import_midi_bytes(midi_bytes, display_filename="outbound.mid")
    assert len(imported.composition.tracks) >= 2
    assert sum(len(track.events) for track in imported.composition.tracks) >= 2


@pytest.mark.parametrize(
    ("intent", "operation", "surface", "assert_fn"),
    [
        (
            "add_accompaniment",
            "add_accompaniment",
            "arrangement",
            lambda composition: (
                "harmony" in {track["role"] for track in composition["tracks"]}
                and len(composition["tracks"]) >= 2
            ),
        ),
        (
            "orchestrate_selection",
            "orchestrate_selected_tracks",
            "arrangement",
            lambda composition: len(composition["tracks"]) >= 2,
        ),
        (
            "reharmonize_selection",
            "reharmonize",
            "harmony",
            lambda composition: len(composition.get("harmony") or []) >= 1,
        ),
    ],
)
def test_new_realize_intents_prepare_working_material(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    intent: str,
    operation: str,
    surface: str,
    assert_fn,
) -> None:
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    settings, preview = _ingest_fixture(tmp_path)
    assert preview.alignment.bar_count == 8

    realize = asyncio.run(
        realize_exchange_intent(
            ArdourExchangeRealizeRequestV1.model_validate(
                {
                    "schema_version": "ardour.exchange.realize_request.v1",
                    "intent": intent,
                    "candidate_count": 1,
                }
            )
        )
    )
    assert realize["operation"] == operation
    assert realize["surface"] == surface
    candidate = realize["preview"]["candidates"][0]["composition"]
    assert assert_fn(candidate)

    prepared = prepare_outbound_package(
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
    assert prepared.manifest.start_bar == expected["start_bar"]
    assert prepared.manifest.bar_count == expected["bar_count"]
    assert prepared.manifest.start_samples == expected["start_samples"]

    midi_path = settings.exchange_root / prepared.package_id / "material.mid"
    assert midi_path.is_file()
    imported = import_midi_bytes(midi_path.read_bytes(), display_filename="outbound.mid")
    assert imported.composition.bar_count == 8
    assert sum(len(track.events) for track in imported.composition.tracks) >= 1


def test_lua_recipes_exist_and_document_operator_install() -> None:
    export_script = _LUA_DIR / "export_selected_midi_region.lua"
    import_script = _LUA_DIR / "import_exchange_package.lua"
    readme = _LUA_DIR / "README.md"
    assert export_script.is_file()
    assert import_script.is_file()
    text = readme.read_text(encoding="utf-8")
    assert "operator" in text.lower() or "Operator" in text
    assert "ARDOUR_EXCHANGE_ROOT" in text
    assert "never remote" in text.lower() or "never remote-executes" in text.lower() or "never remote-injects" in text.lower()
    export_body = export_script.read_text(encoding="utf-8")
    assert "material.mid" in export_body
    assert "manifest.json" in export_body
    assert "Type-0" in export_body or "Type 0" in export_body or "SMF" in export_body
