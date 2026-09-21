"""Source catalog loader with fail-closed provenance sidecars."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from app.dataset.errors import DatasetConfigError, DatasetProvenanceError
from app.dataset.schemas import (
    DatasetLabels,
    DatasetPipelineConfig,
    DatasetPipelineSource,
    DatasetProvenance,
    ProvenanceStatus,
)
from app.import_settings import (
    MIDI_EXTENSIONS,
    MUSICXML_EXTENSIONS,
    MXL_EXTENSIONS,
)


logger = logging.getLogger(__name__)

_JSON_EXTENSIONS = frozenset({".json"})
_SUPPORTED_EXTENSIONS = MIDI_EXTENSIONS | MUSICXML_EXTENSIONS | MXL_EXTENSIONS | _JSON_EXTENSIONS
_SIDECAR_SUFFIXES = (".meta.json", ".meta.yaml", ".meta.yml")


@dataclass(frozen=True)
class CataloguedSource:
    path: Path
    source_id: str
    provenance: DatasetProvenance
    labels: DatasetLabels
    display_filename: str


def load_pipeline_config(path: Path) -> DatasetPipelineConfig:
    """Load ``dataset.pipeline.v1`` from YAML or JSON."""
    raw_text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    try:
        if suffix in {".yaml", ".yml"}:
            import yaml

            data = yaml.safe_load(raw_text)
        else:
            data = json.loads(raw_text)
    except Exception as exc:
        raise DatasetConfigError(
            "pipeline_config_parse_failed",
            f"Failed to parse pipeline config {path.name}",
            details={"error_type": type(exc).__name__},
        ) from exc
    if not isinstance(data, dict):
        raise DatasetConfigError(
            "pipeline_config_invalid",
            "Pipeline config root must be a mapping",
        )
    try:
        config = DatasetPipelineConfig.model_validate(data)
    except Exception as exc:
        logger.debug(
            "Pipeline schema validation failed",
            extra={"field_names": _validation_field_names(exc)},
        )
        raise DatasetConfigError(
            "pipeline_config_invalid",
            "Pipeline config failed schema validation",
            details={"error_type": type(exc).__name__},
        ) from exc
    logger.info(
        "Pipeline config loaded",
        extra={
            "dataset_name": config.dataset_name,
            "source_entry_count": len(config.sources),
            "split_seed": config.split_seed,
        },
    )
    return config


def iterate_catalogued_sources(
    config: DatasetPipelineConfig,
    *,
    base_dir: Path | None = None,
) -> list[CataloguedSource]:
    """Expand pipeline sources into concrete files with mandatory provenance."""
    base = base_dir or Path.cwd()
    collected: list[CataloguedSource] = []
    seen_ids: set[str] = set()

    for entry in config.sources:
        for source in _expand_entry(entry, config=config, base_dir=base):
            if source.source_id in seen_ids:
                raise DatasetConfigError(
                    "duplicate_source_id",
                    f"Duplicate source_id {source.source_id}",
                )
            seen_ids.add(source.source_id)
            collected.append(source)

    collected.sort(key=lambda item: item.source_id)
    logger.info(
        "Source catalog indexed",
        extra={"source_count": len(collected), "dataset_name": config.dataset_name},
    )
    return collected


def _expand_entry(
    entry: DatasetPipelineSource,
    *,
    config: DatasetPipelineConfig,
    base_dir: Path,
) -> Iterable[CataloguedSource]:
    root = Path(entry.path)
    if not root.is_absolute():
        root = (base_dir / root).resolve()
    else:
        root = root.resolve()

    paths: list[Path]
    if root.is_file():
        paths = [root]
    elif root.is_dir():
        pattern = entry.glob or "**/*"
        paths = sorted(
            p
            for p in root.glob(pattern)
            if p.is_file() and p.suffix.lower() in _SUPPORTED_EXTENSIONS and not _is_sidecar(p)
        )
    else:
        raise DatasetConfigError(
            "source_path_missing",
            f"Source path does not exist: {root.name}",
            details={"path_basename": root.name},
        )

    for path in paths:
        provenance, labels = _resolve_metadata(
            path,
            entry=entry,
            config=config,
        )
        source_id = _stable_source_id(path, root if root.is_dir() else path.parent)
        yield CataloguedSource(
            path=path,
            source_id=source_id,
            provenance=provenance,
            labels=labels,
            display_filename=path.name[:120],
        )


def _resolve_metadata(
    path: Path,
    *,
    entry: DatasetPipelineSource,
    config: DatasetPipelineConfig,
) -> tuple[DatasetProvenance, DatasetLabels]:
    sidecar = _load_sidecar(path)
    status = _first_status(
        sidecar.get("provenance_status") or sidecar.get("status"),
        entry.default_provenance,
        config.default_provenance,
    )
    if status is None:
        logger.error(
            "Provenance status missing; fail closed",
            extra={"source_basename": path.name},
        )
        raise DatasetProvenanceError(
            "provenance_missing",
            f"Provenance status required for source {path.name}",
            details={"source_basename": path.name},
        )

    payload = {
        "status": status,
        "license": sidecar.get("license", entry.license),
        "license_spdx": sidecar.get("license_spdx", entry.license_spdx),
        "source_url": sidecar.get("source_url", entry.source_url),
        "source_reference": sidecar.get("source_reference", entry.source_reference),
        "composer": sidecar.get("composer", entry.composer),
        "author": sidecar.get("author", entry.author),
        "user_owned_attested": bool(
            sidecar.get("user_owned_attested", entry.user_owned_attested)
        ),
    }
    try:
        provenance = DatasetProvenance.model_validate(payload)
    except Exception as exc:
        logger.debug(
            "Provenance validation failed",
            extra={"field_names": _validation_field_names(exc), "source_basename": path.name},
        )
        raise DatasetProvenanceError(
            "provenance_invalid",
            f"Invalid provenance for {path.name}",
            details={"error_type": type(exc).__name__},
        ) from exc

    labels_raw = sidecar.get("labels")
    if isinstance(labels_raw, dict):
        labels = DatasetLabels.model_validate(labels_raw)
    elif entry.labels is not None:
        labels = entry.labels
    else:
        labels = DatasetLabels()
    return provenance, labels


def _load_sidecar(path: Path) -> dict[str, Any]:
    # Prefer ``file.mid.meta.json``; also accept ``file.meta.json``.
    candidates = [Path(str(path) + suffix) for suffix in _SIDECAR_SUFFIXES]
    candidates.extend(
        [
            path.with_name(f"{path.stem}.meta.json"),
            path.with_name(f"{path.stem}.meta.yaml"),
            path.with_name(f"{path.stem}.meta.yml"),
        ]
    )
    seen: set[Path] = set()
    for option in candidates:
        if option in seen or not option.exists():
            continue
        seen.add(option)
        text = option.read_text(encoding="utf-8")
        if option.name.endswith((".yaml", ".yml")):
            import yaml

            data = yaml.safe_load(text)
        else:
            data = json.loads(text)
        if not isinstance(data, dict):
            raise DatasetProvenanceError(
                "sidecar_invalid",
                f"Sidecar must be a mapping: {option.name}",
            )
        logger.debug(
            "Loaded provenance sidecar",
            extra={"sidecar_basename": option.name, "source_basename": path.name},
        )
        return data
    return {}


def _is_sidecar(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith(".meta.json") or name.endswith(".meta.yaml") or name.endswith(".meta.yml")


def _first_status(*values: Any) -> ProvenanceStatus | None:
    for value in values:
        if value is None:
            continue
        return value  # type: ignore[return-value]
    return None


def _stable_source_id(path: Path, root: Path) -> str:
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        rel = Path(path.name)
    text = str(rel).replace("\\", "/")
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in text)
    return safe[:200]


def _validation_field_names(exc: Exception) -> list[str]:
    errors = getattr(exc, "errors", None)
    if callable(errors):
        try:
            return [str(err.get("loc", ())) for err in errors()]
        except Exception:
            return []
    return []
