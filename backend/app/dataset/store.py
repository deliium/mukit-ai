"""Filesystem layout helpers for versioned dataset artifacts (atomic writes + CAS)."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable, Iterator

from app.dataset.errors import DatasetStoreError
from app.dataset.schemas import (
    DatasetExampleV1,
    DatasetItemV1,
    DatasetManifestV1,
    DatasetStatsV1,
    SplitJsonlRow,
    SplitName,
)


logger = logging.getLogger(__name__)


class DatasetVersionStore:
    """Read/write helpers for ``$ROOT/<name>/<version_id>/`` layout."""

    def __init__(self, version_dir: Path, *, dataset_root: Path | None = None) -> None:
        self.version_dir = Path(version_dir)
        self.dataset_root = Path(dataset_root) if dataset_root is not None else None
        self.items_dir = self.version_dir / "items"
        self.examples_dir = self.version_dir / "examples"
        self.blobs_dir = self.version_dir / "blobs" / "v2"
        self.splits_dir = self.version_dir / "splits"
        self.sources_dir = self.version_dir / "sources"
        self.rights_dir = self.version_dir / "rights"

    def ensure_layout(self) -> None:
        for path in (
            self.version_dir,
            self.items_dir,
            self.examples_dir,
            self.blobs_dir,
            self.splits_dir,
            self.sources_dir,
            self.rights_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)
        logger.debug(
            "Dataset version layout ensured",
            extra={"rel_path": self._rel(self.version_dir)},
        )

    def item_path(self, item_id: str) -> Path:
        return self.items_dir / f"{item_id}.json"

    def example_path(self, example_id: str) -> Path:
        return self.examples_dir / f"{example_id}.json"

    def blob_path(self, content_hash: str) -> Path:
        return self.blobs_dir / f"{content_hash}.json"

    def split_path(self, split: SplitName) -> Path:
        return self.splits_dir / f"{split}.jsonl"

    def write_json(self, path: Path, payload: dict[str, Any] | list[Any]) -> None:
        text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
        self._atomic_write_text(path, text)

    def write_yaml(self, path: Path, payload: dict[str, Any]) -> None:
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover - dependency should be present
            raise DatasetStoreError(
                "yaml_unavailable",
                "PyYAML is required to write config.snapshot.yaml",
                details={"error_type": type(exc).__name__},
            ) from exc
        text = yaml.safe_dump(payload, sort_keys=True, allow_unicode=False)
        self._atomic_write_text(path, text)

    def write_text(self, path: Path, text: str) -> None:
        self._atomic_write_text(path, text if text.endswith("\n") else text + "\n")

    def put_v2_blob(self, content_hash: str, composition_json: dict[str, Any]) -> Path:
        path = self.blob_path(content_hash)
        if not path.exists():
            self.write_json(path, composition_json)
            logger.debug(
                "CAS blob written",
                extra={"content_hash_prefix": content_hash[:12], "rel_path": self._rel(path)},
            )
        return path

    def get_v2_blob(self, content_hash: str) -> dict[str, Any]:
        path = self.blob_path(content_hash)
        return json.loads(path.read_text(encoding="utf-8"))

    def write_item(self, item: DatasetItemV1) -> Path:
        path = self.item_path(item.item_id)
        self.write_json(path, item.model_dump(mode="json"))
        return path

    def write_example(self, example: DatasetExampleV1) -> Path:
        path = self.example_path(example.example_id)
        self.write_json(path, example.model_dump(mode="json"))
        return path

    def write_manifest(self, manifest: DatasetManifestV1) -> Path:
        path = self.version_dir / "manifest.json"
        self.write_json(path, manifest.model_dump(mode="json"))
        logger.info(
            "Manifest written",
            extra={
                "version_id_prefix": manifest.dataset_version_id[:12],
                "item_count": manifest.counts.items,
                "example_count": manifest.counts.examples,
            },
        )
        return path

    def load_manifest(self) -> DatasetManifestV1:
        path = self.version_dir / "manifest.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        return DatasetManifestV1.model_validate(data)

    def write_stats(self, stats: DatasetStatsV1) -> Path:
        path = self.version_dir / "stats.json"
        self.write_json(path, stats.model_dump(mode="json"))
        logger.info(
            "Stats written",
            extra={
                "version_id_prefix": stats.dataset_version_id[:12],
                "inventory_items": stats.inventory.item_count,
                "eligible_items": stats.eligible.item_count,
            },
        )
        return path

    def write_build_id(self, version_id: str) -> Path:
        path = self.version_dir / "BUILD_ID"
        self.write_text(path, version_id)
        return path

    def write_split_jsonl(self, split: SplitName, rows: Iterable[SplitJsonlRow]) -> Path:
        path = self.split_path(split)
        lines = [
            json.dumps(row.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
            for row in rows
        ]
        text = ("\n".join(lines) + "\n") if lines else ""
        self._atomic_write_text(path, text)
        logger.info(
            "Split jsonl written",
            extra={"split": split, "row_count": len(lines), "rel_path": self._rel(path)},
        )
        return path

    def iter_items(self) -> Iterator[DatasetItemV1]:
        if not self.items_dir.exists():
            return
        for path in sorted(self.items_dir.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            yield DatasetItemV1.model_validate(data)

    def iter_examples(self) -> Iterator[DatasetExampleV1]:
        if not self.examples_dir.exists():
            return
        for path in sorted(self.examples_dir.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            yield DatasetExampleV1.model_validate(data)

    def load_item(self, item_id: str) -> DatasetItemV1:
        data = json.loads(self.item_path(item_id).read_text(encoding="utf-8"))
        return DatasetItemV1.model_validate(data)

    def load_example(self, example_id: str) -> DatasetExampleV1:
        data = json.loads(self.example_path(example_id).read_text(encoding="utf-8"))
        return DatasetExampleV1.model_validate(data)

    def count_items(self) -> int:
        if not self.items_dir.exists():
            return 0
        return sum(1 for _ in self.items_dir.glob("*.json"))

    def count_examples(self) -> int:
        if not self.examples_dir.exists():
            return 0
        return sum(1 for _ in self.examples_dir.glob("*.json"))

    def _atomic_write_text(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd: int | None = None
        tmp_path: Path | None = None
        try:
            fd, tmp_name = tempfile.mkstemp(
                prefix=f".{path.name}.",
                suffix=".tmp",
                dir=str(path.parent),
            )
            tmp_path = Path(tmp_name)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                fd = None
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, path)
            tmp_path = None
            logger.debug(
                "Atomic write completed",
                extra={"rel_path": self._rel(path), "bytes": len(text.encode("utf-8"))},
            )
        except OSError as exc:
            logger.error(
                "Atomic write failed",
                extra={
                    "rel_path": self._rel(path),
                    "error_type": type(exc).__name__,
                },
            )
            raise DatasetStoreError(
                "atomic_write_failed",
                f"Failed to write {path.name}",
                details={"error_type": type(exc).__name__},
            ) from exc
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            if tmp_path is not None and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    def _rel(self, path: Path) -> str:
        if self.dataset_root is not None:
            try:
                return str(path.resolve().relative_to(self.dataset_root.resolve()))
            except ValueError:
                return path.name
        return path.name
