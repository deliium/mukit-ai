"""Read-only dataset catalog and closed Lab presets.

Never writes ``DATASET_ROOT``. Never builds corpora. Payload refuse is
centralized here for service-layer callers (schemas also enforce on DTOs).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Mapping

from app.model_lab_schemas import (
    ModelLabArchitecturePresetV1,
    ModelLabDatasetCatalogEntryV1,
    ModelLabError,
    ModelLabPresetsV1,
    ModelLabTokenizerPresetV1,
    reject_model_lab_payload,
)
from app.music_transformer.schemas import tiny_test_config
from app.tokenizer.settings import TOKENIZER_VERSION

logger = logging.getLogger(__name__)

LAB_FIXTURE_TINY_ID = "lab_fixture:tiny"
_DEFAULT_VOCAB_SIZE = 1075


def refuse_lab_payload(payload: Any) -> None:
    """Service-boundary wrapper for shell / note / path refuse."""
    reject_model_lab_payload(payload)


def _dataset_root(env: Mapping[str, str] | None = None) -> Path | None:
    source = env if env is not None else os.environ
    raw = source.get("DATASET_ROOT")
    if raw is None or not str(raw).strip():
        return None
    return Path(str(raw).strip()).expanduser()


def _is_under(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (OSError, ValueError):
        return False


def _load_manifest(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.debug(
            "Model Lab catalog skipped unreadable manifest",
            extra={"field_name": "manifest.json", "code": "model_lab_manifest_unreadable"},
        )
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def list_dataset_catalog(
    *,
    env: Mapping[str, str] | None = None,
    include_lab_fixture: bool = False,
) -> list[ModelLabDatasetCatalogEntryV1]:
    """Scan ``DATASET_ROOT/<name>/<version_id>/manifest.json`` (read-only)."""
    root = _dataset_root(env)
    entries: list[ModelLabDatasetCatalogEntryV1] = []
    if root is not None and root.is_dir():
        for name_dir in sorted(root.iterdir()):
            if not name_dir.is_dir() or name_dir.name.startswith("."):
                continue
            for version_dir in sorted(name_dir.iterdir()):
                if not version_dir.is_dir():
                    continue
                manifest_path = version_dir / "manifest.json"
                if not manifest_path.is_file():
                    continue
                payload = _load_manifest(manifest_path)
                if payload is None:
                    continue
                version_id = str(payload.get("dataset_version_id") or version_dir.name)
                if version_id != version_dir.name:
                    logger.debug(
                        "Model Lab catalog version mismatch skipped",
                        extra={
                            "dataset_name": name_dir.name,
                            "code": "model_lab_dataset_version_mismatch",
                        },
                    )
                    continue
                counts = payload.get("counts") if isinstance(payload.get("counts"), dict) else {}
                entries.append(
                    ModelLabDatasetCatalogEntryV1(
                        dataset_name=str(payload.get("dataset_name") or name_dir.name),
                        dataset_version_id=version_id,
                        item_count=int(counts.get("items") or 0),
                        example_count=int(counts.get("examples") or 0),
                        train_eligible_items=int(counts.get("train_eligible_items") or 0),
                        has_rights_index=(version_dir / "rights" / "index.jsonl").is_file(),
                        lab_fixture=False,
                    )
                )
    if include_lab_fixture:
        entries.append(
            ModelLabDatasetCatalogEntryV1(
                dataset_name="lab_fixture_tiny",
                dataset_version_id=LAB_FIXTURE_TINY_ID,
                item_count=1,
                example_count=1,
                train_eligible_items=1,
                has_rights_index=True,
                lab_fixture=True,
            )
        )
    logger.debug(
        "Model Lab catalog listed",
        extra={"count": len(entries)},
    )
    return entries


def resolve_dataset_dir(
    dataset_version_id: str,
    *,
    env: Mapping[str, str] | None = None,
    lab_fixture_root: Path | None = None,
) -> tuple[Path, ModelLabDatasetCatalogEntryV1]:
    """Resolve a catalog id to a real directory under the dataset root.

    Path escape / non-child paths raise ``model_lab_dataset_refused``.
    """
    text = dataset_version_id.strip()
    if not text:
        raise ModelLabError(
            "model_lab_dataset_refused",
            "Dataset version id is required.",
            http_status=422,
        )
    if text == LAB_FIXTURE_TINY_ID:
        if lab_fixture_root is None:
            raise ModelLabError(
                "model_lab_dataset_refused",
                "Lab fixture dataset is not available.",
                http_status=422,
                details={"dataset_version_id": text},
            )
        fixture_dir = Path(lab_fixture_root).resolve()
        if not (fixture_dir / "manifest.json").is_file():
            raise ModelLabError(
                "model_lab_dataset_refused",
                "Lab fixture manifest is missing.",
                http_status=422,
                details={"dataset_version_id": text},
            )
        entry = ModelLabDatasetCatalogEntryV1(
            dataset_name="lab_fixture_tiny",
            dataset_version_id=LAB_FIXTURE_TINY_ID,
            item_count=1,
            example_count=1,
            train_eligible_items=1,
            has_rights_index=(fixture_dir / "rights" / "index.jsonl").is_file(),
            lab_fixture=True,
        )
        logger.debug(
            "Model Lab dataset resolved",
            extra={"dataset_name": entry.dataset_name, "lab_fixture": True},
        )
        return fixture_dir, entry

    root = _dataset_root(env)
    if root is None:
        raise ModelLabError(
            "model_lab_dataset_refused",
            "DATASET_ROOT is not configured.",
            http_status=422,
        )
    root_resolved = root.resolve()
    # Reject absolute / parent-escape attempts before walk.
    if text.startswith("/") or ".." in Path(text).parts or "\\" in text:
        raise ModelLabError(
            "model_lab_dataset_refused",
            "Dataset version path escape refused.",
            http_status=422,
            details={"dataset_version_id": text[:64]},
        )

    for entry in list_dataset_catalog(env=env, include_lab_fixture=False):
        if entry.dataset_version_id != text:
            continue
        candidate = (root_resolved / entry.dataset_name / text).resolve()
        if not _is_under(candidate, root_resolved):
            raise ModelLabError(
                "model_lab_dataset_refused",
                "Dataset version path escape refused.",
                http_status=422,
            )
        if not (candidate / "manifest.json").is_file():
            raise ModelLabError(
                "model_lab_dataset_refused",
                "Dataset version manifest is missing.",
                http_status=422,
            )
        logger.debug(
            "Model Lab dataset resolved",
            extra={"dataset_name": entry.dataset_name, "lab_fixture": False},
        )
        return candidate, entry

    raise ModelLabError(
        "model_lab_dataset_refused",
        "Dataset version was not found under DATASET_ROOT.",
        http_status=404,
        details={"dataset_version_id": text[:64]},
    )


def tokenizer_preset(preset_id: str) -> ModelLabTokenizerPresetV1:
    if preset_id not in ("core", "core_harmony"):
        raise ModelLabError(
            "model_lab_payload_refused",
            "Unknown tokenizer preset.",
            http_status=422,
            details={"field_name": "tokenizer_preset"},
        )
    return ModelLabTokenizerPresetV1(
        preset_id=preset_id,  # type: ignore[arg-type]
        profile=preset_id,  # type: ignore[arg-type]
        emit_harmony=preset_id == "core_harmony",
        tokenizer_version=TOKENIZER_VERSION,
    )


def architecture_preset(
    preset_id: str,
    *,
    vocab_size: int = _DEFAULT_VOCAB_SIZE,
    n_layers: int | None = None,
    d_model: int | None = None,
    max_seq_len: int | None = None,
) -> ModelLabArchitecturePresetV1:
    if preset_id == "tiny_lab":
        cfg = tiny_test_config(vocab_size=vocab_size)
        layers = n_layers if n_layers is not None else cfg.n_layers
        model_dim = d_model if d_model is not None else cfg.d_model
        seq = max_seq_len if max_seq_len is not None else cfg.max_seq_len
        # Lab-tightened ceilings for tiny_lab edits.
        layers = min(max(layers, 1), 4)
        model_dim = min(max(model_dim, 16), 128)
        seq = min(max(seq, 8), 256)
        heads = 2 if model_dim % 2 == 0 else 1
        while heads > 1 and model_dim % heads != 0:
            heads -= 1
        return ModelLabArchitecturePresetV1(
            preset_id="tiny_lab",
            n_layers=layers,
            d_model=model_dim,
            n_heads=heads,
            d_ff=max(model_dim * 2, 32),
            max_seq_len=seq,
            dropout=0.0,
        )
    if preset_id == "small_lab":
        layers = n_layers if n_layers is not None else 4
        model_dim = d_model if d_model is not None else 128
        seq = max_seq_len if max_seq_len is not None else 256
        layers = min(max(layers, 1), 8)
        model_dim = min(max(model_dim, 32), 256)
        seq = min(max(seq, 8), 512)
        heads = 4
        while heads > 1 and model_dim % heads != 0:
            heads -= 1
        return ModelLabArchitecturePresetV1(
            preset_id="small_lab",
            n_layers=layers,
            d_model=model_dim,
            n_heads=max(heads, 1),
            d_ff=model_dim * 4,
            max_seq_len=seq,
            dropout=0.1,
        )
    raise ModelLabError(
        "model_lab_payload_refused",
        "Unknown architecture preset.",
        http_status=422,
        details={"field_name": "architecture_preset"},
    )


def list_presets() -> ModelLabPresetsV1:
    """Closed tokenizer + architecture presets for the Lab wizard."""
    presets = ModelLabPresetsV1(
        tokenizer=[tokenizer_preset("core"), tokenizer_preset("core_harmony")],
        architecture=[architecture_preset("tiny_lab"), architecture_preset("small_lab")],
    )
    logger.debug(
        "Model Lab presets listed",
        extra={
            "tokenizer_count": len(presets.tokenizer),
            "architecture_count": len(presets.architecture),
        },
    )
    return presets
