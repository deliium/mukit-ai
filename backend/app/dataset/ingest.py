"""Batch ingest of MIDI / MusicXML / Composition JSON into ``dataset.item.v1`` shells."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

from app.composition_schemas import CompositionV2
from app.dataset.errors import DatasetIngestError
from app.dataset.fingerprint import dataset_content_hash
from app.dataset.normalize import normalize_dataset_composition
from app.dataset.provenance import compute_train_eligible
from app.dataset.schemas import (
    DatasetIngestIssueSummary,
    DatasetInstrumentSummary,
    DatasetItemV1,
    DatasetPipelineConfig,
    SourceFormat,
)
from app.dataset.settings import (
    DatasetSettings,
    dataset_settings_as_import_settings,
    load_dataset_settings,
)
from app.dataset.sources import CataloguedSource
from app.import_schemas import CompositionImportError
from app.import_settings import (
    MIDI_HEADER_SIGNATURE,
    XML_PROLOG_PREFIXES,
    ZIP_EMPTY_ARCHIVE_SIGNATURE,
    ZIP_LOCAL_FILE_SIGNATURE,
)
from app.services.composition_midi_import import import_midi_bytes
from app.services.composition_musicxml_import import import_musicxml_bytes
from app.services.composition_normalizer import (
    CompositionNormalizationError,
    normalize_composition_json,
)


logger = logging.getLogger(__name__)


def detect_source_format(data: bytes, *, filename: str) -> SourceFormat:
    """Content-signature detection; filename is a fallback hint only."""
    if data.startswith(MIDI_HEADER_SIGNATURE):
        return "midi"
    if data.startswith(ZIP_LOCAL_FILE_SIGNATURE) or data.startswith(ZIP_EMPTY_ARCHIVE_SIGNATURE):
        # MXL is zip; bare zip without MusicXML is rejected by the MusicXML importer.
        return "mxl"
    stripped = data.lstrip()
    if any(stripped.startswith(prefix) for prefix in XML_PROLOG_PREFIXES):
        return "musicxml"
    # JSON compositions
    try:
        text = data.decode("utf-8")
        parsed = json.loads(text)
        if isinstance(parsed, dict) and "schema_version" in parsed:
            return "composition_json"
    except (UnicodeDecodeError, json.JSONDecodeError):
        pass
    suffix = Path(filename).suffix.lower()
    if suffix in {".mid", ".midi"}:
        return "midi"
    if suffix == ".mxl":
        return "mxl"
    if suffix in {".musicxml", ".xml"}:
        return "musicxml"
    if suffix == ".json":
        return "composition_json"
    raise DatasetIngestError(
        "unsupported_source_format",
        f"Could not detect source format for {filename}",
        details={"filename": filename},
    )


def ingest_source(
    source: CataloguedSource,
    *,
    config: DatasetPipelineConfig,
    settings: DatasetSettings | None = None,
) -> DatasetItemV1:
    """Ingest one catalogued source into a normalized dataset item (no project DB)."""
    cfg = settings or load_dataset_settings()
    started = time.perf_counter()
    data = source.path.read_bytes()
    if len(data) > cfg.max_source_bytes:
        raise DatasetIngestError(
            "source_too_large",
            "Source exceeds DATASET_MAX_SOURCE_BYTES",
            details={
                "limit_bytes": cfg.max_source_bytes,
                "input_bytes": len(data),
                "source_id": source.source_id,
            },
        )

    source_bytes_hash = hashlib.sha256(data).hexdigest()
    fmt = detect_source_format(data, filename=source.display_filename)
    composition, issue_codes, issue_counts = _load_composition(
        data,
        fmt=fmt,
        display_filename=source.display_filename,
        settings=cfg,
        normalization=config.normalization,
    )
    composition, normalize_codes, normalize_counts = normalize_dataset_composition(
        composition,
        target_ppq=config.normalization.target_ppq,
        collapse_dup_notes=config.normalization.collapse_dup_notes,
    )
    for code, count in normalize_counts.items():
        issue_counts[code] = issue_counts.get(code, 0) + count
    issue_codes.extend(code for code in normalize_codes if code not in issue_codes)

    content_hash = dataset_content_hash(composition)
    item_id = _item_id(config.dataset_name, source.source_id, content_hash)
    train_eligible = compute_train_eligible(
        source.provenance,
        eligibility=config.eligibility,
        use_policy_override=source.use_policy,
        source_id=source.source_id,
    )
    instruments = [
        DatasetInstrumentSummary(
            track_id=track.id,
            name=track.name,
            role=track.role,
            program=track.midi_program,
            is_drum=bool(track.is_drum),
        )
        for track in composition.tracks
    ]
    note_count = sum(len(track.events) for track in composition.tracks)
    ingest_report = DatasetIngestIssueSummary(
        codes=issue_codes,
        counts=issue_counts,
        note_count=note_count,
        bar_count=composition.bar_count,
        track_count=len(composition.tracks),
        source_bytes=len(data) if config.retain_sources else None,
        source_format=fmt,
    )
    item = DatasetItemV1(
        item_id=item_id,
        source_id=source.source_id,
        dataset_name=config.dataset_name,
        provenance=source.provenance,
        train_eligible=train_eligible,
        use_policy=source.use_policy,  # type: ignore[arg-type]
        labels=source.labels,
        instruments=instruments,
        composition=composition if config.normalization.embed_composition_in_item else None,
        composition_blob_hash=content_hash if config.normalization.store_cas_blob else None,
        content_hash=content_hash,
        source_bytes_hash=source_bytes_hash,
        ingest_report=ingest_report,
        cluster_id=None,
        is_canonical=True,
    )
    if item.composition is None and not item.composition_blob_hash:
        # Always keep at least embedded composition for trainer simplicity when CAS off.
        item = item.model_copy(update={"composition": composition})

    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
    logger.info(
        "Dataset source ingested",
        extra={
            "source_id": source.source_id,
            "item_id": item_id,
            "format": fmt,
            "note_count": note_count,
            "bar_count": composition.bar_count,
            "provenance_status": source.provenance.status,
            "use_policy": source.use_policy,
            "train_eligible": train_eligible,
            "elapsed_ms": elapsed_ms,
            "issue_codes": issue_codes[:20],
        },
    )
    return item


def _load_composition(
    data: bytes,
    *,
    fmt: SourceFormat,
    display_filename: str,
    settings: DatasetSettings,
    normalization: Any,
) -> tuple[CompositionV2, list[str], dict[str, int]]:
    base = dataset_settings_as_import_settings(settings)
    import_settings = type(base)(
        max_upload_bytes=base.max_upload_bytes,
        max_expanded_bytes=base.max_expanded_bytes,
        max_compression_ratio=base.max_compression_ratio,
        max_archive_entries=base.max_archive_entries,
        max_tracks=base.max_tracks,
        max_notes=base.max_notes,
        max_bars=base.max_bars,
        max_ppq=base.max_ppq,
        max_metadata_changes=base.max_metadata_changes,
        max_active_notes=base.max_active_notes,
        default_target_ppq=int(normalization.target_ppq),
    )
    try:
        if fmt == "midi":
            result = import_midi_bytes(
                data,
                display_filename=display_filename,
                settings=import_settings,
            )
            codes = [issue.code for issue in result.import_report.issues]
            return result.composition, codes, _count_codes(codes)
        if fmt in {"musicxml", "mxl"}:
            result = import_musicxml_bytes(
                data,
                display_filename=display_filename,
                settings=import_settings,
            )
            codes = [issue.code for issue in result.import_report.issues]
            return result.composition, codes, _count_codes(codes)
        raw = json.loads(data.decode("utf-8"))
        composition = normalize_composition_json(raw)
        return composition, [], {}
    except CompositionImportError as exc:
        logger.warning(
            "Dataset ingest skipped/failed import",
            extra={
                "display_name": display_filename,
                "issue_code": exc.code,
                "format": fmt,
            },
        )
        raise DatasetIngestError(
            exc.code,
            exc.message,
            details={"display_name": display_filename, "format": fmt},
        ) from exc
    except CompositionNormalizationError as exc:
        raise DatasetIngestError(
            "composition_normalize_failed",
            str(exc),
            details={"display_name": display_filename, "format": fmt},
        ) from exc
    except DatasetIngestError:
        raise
    except Exception as exc:
        raise DatasetIngestError(
            "ingest_failed",
            f"Failed to ingest {display_filename}",
            details={"error_type": type(exc).__name__, "format": fmt},
        ) from exc


def _count_codes(codes: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for code in codes:
        counts[code] = counts.get(code, 0) + 1
    return counts


def _item_id(dataset_name: str, source_id: str, content_hash: str) -> str:
    material = f"{dataset_name}|{source_id}|{content_hash}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
