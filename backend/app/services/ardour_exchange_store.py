"""Filesystem package store for Ardour exchange under ``ARDOUR_EXCHANGE_ROOT``.

No SQLite. Packages are directories (``manifest.json`` + ``material.mid`` +
optional ``audio/``). A process-memory registry holds the current session
preview id. Lifespan calls ``clear_exchange_preview`` / ``shutdown_exchange``.
"""

from __future__ import annotations

import io
import json
import logging
import os
import secrets
import shutil
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from app.ardour_exchange_schemas import (
    ArdourExchangeError,
    ArdourExchangeManifestV1,
    ArdourExchangePackageV1,
    ArdourExchangePreviewV1,
    PACKAGE_ID_PATTERN,
)
from app.ardour_exchange_settings import (
    ArdourExchangeSettings,
    load_ardour_exchange_settings,
)

logger = logging.getLogger(__name__)

_MANIFEST_NAME = "manifest.json"
_MATERIAL_NAME = "material.mid"
_AUDIO_DIR = "audio"

_lock = threading.RLock()
_preview: ArdourExchangePreviewV1 | None = None
_preview_id: str | None = None


@dataclass(frozen=True)
class PackageReadResult:
    """Loaded package bytes + validated manifest."""

    package_id: str
    directory: Path
    manifest: ArdourExchangeManifestV1
    midi_bytes: bytes
    audio_files: dict[str, bytes]
    byte_size: int


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def allocate_package_id() -> str:
    return f"aex_{secrets.token_hex(8)}"


def allocate_preview_id() -> str:
    return f"prev_{secrets.token_hex(6)}"


def require_exchange_settings(
    env: Mapping[str, str] | None = None,
) -> ArdourExchangeSettings:
    """Load settings; raise when disabled or root is not configured."""
    settings = load_ardour_exchange_settings(env)
    if not settings.enabled:
        raise ArdourExchangeError("ardour_exchange_disabled")
    if not settings.root_configured or settings.exchange_root is None:
        raise ArdourExchangeError("ardour_exchange_root_unconfigured")
    return settings


def ensure_exchange_root(settings: ArdourExchangeSettings) -> Path:
    """Create the exchange root directory when missing."""
    if settings.exchange_root is None:
        raise ArdourExchangeError("ardour_exchange_root_unconfigured")
    root = settings.exchange_root
    root.mkdir(parents=True, exist_ok=True)
    logger.debug(
        "Ardour exchange root ready",
        extra={"root_basename": root.name},
    )
    return root.resolve()


def _confine(root: Path, candidate: Path) -> Path:
    resolved_root = root.resolve()
    resolved = candidate.resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        logger.warning(
            "Ardour exchange path escape refused",
            extra={"code": "ardour_exchange_path_escape", "basename": candidate.name},
        )
        raise ArdourExchangeError("ardour_exchange_path_escape")
    return resolved


def package_dir(settings: ArdourExchangeSettings, package_id: str) -> Path:
    if not PACKAGE_ID_PATTERN.match(package_id):
        raise ArdourExchangeError("ardour_exchange_package_invalid")
    root = ensure_exchange_root(settings)
    return _confine(root, root / package_id)


def _dir_byte_size(directory: Path) -> int:
    total = 0
    for path in directory.rglob("*"):
        if path.is_file() and not path.is_symlink():
            try:
                total += path.stat().st_size
            except OSError:
                continue
    return total


def _validate_member_name(name: str) -> str:
    text = name.replace("\\", "/").lstrip("/")
    if not text or text.endswith("/"):
        return text
    parts = text.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ArdourExchangeError("ardour_exchange_path_escape")
    if parts[0] not in {_MANIFEST_NAME, _MATERIAL_NAME, _AUDIO_DIR}:
        # Allow nested under audio/ only for wavs; top-level extras refused.
        if not (len(parts) == 2 and parts[0] == _AUDIO_DIR):
            raise ArdourExchangeError(
                "ardour_exchange_package_invalid",
                details={"member": parts[0][:40]},
            )
    return text


def write_package(
    settings: ArdourExchangeSettings,
    *,
    manifest: ArdourExchangeManifestV1,
    midi_bytes: bytes,
    audio_files: Mapping[str, bytes] | None = None,
) -> PackageReadResult:
    """Atomically write a package directory under the exchange root."""
    if not midi_bytes:
        raise ArdourExchangeError(
            "ardour_exchange_package_invalid",
            details={"reason": "empty_midi"},
        )
    package_id = manifest.package_id
    root = ensure_exchange_root(settings)
    target = package_dir(settings, package_id)
    if target.exists():
        raise ArdourExchangeError(
            "ardour_exchange_package_invalid",
            details={"reason": "package_exists"},
        )

    audio_map = dict(audio_files or {})
    for relpath in audio_map:
        if not relpath.startswith("audio/") or ".." in relpath or relpath.endswith("/"):
            raise ArdourExchangeError("ardour_exchange_path_escape")

    staging_parent = root / ".staging"
    staging_parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f"{package_id}_", dir=str(staging_parent))
    )
    try:
        (staging / _MANIFEST_NAME).write_text(
            json.dumps(manifest.model_dump(mode="json"), separators=(",", ":"), sort_keys=True),
            encoding="utf-8",
        )
        (staging / _MATERIAL_NAME).write_bytes(midi_bytes)
        if audio_map:
            audio_dir = staging / _AUDIO_DIR
            audio_dir.mkdir(parents=True, exist_ok=True)
            for relpath, payload in audio_map.items():
                name = Path(relpath).name
                if name != Path(relpath).name or "/" in name or "\\" in name:
                    raise ArdourExchangeError("ardour_exchange_path_escape")
                dest = _confine(staging, audio_dir / name)
                dest.write_bytes(payload)

        # Atomic publish: rename staging → package id.
        os.replace(staging, target)
        staging = target  # ownership transferred
    except Exception:
        if staging.exists() and staging != target:
            shutil.rmtree(staging, ignore_errors=True)
        raise

    byte_size = _dir_byte_size(target)
    logger.info(
        "Ardour exchange package created",
        extra={
            "package_id": package_id,
            "direction": manifest.direction,
            "byte_size": byte_size,
        },
    )
    gc_packages(settings)
    return PackageReadResult(
        package_id=package_id,
        directory=target,
        manifest=manifest,
        midi_bytes=midi_bytes,
        audio_files=audio_map,
        byte_size=byte_size,
    )


def read_package(
    settings: ArdourExchangeSettings,
    package_id: str,
) -> PackageReadResult:
    """Read and validate a package directory."""
    directory = package_dir(settings, package_id)
    if not directory.is_dir():
        raise ArdourExchangeError("ardour_exchange_not_found")
    return _read_package_directory(directory, package_id=package_id)


def _read_package_directory(directory: Path, *, package_id: str) -> PackageReadResult:
    for child in directory.rglob("*"):
        if child.is_symlink():
            logger.warning(
                "Ardour exchange package has symlink",
                extra={"code": "ardour_exchange_package_invalid", "package_id": package_id},
            )
            raise ArdourExchangeError("ardour_exchange_package_invalid")

    manifest_path = directory / _MANIFEST_NAME
    material_path = directory / _MATERIAL_NAME
    if not manifest_path.is_file() or not material_path.is_file():
        raise ArdourExchangeError("ardour_exchange_package_invalid")

    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArdourExchangeError("ardour_exchange_package_invalid") from exc

    try:
        manifest = ArdourExchangeManifestV1.model_validate(raw)
    except Exception as exc:
        raise ArdourExchangeError("ardour_exchange_package_invalid") from exc

    if manifest.package_id != package_id:
        raise ArdourExchangeError(
            "ardour_exchange_package_invalid",
            details={"reason": "package_id_mismatch"},
        )

    midi_bytes = material_path.read_bytes()
    if not midi_bytes:
        raise ArdourExchangeError("ardour_exchange_package_invalid")

    audio_files: dict[str, bytes] = {}
    audio_dir = directory / _AUDIO_DIR
    if audio_dir.is_dir():
        for wav in sorted(audio_dir.glob("*.wav")):
            if wav.is_symlink() or not wav.is_file():
                continue
            rel = f"{_AUDIO_DIR}/{wav.name}"
            audio_files[rel] = wav.read_bytes()

    return PackageReadResult(
        package_id=package_id,
        directory=directory,
        manifest=manifest,
        midi_bytes=midi_bytes,
        audio_files=audio_files,
        byte_size=_dir_byte_size(directory),
    )


def list_packages(settings: ArdourExchangeSettings) -> list[ArdourExchangePackageV1]:
    """List package ids under the exchange root (metadata only)."""
    root = ensure_exchange_root(settings)
    rows: list[ArdourExchangePackageV1] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if not PACKAGE_ID_PATTERN.match(child.name):
            continue
        try:
            packed = _read_package_directory(child, package_id=child.name)
        except ArdourExchangeError:
            logger.warning(
                "Ardour exchange skipped invalid package",
                extra={"code": "ardour_exchange_package_invalid", "package_id": child.name},
            )
            continue
        rows.append(
            ArdourExchangePackageV1(
                package_id=packed.package_id,
                direction=packed.manifest.direction,
                track_name=packed.manifest.track_name,
                bar_count=packed.manifest.bar_count,
                byte_size=packed.byte_size,
                created_at=packed.manifest.created_at,
            )
        )
    logger.debug("Ardour exchange list packages", extra={"count": len(rows)})
    return rows


def delete_package(settings: ArdourExchangeSettings, package_id: str) -> None:
    directory = package_dir(settings, package_id)
    if not directory.exists():
        raise ArdourExchangeError("ardour_exchange_not_found")
    byte_size = _dir_byte_size(directory) if directory.is_dir() else 0
    shutil.rmtree(directory, ignore_errors=False)
    logger.info(
        "Ardour exchange package deleted",
        extra={"package_id": package_id, "direction": "n/a", "byte_size": byte_size},
    )


def package_to_zip_bytes(result: PackageReadResult) -> bytes:
    """Serialize a package directory to zip bytes (same layout)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            _MANIFEST_NAME,
            json.dumps(
                result.manifest.model_dump(mode="json"),
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
        archive.writestr(_MATERIAL_NAME, result.midi_bytes)
        for relpath, payload in sorted(result.audio_files.items()):
            archive.writestr(relpath, payload)
    return buffer.getvalue()


def ingest_zip_bytes(
    settings: ArdourExchangeSettings,
    zip_bytes: bytes,
    *,
    package_id: str | None = None,
) -> PackageReadResult:
    """Expand a zip into a new package directory under the exchange root."""
    if len(zip_bytes) > settings.max_package_bytes:
        raise ArdourExchangeError("ardour_exchange_package_too_large")

    try:
        archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise ArdourExchangeError("ardour_exchange_package_invalid") from exc

    members: dict[str, bytes] = {}
    total = 0
    with archive:
        for info in archive.infolist():
            name = _validate_member_name(info.filename)
            if not name or name.endswith("/"):
                continue
            if info.is_dir():
                continue
            # Refuse symlink-like external attrs on Unix (high bit).
            if info.external_attr >> 16 and (info.external_attr >> 16) & 0o120000 == 0o120000:
                raise ArdourExchangeError("ardour_exchange_path_escape")
            data = archive.read(info)
            total += len(data)
            if total > settings.max_package_bytes:
                raise ArdourExchangeError("ardour_exchange_package_too_large")
            members[name] = data

    if _MANIFEST_NAME not in members or _MATERIAL_NAME not in members:
        raise ArdourExchangeError("ardour_exchange_package_invalid")

    try:
        raw_manifest = json.loads(members[_MANIFEST_NAME].decode("utf-8"))
        manifest = ArdourExchangeManifestV1.model_validate(raw_manifest)
    except Exception as exc:
        raise ArdourExchangeError("ardour_exchange_package_invalid") from exc

    if package_id is not None and manifest.package_id != package_id:
        raise ArdourExchangeError(
            "ardour_exchange_package_invalid",
            details={"reason": "package_id_mismatch"},
        )

    audio_files = {
        name: payload
        for name, payload in members.items()
        if name.startswith(f"{_AUDIO_DIR}/") and name.endswith(".wav")
    }
    return write_package(
        settings,
        manifest=manifest,
        midi_bytes=members[_MATERIAL_NAME],
        audio_files=audio_files,
    )


def gc_packages(settings: ArdourExchangeSettings) -> int:
    """Delete oldest packages beyond ``max_packages`` and expired TTL entries."""
    root = ensure_exchange_root(settings)
    entries: list[tuple[float, Path]] = []
    now = time.time()
    removed = 0
    for child in root.iterdir():
        if not child.is_dir() or child.name.startswith(".") or not PACKAGE_ID_PATTERN.match(child.name):
            continue
        try:
            mtime = child.stat().st_mtime
        except OSError:
            continue
        age = now - mtime
        if age > settings.package_ttl_seconds:
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
            logger.info(
                "Ardour exchange package GC ttl",
                extra={"package_id": child.name, "direction": "n/a", "byte_size": 0},
            )
            continue
        entries.append((mtime, child))

    entries.sort(key=lambda item: item[0])
    overflow = len(entries) - settings.max_packages
    for _, path in entries[: max(0, overflow)]:
        shutil.rmtree(path, ignore_errors=True)
        removed += 1
        logger.info(
            "Ardour exchange package GC cap",
            extra={"package_id": path.name, "direction": "n/a", "byte_size": 0},
        )
    if removed:
        logger.debug("Ardour exchange GC finished", extra={"removed": removed})
    return removed


def set_exchange_preview(preview: ArdourExchangePreviewV1) -> None:
    """Replace the process-memory session preview."""
    global _preview, _preview_id
    with _lock:
        _preview = preview
        _preview_id = preview.preview_id
    logger.info(
        "Ardour exchange preview set",
        extra={"preview_id": preview.preview_id, "package_id": preview.package_id},
    )


def get_exchange_preview() -> ArdourExchangePreviewV1 | None:
    with _lock:
        return _preview


def clear_exchange_preview() -> None:
    """Drop the session preview (lifespan / Discard)."""
    global _preview, _preview_id
    with _lock:
        had = _preview_id
        _preview = None
        _preview_id = None
    logger.info(
        "Ardour exchange preview cleared",
        extra={"preview_id": had},
    )


def shutdown_exchange(*, gc: bool = True, env: Mapping[str, str] | None = None) -> None:
    """Lifespan teardown: clear preview and optionally GC packages."""
    clear_exchange_preview()
    if not gc:
        logger.info("Ardour exchange lifespan teardown", extra={"gc": False})
        return
    settings = load_ardour_exchange_settings(env)
    if settings.root_configured and settings.exchange_root is not None:
        try:
            removed = gc_packages(settings)
        except ArdourExchangeError:
            removed = 0
        logger.info(
            "Ardour exchange lifespan teardown",
            extra={"gc": True, "removed": removed},
        )
    else:
        logger.info("Ardour exchange lifespan teardown", extra={"gc": True, "removed": 0})


def package_count(settings: ArdourExchangeSettings) -> int:
    if not settings.root_configured or settings.exchange_root is None:
        return 0
    if not settings.exchange_root.is_dir():
        return 0
    return sum(
        1
        for child in settings.exchange_root.iterdir()
        if child.is_dir()
        and not child.name.startswith(".")
        and PACKAGE_ID_PATTERN.match(child.name)
    )


def new_manifest_payload(
    *,
    direction: str,
    track_name: str,
    tempo_bpm: int,
    time_signature: str,
    start_bar: int,
    bar_count: int,
    start_samples: int,
    sample_rate: int,
    source_fingerprint: str,
    ticks_per_quarter: int = 480,
    source_track_ssid: int | None = None,
    length_samples: int | None = None,
    audio_relpaths: list[str] | None = None,
    package_id: str | None = None,
    created_at: str | None = None,
) -> ArdourExchangeManifestV1:
    """Helper to build a validated manifest with a fresh package id."""
    return ArdourExchangeManifestV1.model_validate(
        {
            "schema_version": "ardour.exchange.manifest.v1",
            "package_id": package_id or allocate_package_id(),
            "direction": direction,
            "track_name": track_name,
            "source_track_ssid": source_track_ssid,
            "tempo_bpm": tempo_bpm,
            "time_signature": time_signature,
            "ticks_per_quarter": ticks_per_quarter,
            "start_bar": start_bar,
            "bar_count": bar_count,
            "start_samples": start_samples,
            "length_samples": length_samples,
            "sample_rate": sample_rate,
            "material_relpath": "material.mid",
            "audio_relpaths": list(audio_relpaths or []),
            "source_fingerprint": source_fingerprint,
            "created_at": created_at or _utc_now_iso(),
        }
    )


# Keep typing import used for Mapping in public helpers.
_ = Any
