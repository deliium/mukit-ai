"""Append-only train/val metrics recorder (JSONL + summary)."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from app.music_transformer.experiment_schemas import (
    MusicTransformerMetricsRowV1,
    MusicTransformerMetricsSummaryV1,
)


logger = logging.getLogger(__name__)


class MetricsRecorder:
    def __init__(
        self,
        *,
        experiment_id: str,
        metrics_jsonl: Path,
        metrics_summary: Path,
    ) -> None:
        self.experiment_id = experiment_id
        self.metrics_jsonl = Path(metrics_jsonl)
        self.metrics_summary = Path(metrics_summary)
        self._started = time.perf_counter()
        self._total_tokens = 0
        self._row_count = 0
        self._best_val_loss: float | None = None
        self._best_step: int | None = None
        self._final_train_loss: float | None = None
        self._final_step = 0
        self._early_stopped = False
        self.metrics_jsonl.parent.mkdir(parents=True, exist_ok=True)
        if not self.metrics_jsonl.exists():
            self.metrics_jsonl.write_text("", encoding="utf-8")

    def record(
        self,
        *,
        global_step: int,
        split: str = "train",
        loss: float | None = None,
        val_loss: float | None = None,
        token_accuracy: float | None = None,
        lr: float | None = None,
        tokens_processed: int = 0,
        elapsed_sec: float | None = None,
        mem_mb: float | None = None,
        epoch: int | None = None,
        notes: dict[str, Any] | None = None,
    ) -> MusicTransformerMetricsRowV1:
        tokens_per_sec = None
        if tokens_processed > 0 and elapsed_sec and elapsed_sec > 0:
            tokens_per_sec = float(tokens_processed) / float(elapsed_sec)
        self._total_tokens += max(0, tokens_processed)
        if loss is not None and split == "train":
            self._final_train_loss = loss
        if val_loss is not None:
            if self._best_val_loss is None or val_loss < self._best_val_loss:
                self._best_val_loss = val_loss
                self._best_step = global_step
        self._final_step = global_step
        row = MusicTransformerMetricsRowV1(
            experiment_id=self.experiment_id,
            global_step=global_step,
            epoch=epoch,
            split=split,  # type: ignore[arg-type]
            loss=loss,
            val_loss=val_loss,
            token_accuracy=token_accuracy,
            lr=lr,
            tokens_per_sec=tokens_per_sec,
            mem_mb=mem_mb,
            notes=notes or {},
        )
        with self.metrics_jsonl.open("a", encoding="utf-8") as handle:
            handle.write(row.model_dump_json() + "\n")
        self._row_count += 1
        logger.info(
            "Metrics snapshot",
            extra={
                "experiment_id": self.experiment_id,
                "global_step": global_step,
                "split": split,
                "loss": None if loss is None else round(loss, 6),
                "val_loss": None if val_loss is None else round(val_loss, 6),
                "lr": None if lr is None else round(lr, 8),
                "tokens_per_sec": None
                if tokens_per_sec is None
                else round(tokens_per_sec, 2),
                "mem_mb": None if mem_mb is None else round(mem_mb, 2),
            },
        )
        return row

    def mark_early_stopped(self) -> None:
        self._early_stopped = True

    def finalize(self) -> MusicTransformerMetricsSummaryV1:
        wall = time.perf_counter() - self._started
        summary = MusicTransformerMetricsSummaryV1(
            experiment_id=self.experiment_id,
            final_step=self._final_step,
            final_train_loss=self._final_train_loss,
            best_val_loss=self._best_val_loss,
            best_step=self._best_step,
            total_tokens=self._total_tokens,
            wall_time_sec=wall,
            early_stopped=self._early_stopped,
            row_count=self._row_count,
        )
        self.metrics_summary.write_text(
            json.dumps(summary.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        logger.info(
            "Metrics summary written",
            extra={
                "experiment_id": self.experiment_id,
                "final_step": self._final_step,
                "row_count": self._row_count,
                "early_stopped": self._early_stopped,
            },
        )
        return summary


def measure_memory_mb(device: str) -> float | None:
    """CUDA max allocated when available; else best-effort RSS; else None."""
    try:
        import torch

        if str(device).startswith("cuda") and torch.cuda.is_available():
            return float(torch.cuda.max_memory_allocated()) / (1024.0 * 1024.0)
    except Exception:  # noqa: BLE001
        pass
    try:
        import resource

        # ru_maxrss is KB on Linux
        return float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) / 1024.0
    except Exception:  # noqa: BLE001
        logger.warning("Memory measurement unavailable", extra={"device": device})
        return None
