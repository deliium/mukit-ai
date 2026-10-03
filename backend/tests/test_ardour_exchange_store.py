"""Filesystem package store for Ardour exchange."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ardour_exchange_schemas import ArdourExchangeError, ArdourExchangePreviewV1
from app.ardour_exchange_settings import load_ardour_exchange_settings
from app.composition_schemas import CompositionV2
from app.services import ardour_exchange_store as store

_FIXTURE_V2 = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "fixtures"
    / "composition_v2_expressive.json"
)

# Minimal Type-0 SMF: header + one empty track end-of-track.
_MINIMAL_MIDI = bytes.fromhex(
    "4d54686400000006000000010060"
    "4d54726b0000000400ff2f00"
)


def _env(tmp_path: Path) -> dict[str, str]:
    root = tmp_path / "exchange"
    root.mkdir()
    return {
        "ARDOUR_EXCHANGE_ENABLED": "1",
        "ARDOUR_EXCHANGE_ROOT": str(root),
        "PROJECT_DB_PATH": str(tmp_path / "projects.db"),
        "ARDOUR_EXCHANGE_MAX_PACKAGES": "3",
        "ARDOUR_EXCHANGE_PACKAGE_TTL_SECONDS": "86400",
    }


def _settings(tmp_path: Path):
    return load_ardour_exchange_settings(_env(tmp_path))


def test_write_read_list_delete_round_trip(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    manifest = store.new_manifest_payload(
        direction="inbound",
        track_name="Idea",
        tempo_bpm=120,
        time_signature="4/4",
        start_bar=1,
        bar_count=8,
        start_samples=0,
        sample_rate=48000,
        source_fingerprint="abcdef0123456789",
    )
    written = store.write_package(settings, manifest=manifest, midi_bytes=_MINIMAL_MIDI)
    assert written.package_id == manifest.package_id
    assert (written.directory / "material.mid").is_file()

    loaded = store.read_package(settings, manifest.package_id)
    assert loaded.midi_bytes == _MINIMAL_MIDI
    assert loaded.manifest.bar_count == 8

    listed = store.list_packages(settings)
    assert [row.package_id for row in listed] == [manifest.package_id]

    zipped = store.package_to_zip_bytes(loaded)
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(zipped)) as archive:
        raw = json.loads(archive.read("manifest.json"))
    raw["package_id"] = "aex_fedcba9876543210"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        out.writestr("manifest.json", json.dumps(raw))
        out.writestr("material.mid", _MINIMAL_MIDI)
    second = store.ingest_zip_bytes(settings, buf.getvalue())
    assert second.package_id == "aex_fedcba9876543210"

    store.delete_package(settings, manifest.package_id)
    with pytest.raises(ArdourExchangeError) as excinfo:
        store.read_package(settings, manifest.package_id)
    assert excinfo.value.code == "ardour_exchange_not_found"


def test_path_escape_in_zip_refused(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("../evil.json", b"{}")
        archive.writestr("manifest.json", b"{}")
        archive.writestr("material.mid", _MINIMAL_MIDI)
    with pytest.raises(ArdourExchangeError) as excinfo:
        store.ingest_zip_bytes(settings, buf.getvalue())
    assert excinfo.value.code in {
        "ardour_exchange_path_escape",
        "ardour_exchange_package_invalid",
    }


def test_gc_cap_removes_oldest(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    ids = []
    for index in range(4):
        manifest = store.new_manifest_payload(
            direction="inbound",
            track_name=f"T{index}",
            tempo_bpm=120,
            time_signature="4/4",
            start_bar=1,
            bar_count=8,
            start_samples=0,
            sample_rate=48000,
            source_fingerprint="abcdef0123456789",
        )
        store.write_package(settings, manifest=manifest, midi_bytes=_MINIMAL_MIDI)
        ids.append(manifest.package_id)
    remaining = {row.package_id for row in store.list_packages(settings)}
    assert len(remaining) == 3
    assert ids[0] not in remaining


def test_preview_registry_clear(tmp_path: Path) -> None:
    store.clear_exchange_preview()
    assert store.get_exchange_preview() is None

    composition = CompositionV2.model_validate(
        json.loads(_FIXTURE_V2.read_text(encoding="utf-8"))
    )
    manifest = store.new_manifest_payload(
        direction="inbound",
        track_name="Idea",
        tempo_bpm=120,
        time_signature="4/4",
        start_bar=1,
        bar_count=8,
        start_samples=0,
        sample_rate=48000,
        source_fingerprint="abcdef0123456789",
    )
    preview = ArdourExchangePreviewV1.model_validate(
        {
            "schema_version": "ardour.exchange.preview.v1",
            "preview_id": "prev_aabbccddeeff",
            "package_id": manifest.package_id,
            "manifest": manifest.model_dump(mode="json"),
            "draft_composition": composition.model_dump(mode="json"),
            "import_report": {
                "issue_codes": [],
                "warning_codes": [],
                "note_count": 1,
                "track_count": 1,
            },
            "alignment": {
                "start_bar": 1,
                "bar_count": 8,
                "tempo_bpm": 120,
                "time_signature": "4/4",
            },
        }
    )
    store.set_exchange_preview(preview)
    assert store.get_exchange_preview() is not None
    store.shutdown_exchange(gc=False, env=_env(tmp_path))
    assert store.get_exchange_preview() is None
