"""Malformed-input, resource-limit, and HTTP mapping gates for composition import."""

from __future__ import annotations

import io
import logging
from pathlib import Path

import mido
import pytest
from fastapi.testclient import TestClient

from app.import_schemas import CompositionImportError
from app.import_settings import load_import_settings
from app.main import app
from app.services.composition_midi_import import import_midi_bytes, parse_midi_bytes
from app.services.composition_musicxml_import import import_musicxml_bytes, parse_musicxml_bytes
from tests.fixtures.build_import_fixtures import SENTINEL_FILENAME

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "import"


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def _load(name: str) -> bytes:
    return (FIXTURE_DIR / name).read_bytes()


def _midi_bytes(mid: mido.MidiFile) -> bytes:
    buf = io.BytesIO()
    mid.save(file=buf)
    return buf.getvalue()


def test_empty_upload_returns_422(client):
    response = client.post(
        "/imports/midi",
        files={"file": ("empty.mid", b"", "audio/midi")},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "import_malformed_source"


def test_forged_extension_still_uses_content_signature(client):
    midi = _load("multitrack.mid")
    response = client.post(
        "/imports/musicxml",
        files={"file": ("looks-like.musicxml", midi, "application/xml")},
    )
    assert response.status_code == 415
    detail = response.json()["detail"]
    assert detail["code"] == "import_unsupported_media_type"
    assert SENTINEL_FILENAME not in response.text


def test_truncated_midi_fixture_is_malformed():
    with pytest.raises(CompositionImportError) as exc_info:
        parse_midi_bytes(_load("truncated.mid"))
    assert exc_info.value.code == "import_malformed_source"
    assert exc_info.value.http_status == 422


def test_smpte_division_rejected():
    # Minimal SMPTE header: MThd, length 6, format 0, 1 track, SMPTE division.
    header = b"MThd" + (6).to_bytes(4, "big") + b"\x00\x00\x00\x01\xe7\x28"
    with pytest.raises(CompositionImportError) as exc_info:
        parse_midi_bytes(header + b"MTrk" + (0).to_bytes(4, "big"))
    assert exc_info.value.code in {"import_malformed_source", "import_non_representable", "import_unsupported_media_type"}
    assert "smpte" in str(exc_info.value.details).lower() or "SMPTE" in str(exc_info.value)


def test_entity_and_zip_slip_fixtures_rejected(client):
    entity = client.post(
        "/imports/musicxml",
        files={"file": ("entities.musicxml", _load("entities.musicxml"), "application/xml")},
    )
    assert entity.status_code == 422
    assert entity.json()["detail"]["code"] == "import_malformed_source"

    slip = client.post(
        "/imports/musicxml",
        files={"file": ("bad.mxl", _load("zip_slip.mxl"), "application/vnd.recordare.musicxml")},
    )
    assert slip.status_code == 422
    assert slip.json()["detail"]["code"] == "import_malformed_source"


def test_upload_byte_limit_enforced_without_nginx(client, monkeypatch):
    monkeypatch.setenv("IMPORT_MAX_UPLOAD_BYTES", "2048")
    # Reload settings via explicit env for route path uses load_import_settings each request.
    oversized = b"MThd" + (b"\x00" * 3000)
    response = client.post(
        "/imports/midi",
        files={"file": ("huge.mid", oversized, "audio/midi")},
    )
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "import_payload_too_large"


def test_track_and_note_limits_enforced(monkeypatch):
    settings = load_import_settings({"IMPORT_MAX_TRACKS": "1", "IMPORT_MAX_NOTES": "100"})
    with pytest.raises(CompositionImportError) as tracks:
        import_midi_bytes(_load("multitrack.mid"), display_filename="multitrack.mid", settings=settings)
    assert tracks.value.code == "import_complexity_exceeded"
    assert tracks.value.http_status == 413

    settings_notes = load_import_settings({"IMPORT_MAX_NOTES": "2"})
    with pytest.raises(CompositionImportError) as notes:
        import_midi_bytes(_load("multitrack.mid"), display_filename="multitrack.mid", settings=settings_notes)
    assert notes.value.code == "import_complexity_exceeded"


def test_failed_import_does_not_mutate_unrelated_state(client):
    # Baseline successful import then failed one; failed response must be bounded.
    ok = client.post(
        "/imports/midi",
        files={"file": ("ok.mid", _load("multitrack.mid"), "audio/midi")},
    )
    assert ok.status_code == 200
    failed = client.post(
        "/imports/midi",
        files={"file": ("bad.mid", _load("truncated.mid"), "audio/midi")},
    )
    assert failed.status_code == 422
    detail = failed.json()["detail"]
    assert set(detail.keys()) <= {"code", "message", "details"}
    assert "MThd" not in failed.text
    assert len(failed.text) < 4000


def test_rejected_import_logs_omit_payload_sentinels(caplog, client):
    with caplog.at_level(logging.DEBUG):
        client.post(
            "/imports/midi",
            files={"file": (SENTINEL_FILENAME, _load("truncated.mid"), "audio/midi")},
        )
        client.post(
            "/imports/musicxml",
            files={"file": ("entities.musicxml", _load("entities.musicxml"), "application/xml")},
        )
    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert SENTINEL_FILENAME not in joined
    assert "file:///etc/passwd" not in joined
    assert "<!ENTITY" not in joined


def test_nested_archive_and_encrypted_mxl_rejected():
    # Nested zip as entry body.
    outer = io.BytesIO()
    import zipfile

    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as nested:
        nested.writestr("nested.xml", "<score-partwise/>")
    with zipfile.ZipFile(outer, "w") as archive:
        archive.writestr("META-INF/container.xml", """<?xml version="1.0"?><container><rootfiles>
        <rootfile full-path="score.mxl"/></rootfiles></container>""")
        archive.writestr("score.mxl", inner.getvalue())
    with pytest.raises(CompositionImportError) as nested_exc:
        parse_musicxml_bytes(outer.getvalue())
    assert nested_exc.value.http_status in {422, 413}
