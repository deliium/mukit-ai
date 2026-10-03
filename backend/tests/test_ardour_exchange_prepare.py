"""Outbound prepare for Ardour exchange packages."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from app.ardour_exchange_schemas import ArdourExchangePrepareRequestV1
from app.ardour_exchange_settings import load_ardour_exchange_settings
from app.services import ardour_exchange_store as store
from app.services.ardour_exchange_ingest import ingest_package_id
from app.services.ardour_exchange_prepare import prepare_outbound_package

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
    }


def test_prepare_preserves_inbound_alignment(tmp_path: Path) -> None:
    store.clear_exchange_preview()
    settings = load_ardour_exchange_settings(_env(tmp_path))
    assert settings.exchange_root is not None
    manifest = json.loads((_FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))
    dest = settings.exchange_root / manifest["package_id"]
    shutil.copytree(_FIXTURE_DIR, dest)
    ingest_package_id(settings, manifest["package_id"])

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
