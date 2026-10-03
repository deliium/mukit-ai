"""Dataset-version rights siblings (``rights/index.jsonl`` + ``rights/manifest.json``).

Hard-locked layout under the dataset version dir. Digests are not folded into
``dataset.manifest.v1`` (keeps ``dataset_version_id`` bit-stable). Does not
import ``dataset.pipeline`` (callers invoke from the orchestrator).
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.dataset.schemas import DatasetItemV1
from app.rights_governance_schemas import (
    ModelDataProvenanceManifestV1,
    ModelDataProvenanceSourceV1,
    RightsRegistryEntryV1,
    map_legacy_dataset_provenance,
    rights_digest_prefix,
)

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def item_to_rights_entry(item: DatasetItemV1) -> RightsRegistryEntryV1:
    """Map a dataset item (+ optional ``use_policy``) into a registry entry."""
    use_policy = getattr(item, "use_policy", None)
    return map_legacy_dataset_provenance(
        item.provenance,
        use_policy_override=use_policy,
        source_kind="dataset_item",
        source_id=item.item_id,
        source_fingerprint_prefix=(item.content_hash or "")[:40] or None,
    )


def build_rights_index_rows(items: list[DatasetItemV1]) -> list[dict[str, Any]]:
    """Serialize all inventory entries for ``rights/index.jsonl``."""
    rows: list[dict[str, Any]] = []
    for item in sorted(items, key=lambda row: row.item_id):
        entry = item_to_rights_entry(item)
        rows.append(entry.model_dump(mode="json"))
    return rows


def build_model_data_manifest_for_dataset(
    items: list[DatasetItemV1],
    *,
    dataset_version_id: str,
) -> ModelDataProvenanceManifestV1:
    """Train-eligible sources only + excluded counts by use_policy."""
    sources: list[ModelDataProvenanceSourceV1] = []
    excluded: Counter[str] = Counter()
    for item in items:
        entry = item_to_rights_entry(item)
        if item.train_eligible and entry.use_policy == "training_allowed":
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
        else:
            excluded[entry.use_policy] += 1
    return ModelDataProvenanceManifestV1(
        manifest_kind="dataset_train_split",
        subject_id=dataset_version_id,
        sources=sources,
        excluded_source_counts_by_use_policy=dict(sorted(excluded.items())),
        created_at=_utc_now_iso(),
    )


def write_rights_siblings(
    version_dir: Path,
    items: list[DatasetItemV1],
    *,
    dataset_version_id: str,
) -> tuple[Path, Path]:
    """Always write ``rights/index.jsonl`` and ``rights/manifest.json``."""
    rights_dir = Path(version_dir) / "rights"
    rights_dir.mkdir(parents=True, exist_ok=True)

    index_rows = build_rights_index_rows(items)
    index_path = rights_dir / "index.jsonl"
    lines = [
        json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        for row in index_rows
    ]
    index_path.write_text(("\n".join(lines) + "\n") if lines else "", encoding="utf-8")

    manifest = build_model_data_manifest_for_dataset(
        items,
        dataset_version_id=dataset_version_id,
    )
    manifest_path = rights_dir / "manifest.json"
    payload = manifest.model_dump(mode="json")
    manifest_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )

    by_policy = Counter(str(row.get("use_policy")) for row in index_rows)
    logger.info(
        "Dataset rights siblings written",
        extra={
            "version_id_prefix": dataset_version_id[:12],
            "index_count": len(index_rows),
            "train_source_count": len(manifest.sources),
            "eligibility_by_use_policy": dict(sorted(by_policy.items())),
            "excluded_source_counts_by_use_policy": dict(
                sorted(manifest.excluded_source_counts_by_use_policy.items())
            ),
        },
    )
    return index_path, manifest_path


def load_rights_index(version_dir: Path) -> list[RightsRegistryEntryV1]:
    """Load ``rights/index.jsonl`` entries (empty list if missing)."""
    path = Path(version_dir) / "rights" / "index.jsonl"
    if not path.is_file():
        return []
    entries: list[RightsRegistryEntryV1] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        data = json.loads(text)
        entries.append(RightsRegistryEntryV1.model_validate(data))
    return entries


def load_rights_manifest(version_dir: Path) -> ModelDataProvenanceManifestV1 | None:
    path = Path(version_dir) / "rights" / "manifest.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return ModelDataProvenanceManifestV1.model_validate(data)
