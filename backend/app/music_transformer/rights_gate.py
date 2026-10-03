"""Hard-fail rights verification for Music Transformer train inputs.

Reads dataset-version ``rights/`` siblings. Does not import FastAPI or SQLite.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from app.dataset.rights_index import load_rights_index, load_rights_manifest
from app.music_transformer.errors import MusicTransformerTrainError
from app.rights_governance_schemas import (
    ModelDataProvenanceManifestV1,
    ModelDataProvenanceSourceV1,
    rights_digest_prefix,
)
from app.services.rights_governance_policy import evaluate_rights_use

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parent_item_id_for_example(path: Path) -> str | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    parent = data.get("parent_item_id") or data.get("item_id")
    if isinstance(parent, str) and parent.strip():
        return parent.strip()
    return None


def verify_train_paths_against_rights(
    dataset_dir: Path,
    train_paths: list[Path],
) -> ModelDataProvenanceManifestV1:
    """Refuse when any train path's parent item is not train-eligible.

    When ``rights/index.jsonl`` is missing under a dataset version dir that has
    ``manifest.json``, fail closed. Fixture-only inputs without a dataset dir
    skip this helper (caller gates on ``dataset_dir is not None``).
    """
    root = Path(dataset_dir)
    index = load_rights_index(root)
    has_dataset_manifest = (root / "manifest.json").is_file()
    if not index:
        if has_dataset_manifest or (root / "splits").is_dir():
            logger.error(
                "Music Transformer train rights refused",
                extra={"code": "rights_train_refused", "reason": "missing_rights_index"},
            )
            raise MusicTransformerTrainError(
                "rights_train_refused",
                "Dataset rights index is required for train inputs",
                details={"dataset_dir": root.name},
            )
        # Bare example folder — nothing to verify.
        return ModelDataProvenanceManifestV1(
            manifest_kind="music_transformer_train",
            subject_id=root.name,
            sources=[],
            excluded_source_counts_by_use_policy={},
            created_at=_utc_now_iso(),
        )

    by_item = {entry.source_id: entry for entry in index}
    eligible_ids: set[str] = set()
    for entry in index:
        if evaluate_rights_use(entry, "train").allowed:
            eligible_ids.add(entry.source_id)

    poisoned: list[str] = []
    used_item_ids: set[str] = set()
    for path in train_paths:
        parent = _parent_item_id_for_example(path)
        if parent is None:
            # Split path without readable parent — try basename stem against items.
            parent = path.stem
        used_item_ids.add(parent)
        entry = by_item.get(parent)
        if entry is None or parent not in eligible_ids:
            poisoned.append(parent)

    if poisoned:
        logger.error(
            "Music Transformer train rights refused",
            extra={
                "code": "rights_train_refused",
                "poisoned_count": len(set(poisoned)),
                "poisoned_prefixes": sorted({p[:12] for p in poisoned})[:8],
            },
        )
        raise MusicTransformerTrainError(
            "rights_train_refused",
            "Train inputs include rights-ineligible sources",
            details={"poisoned_count": len(set(poisoned))},
        )

    sources: list[ModelDataProvenanceSourceV1] = []
    for item_id in sorted(used_item_ids & eligible_ids):
        entry = by_item[item_id]
        sources.append(
            ModelDataProvenanceSourceV1(
                entry_id=entry.entry_id,
                source_kind=entry.source_kind,
                source_id=entry.source_id,
                ownership_class=entry.ownership_class,
                use_policy="training_allowed",
                rights_digest_prefix=rights_digest_prefix(entry.rights_digest),
            )
        )

    existing = load_rights_manifest(root)
    excluded = (
        dict(existing.excluded_source_counts_by_use_policy)
        if existing is not None
        else {}
    )
    version_id = read_subject_id(root)
    logger.info(
        "Music Transformer train rights verified",
        extra={
            "train_path_count": len(train_paths),
            "eligible_source_count": len(sources),
            "index_count": len(index),
        },
    )
    return ModelDataProvenanceManifestV1(
        manifest_kind="music_transformer_train",
        subject_id=version_id,
        sources=sources,
        excluded_source_counts_by_use_policy=excluded,
        created_at=_utc_now_iso(),
    )


def read_subject_id(dataset_dir: Path) -> str:
    manifest = Path(dataset_dir) / "manifest.json"
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            version = data.get("dataset_version_id")
            if isinstance(version, str) and version:
                return version
        except (OSError, json.JSONDecodeError):
            pass
    return Path(dataset_dir).name


def write_model_data_manifest(path: Path, manifest: ModelDataProvenanceManifestV1) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    logger.info(
        "Model data provenance manifest written",
        extra={
            "manifest_kind": manifest.manifest_kind,
            "source_count": len(manifest.sources),
            "basename": path.name,
        },
    )
    return path
