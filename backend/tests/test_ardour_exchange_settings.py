"""Env parsing for Ardour exchange settings."""

from __future__ import annotations

from pathlib import Path

from app.ardour_exchange_settings import load_ardour_exchange_settings


def test_defaults_are_off_and_unconfigured(tmp_path: Path) -> None:
    settings = load_ardour_exchange_settings({})
    assert settings.enabled is False
    assert settings.root_configured is False
    assert settings.exchange_root is None
    assert settings.max_package_bytes == 25 * 1024 * 1024
    assert settings.max_packages == 32
    assert settings.package_ttl_seconds == 86_400


def test_truthy_flag_enables(tmp_path: Path) -> None:
    root = tmp_path / "exchange"
    root.mkdir()
    settings = load_ardour_exchange_settings(
        {
            "ARDOUR_EXCHANGE_ENABLED": "yes",
            "ARDOUR_EXCHANGE_ROOT": str(root),
            "PROJECT_DB_PATH": str(tmp_path / "projects.db"),
        }
    )
    assert settings.enabled is True
    assert settings.root_configured is True
    assert settings.exchange_root == root


def test_unrecognized_flag_is_off(tmp_path: Path) -> None:
    root = tmp_path / "exchange"
    root.mkdir()
    settings = load_ardour_exchange_settings(
        {
            "ARDOUR_EXCHANGE_ENABLED": "maybe",
            "ARDOUR_EXCHANGE_ROOT": str(root),
            "PROJECT_DB_PATH": str(tmp_path / "projects.db"),
        }
    )
    assert settings.enabled is False


def test_dataset_root_refused(tmp_path: Path) -> None:
    dataset = tmp_path / "datasets"
    dataset.mkdir()
    settings = load_ardour_exchange_settings(
        {
            "ARDOUR_EXCHANGE_ENABLED": "1",
            "ARDOUR_EXCHANGE_ROOT": str(dataset),
            "DATASET_ROOT": str(dataset),
            "PROJECT_DB_PATH": str(tmp_path / "projects.db"),
        }
    )
    assert settings.root_configured is False
    assert settings.exchange_root is None


def test_project_db_path_refused(tmp_path: Path) -> None:
    db = tmp_path / "projects.db"
    db.write_bytes(b"")
    settings = load_ardour_exchange_settings(
        {
            "ARDOUR_EXCHANGE_ENABLED": "1",
            "ARDOUR_EXCHANGE_ROOT": str(db),
            "PROJECT_DB_PATH": str(db),
        }
    )
    assert settings.root_configured is False


def test_limits_clamped(tmp_path: Path) -> None:
    root = tmp_path / "exchange"
    root.mkdir()
    settings = load_ardour_exchange_settings(
        {
            "ARDOUR_EXCHANGE_ROOT": str(root),
            "PROJECT_DB_PATH": str(tmp_path / "projects.db"),
            "ARDOUR_EXCHANGE_MAX_PACKAGE_BYTES": "100",
            "ARDOUR_EXCHANGE_MAX_PACKAGES": "9999",
            "ARDOUR_EXCHANGE_PACKAGE_TTL_SECONDS": "1",
        }
    )
    assert settings.max_package_bytes == 64 * 1024
    assert settings.max_packages == 500
    assert settings.package_ttl_seconds == 60
