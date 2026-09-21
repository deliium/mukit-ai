"""Dataset collation: Composition V2 / dataset.example.v1 → LM tensors."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from app.music_transformer.errors import MusicTransformerTrainError
from app.tokenizer.encode import encode_composition
from app.tokenizer.schemas import (
    TokenizerConditioningV1,
    TokenizerConfigV1,
    default_tokenizer_config,
)
from app.tokenizer.vocab import Vocab, build_vocab


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EncodedExample:
    input_ids: list[int]
    truncated: bool
    source_basename: str


@dataclass(frozen=True)
class CollatedBatch:
    input_ids: list[list[int]]
    labels: list[list[int]]
    lengths: list[int]


def load_json_document(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def encode_file_to_ids(
    path: Path,
    *,
    config: TokenizerConfigV1 | None = None,
    conditioning: TokenizerConditioningV1 | dict[str, Any] | None = None,
    vocab: Vocab | None = None,
    max_seq_len: int,
) -> EncodedExample:
    """Encode a V2 / dataset.example JSON file; truncate to ``max_seq_len``."""
    cfg = config or default_tokenizer_config()
    active = vocab or build_vocab(cfg)
    doc = load_json_document(path)
    seq = encode_composition(doc, cfg, conditioning=conditioning, vocab=active)
    ids = list(seq.token_ids)
    truncated = False
    if len(ids) > max_seq_len:
        ids = ids[:max_seq_len]
        truncated = True
    return EncodedExample(
        input_ids=ids,
        truncated=truncated,
        source_basename=path.name,
    )


def collect_input_paths(
    *,
    dataset_dir: Path | None = None,
    inputs: Sequence[Path] | None = None,
    inputs_glob: str | None = None,
    split: str | None = None,
) -> list[Path]:
    """Collect train inputs. When ``split`` is set and dataset has ``splits/<split>.jsonl``,
    prefer those example paths; otherwise fall back to ``examples/**/*.json``.
    """
    paths: list[Path] = []
    if inputs:
        paths.extend(Path(p) for p in inputs)
    if dataset_dir is not None:
        root = Path(dataset_dir)
        split_paths = _paths_from_split_manifest(root, split) if split else []
        if split_paths:
            paths.extend(split_paths)
        else:
            examples = root / "examples"
            search_root = examples if examples.is_dir() else root
            paths.extend(sorted(search_root.rglob("*.json")))
    if inputs_glob:
        paths.extend(sorted(Path().glob(inputs_glob)))
    # de-dupe preserving order
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            continue
        seen.add(key)
        if path.is_file():
            unique.append(path)
    return unique


def _paths_from_split_manifest(dataset_dir: Path, split: str | None) -> list[Path]:
    if not split:
        return []
    split_file = Path(dataset_dir) / "splits" / f"{split}.jsonl"
    if not split_file.is_file():
        return []
    paths: list[Path] = []
    examples_dir = Path(dataset_dir) / "examples"
    try:
        for line in split_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            example_id = row.get("example_id") or row.get("id")
            rel = row.get("path") or row.get("example_path")
            if rel:
                candidate = Path(dataset_dir) / rel
                if candidate.is_file():
                    paths.append(candidate)
                    continue
            if example_id:
                candidate = examples_dir / f"{example_id}.json"
                if candidate.is_file():
                    paths.append(candidate)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "Failed reading split manifest; falling back to examples/",
            extra={"split": split, "error_type": type(exc).__name__},
        )
        return []
    logger.info(
        "Loaded split input paths",
        extra={"split": split, "path_count": len(paths), "basename": split_file.name},
    )
    return paths


def split_train_val_examples(
    examples: list[EncodedExample],
    *,
    val_fraction: float | None,
    seed: int,
) -> tuple[list[EncodedExample], list[EncodedExample]]:
    """Deterministic in-memory val split for fixtures without split manifests."""
    if not val_fraction or val_fraction <= 0 or len(examples) < 2:
        return examples, []
    import random as py_random

    rng = py_random.Random(seed)
    order = list(range(len(examples)))
    rng.shuffle(order)
    val_n = max(1, int(len(examples) * val_fraction))
    val_n = min(val_n, len(examples) - 1)
    val_idx = set(order[:val_n])
    train = [examples[i] for i in range(len(examples)) if i not in val_idx]
    val = [examples[i] for i in range(len(examples)) if i in val_idx]
    logger.info(
        "Split train/val by fraction",
        extra={
            "train_count": len(train),
            "val_count": len(val),
            "val_fraction": val_fraction,
            "seed": seed,
        },
    )
    return train, val


def count_non_pad_tokens(labels: Sequence[Sequence[int]], *, pad_id: int = 0) -> int:
    return sum(1 for row in labels for token in row if token != pad_id)


def load_encoded_corpus(
    paths: Iterable[Path],
    *,
    config: TokenizerConfigV1 | None = None,
    max_seq_len: int,
    conditioning: TokenizerConditioningV1 | dict[str, Any] | None = None,
) -> list[EncodedExample]:
    cfg = config or default_tokenizer_config()
    vocab = build_vocab(cfg)
    examples: list[EncodedExample] = []
    truncate_count = 0
    for path in paths:
        try:
            ex = encode_file_to_ids(
                path,
                config=cfg,
                conditioning=conditioning,
                vocab=vocab,
                max_seq_len=max_seq_len,
            )
        except Exception as exc:  # noqa: BLE001 — skip bad files with log
            logger.debug(
                "Skipping unreadable train input",
                extra={"basename": path.name, "error_type": type(exc).__name__},
            )
            continue
        if len(ex.input_ids) < 2:
            continue
        if ex.truncated:
            truncate_count += 1
        examples.append(ex)
    if not examples:
        raise MusicTransformerTrainError(
            "train_data_empty",
            "No encodable training sequences found",
        )
    avg_len = sum(len(e.input_ids) for e in examples) / len(examples)
    logger.info(
        "Encoded training corpus",
        extra={
            "sequence_count": len(examples),
            "avg_length": round(avg_len, 2),
            "truncate_count": truncate_count,
            "max_seq_len": max_seq_len,
        },
    )
    return examples


def pad_batch(
    sequences: Sequence[list[int]],
    *,
    pad_id: int = 0,
    max_seq_len: int | None = None,
) -> CollatedBatch:
    lengths = [len(s) for s in sequences]
    target = max(lengths) if max_seq_len is None else max_seq_len
    target = max(target, 2)
    input_ids: list[list[int]] = []
    labels: list[list[int]] = []
    for seq in sequences:
        clipped = seq[:target]
        pad_n = target - len(clipped)
        padded = clipped + [pad_id] * pad_n
        # labels: same as input for CE shift inside loss; pad stays pad
        input_ids.append(padded)
        labels.append(list(padded))
    return CollatedBatch(input_ids=input_ids, labels=labels, lengths=lengths)


def iter_batches(
    examples: Sequence[EncodedExample],
    *,
    batch_size: int,
    pad_id: int = 0,
    max_seq_len: int,
) -> Iterable[CollatedBatch]:
    for start in range(0, len(examples), batch_size):
        chunk = examples[start : start + batch_size]
        yield pad_batch(
            [e.input_ids for e in chunk],
            pad_id=pad_id,
            max_seq_len=max_seq_len,
        )


def prompt_ids_from_composition(
    doc: Any,
    *,
    config: TokenizerConfigV1 | None = None,
    conditioning: TokenizerConditioningV1 | dict[str, Any] | None = None,
    vocab: Vocab | None = None,
    drop_eos: bool = True,
) -> list[int]:
    """Encode a seed composition as a generate prefix (optionally strip trailing EOS)."""
    cfg = config or default_tokenizer_config()
    active = vocab or build_vocab(cfg)
    seq = encode_composition(doc, cfg, conditioning=conditioning, vocab=active)
    ids = list(seq.token_ids)
    if drop_eos and ids:
        eos = active.token_to_id.get("EOS")
        if eos is not None and ids[-1] == eos:
            ids = ids[:-1]
    return ids


def read_dataset_version_id(dataset_dir: Path | None) -> str | None:
    if dataset_dir is None:
        return None
    manifest = Path(dataset_dir) / "manifest.json"
    if not manifest.is_file():
        # try nested
        candidates = list(Path(dataset_dir).glob("**/manifest.json"))
        if not candidates:
            return None
        manifest = candidates[0]
    try:
        raw = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw.get("dataset_version_id") or raw.get("version_id")
