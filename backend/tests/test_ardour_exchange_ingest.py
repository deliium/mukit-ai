"""Ingest pipeline for Ardour exchange packages."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from app.ardour_exchange_schemas import ArdourExchangeError
from app.ardour_exchange_settings import load_ardour_exchange_settings
from app.services import ardour_exchange_store as store
from app.services.ardour_exchange_ingest import ingest_package_id

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
    }


def _copy_fixture(tmp_path: Path) -> str:
    settings = load_ardour_exchange_settings(_env(tmp_path))
    assert settings.exchange_root is not None
    manifest = json.loads((_FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))
    package_id = manifest["package_id"]
    dest = settings.exchange_root / package_id
    shutil.copytree(_FIXTURE_DIR, dest)
    return package_id


def test_golden_ingest_eight_bar_idea(tmp_path: Path) -> None:
    store.clear_exchange_preview()
    package_id = _copy_fixture(tmp_path)
    settings = load_ardour_exchange_settings(_env(tmp_path))
    preview = ingest_package_id(settings, package_id)

    assert preview.alignment.bar_count == 8
    assert preview.alignment.tempo_bpm == 120
    assert preview.alignment.time_signature == "4/4"
    assert preview.alignment.start_bar == 1
    assert preview.manifest.track_name == "Idea"
    assert preview.import_report.note_count > 0
    assert any(len(track.events) > 0 for track in preview.draft_composition.tracks)
    assert store.get_exchange_preview() is not None
    assert store.get_exchange_preview().package_id == package_id


def test_ingest_refuses_length_mismatch(tmp_path: Path) -> None:
    store.clear_exchange_preview()
    package_id = _copy_fixture(tmp_path)
    settings = load_ardour_exchange_settings(_env(tmp_path))
    assert settings.exchange_root is not None
    manifest_path = settings.exchange_root / package_id / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["length_samples"] = 1
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ArdourExchangeError) as excinfo:
        ingest_package_id(settings, package_id)
    assert excinfo.value.code == "ardour_exchange_alignment_invalid"
