"""Aggregate ``tokenizer.stats.v1`` over compositions or dataset version dirs."""

from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from app.composition_schemas import CompositionV2
from app.tokenizer.encode import encode_composition
from app.tokenizer.errors import TokenizerIOError
from app.tokenizer.schemas import (
    TOKENIZER_VERSION,
    TokenizerConfigV1,
    TokenizerStatsV1,
    default_tokenizer_config,
)
from app.tokenizer.vocab import build_vocab


logger = logging.getLogger(__name__)


def compute_stats(
    compositions: Iterable[CompositionV2 | dict[str, Any]],
    config: TokenizerConfigV1 | None = None,
) -> TokenizerStatsV1:
    cfg = config or default_tokenizer_config()
    vocab = build_vocab(cfg)

    sequences = 0
    max_seq = 0
    tokens_per_bar: list[float] = []
    hist_tokens: Counter[str] = Counter()
    hist_notes: Counter[str] = Counter()
    snapped_onset = 0
    snapped_duration = 0
    dropped_invalid = 0
    omitted_expressive = 0
    notes_in_total = 0
    invalid_total = 0

    for doc in compositions:
        try:
            seq = encode_composition(doc, cfg, vocab=vocab, include_token_strs=False)
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Stats encode skipped",
                extra={"error_type": type(exc).__name__},
            )
            continue
        sequences += 1
        max_seq = max(max_seq, len(seq.token_ids))
        report = seq.encode_report
        bars = max(1, report.bars_emitted if report else 1)
        tpb = len(seq.token_ids) / bars
        tokens_per_bar.append(tpb)
        bucket = _hist_bucket(tpb)
        hist_tokens[bucket] += 1
        notes_emitted = report.notes_emitted if report else 0
        npb = notes_emitted / bars
        hist_notes[_hist_bucket(npb)] += 1
        if report:
            snapped_onset += report.snapped_onset
            snapped_duration += report.snapped_duration
            dropped_invalid += report.dropped_invalid
            omitted_expressive += report.omitted_expressive
            notes_in_total += report.notes_in
            invalid_total += report.dropped_invalid

    avg_tpb = sum(tokens_per_bar) / len(tokens_per_bar) if tokens_per_bar else 0.0
    invalid_rate = invalid_total / max(1, notes_in_total)

    stats = TokenizerStatsV1(
        tokenizer_version=TOKENIZER_VERSION,
        profile=cfg.profile,
        vocab_size=vocab.size,
        sequences=sequences,
        avg_tokens_per_bar=round(avg_tpb, 4),
        max_sequence_length=max_seq,
        unknown_or_invalid_event_rate=round(invalid_rate, 6),
        tokens_per_bar_histogram=dict(sorted(hist_tokens.items())),
        notes_per_bar_histogram=dict(sorted(hist_notes.items())),
        snapped_onset_total=snapped_onset,
        snapped_duration_total=snapped_duration,
        dropped_invalid_total=dropped_invalid,
        omitted_expressive_total=omitted_expressive,
    )
    logger.info(
        "Tokenizer stats computed",
        extra={
            "sequences": sequences,
            "vocab_size": vocab.size,
            "avg_tokens_per_bar": stats.avg_tokens_per_bar,
            "max_sequence_length": max_seq,
            "unknown_or_invalid_event_rate": stats.unknown_or_invalid_event_rate,
            "profile": cfg.profile,
            "tokenizer_version": TOKENIZER_VERSION,
        },
    )
    return stats


def compute_stats_from_paths(
    paths: Iterable[Path],
    config: TokenizerConfigV1 | None = None,
) -> TokenizerStatsV1:
    docs: list[Any] = []
    for path in paths:
        path = Path(path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            docs.append(raw)
        except (OSError, json.JSONDecodeError) as exc:
            logger.debug(
                "Stats input skipped",
                extra={"basename": path.name, "error_type": type(exc).__name__},
            )
    return compute_stats(docs, config)


def compute_stats_from_dataset_dir(
    dataset_dir: Path,
    config: TokenizerConfigV1 | None = None,
) -> TokenizerStatsV1:
    """Read examples from a versioned dataset directory (read-only)."""
    dataset_dir = Path(dataset_dir)
    examples_dir = dataset_dir / "examples"
    if not examples_dir.is_dir():
        raise TokenizerIOError(
            "examples_missing",
            "dataset version dir has no examples/",
            details={"basename": dataset_dir.name},
        )
    paths = sorted(examples_dir.glob("*.json"))
    logger.info(
        "Stats scanning dataset examples",
        extra={"basename": dataset_dir.name, "example_file_count": len(paths)},
    )
    return compute_stats_from_paths(paths, config)


def _hist_bucket(value: float) -> str:
    if value < 8:
        return "0-7"
    if value < 16:
        return "8-15"
    if value < 32:
        return "16-31"
    if value < 64:
        return "32-63"
    if value < 128:
        return "64-127"
    return "128+"
