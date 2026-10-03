"""HTTP API tests for Ardour exchange routes."""

from __future__ import annotations

import io
import json
import shutil
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import ardour_exchange_store as store

_FIXTURE_DIR = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "ardour_exchange"
    / "eight_bar_idea"
)


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    root = tmp_path / "exchange"
    root.mkdir()
    monkeypatch.setenv("ARDOUR_EXCHANGE_ENABLED", "1")
    monkeypatch.setenv("ARDOUR_EXCHANGE_ROOT", str(root))
    monkeypatch.setenv("PROJECT_DB_PATH", str(tmp_path / "projects.db"))
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    store.clear_exchange_preview()
    return TestClient(app)


def test_status_always_200_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARDOUR_EXCHANGE_ENABLED", "0")
    store.clear_exchange_preview()
    with TestClient(app) as client:
        response = client.get("/ardour/exchange/status")
    assert response.status_code == 200
    assert response.json()["enabled"] is False


def test_mutating_refused_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARDOUR_EXCHANGE_ENABLED", "0")
    with TestClient(app) as client:
        response = client.post(
            "/ardour/exchange/ingest",
            json={"package_id": "aex_0123456789abcdef"},
        )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ardour_exchange_disabled"


def test_ingest_fixture_apply_no_project_write(client: TestClient) -> None:
    from app.ardour_exchange_settings import load_ardour_exchange_settings

    settings = load_ardour_exchange_settings()
    assert settings.exchange_root is not None
    manifest = json.loads((_FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))
    dest = settings.exchange_root / manifest["package_id"]
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(_FIXTURE_DIR, dest)

    ingest = client.post(
        "/ardour/exchange/ingest",
        json={"package_id": manifest["package_id"]},
    )
    assert ingest.status_code == 200, ingest.text
    body = ingest.json()
    assert body["alignment"]["bar_count"] == 8
    assert body["draft_composition"]["schema_version"] == "composition.v2"

    apply = client.post("/ardour/exchange/apply")
    assert apply.status_code == 200
    assert apply.json()["composition"]["schema_version"] == "composition.v2"
    # Server does not write projects.composition_json — no project id involved.


def test_oversized_zip_returns_413(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # Settings clamp minimum is 64 KiB — exceed that.
    monkeypatch.setenv("ARDOUR_EXCHANGE_MAX_PACKAGE_BYTES", "65536")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("manifest.json", b"{" + b"x" * 70_000 + b"}")
        archive.writestr("material.mid", b"MThd" + b"\x00" * 100)
    response = client.post(
        "/ardour/exchange/ingest",
        files={"file": ("pkg.zip", buf.getvalue(), "application/zip")},
    )
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "ardour_exchange_package_too_large"


def test_lifespan_clear_drops_preview(client: TestClient, tmp_path: Path) -> None:
    from app.ardour_exchange_settings import load_ardour_exchange_settings

    settings = load_ardour_exchange_settings()
    assert settings.exchange_root is not None
    manifest = json.loads((_FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))
    dest = settings.exchange_root / manifest["package_id"]
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(_FIXTURE_DIR, dest)
    assert client.post(
        "/ardour/exchange/ingest",
        json={"package_id": manifest["package_id"]},
    ).status_code == 200
    assert client.get("/ardour/exchange/preview").status_code == 200
    store.shutdown_exchange(gc=False)
    assert client.get("/ardour/exchange/preview").status_code == 404
