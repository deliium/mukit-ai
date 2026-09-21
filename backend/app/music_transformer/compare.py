"""Compare experiment dirs or checkpoints (numeric / reproducibility only)."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from app.music_transformer.errors import MusicTransformerEvalError
from app.music_transformer.experiment_schemas import (
    CompareDeltaV1,
    MusicTransformerCompareReportV1,
)
from app.music_transformer.schemas import canonical_json_dumps


logger = logging.getLogger(__name__)


def compare_experiments(
    a: Path | str,
    b: Path | str,
) -> MusicTransformerCompareReportV1:
    """Compare two experiment directories or checkpoint paths.

    No musical-quality winner field — only digests, equality flags, and deltas.
    """
    side_a = _load_side(Path(a), label="a")
    side_b = _load_side(Path(b), label="b")

    configs_equal = (
        side_a.config_digest is not None
        and side_a.config_digest == side_b.config_digest
    )
    seeds_equal = (
        side_a.seed is not None and side_b.seed is not None and side_a.seed == side_b.seed
    )
    architecture_digests_equal = (
        side_a.architecture_digest is not None
        and side_a.architecture_digest == side_b.architecture_digest
    )
    tokenizer_versions_equal = (
        side_a.tokenizer_version is not None
        and side_a.tokenizer_version == side_b.tokenizer_version
        and side_a.vocab_hash == side_b.vocab_hash
    )

    metric_deltas = _scalar_deltas(side_a.metrics_summary, side_b.metrics_summary)
    eval_deltas = _eval_deltas(side_a.eval_metrics, side_b.eval_metrics)
    listening = _listening_deltas(side_a.listening, side_b.listening)

    report = MusicTransformerCompareReportV1(
        a_id=side_a.side_id,
        b_id=side_b.side_id,
        configs_equal=bool(configs_equal),
        seeds_equal=bool(seeds_equal),
        architecture_digests_equal=bool(architecture_digests_equal),
        tokenizer_versions_equal=bool(tokenizer_versions_equal),
        config_digest_a=side_a.config_digest,
        config_digest_b=side_b.config_digest,
        metric_deltas=metric_deltas,
        eval_deltas=eval_deltas,
        listening=listening,
        musical_quality_claim=False,
    )
    logger.info(
        "Compare report built",
        extra={
            "a_id": report.a_id,
            "b_id": report.b_id,
            "configs_equal": report.configs_equal,
            "seeds_equal": report.seeds_equal,
            "architecture_digests_equal": report.architecture_digests_equal,
            "tokenizer_versions_equal": report.tokenizer_versions_equal,
            "metric_delta_count": len(metric_deltas),
            "eval_delta_count": len(eval_deltas),
            "listening_delta_count": len(listening),
            "musical_quality_claim": False,
        },
    )
    missing_a = [k for k, v in (("metrics", side_a.metrics_summary), ("eval", side_a.eval_metrics)) if not v]
    missing_b = [k for k, v in (("metrics", side_b.metrics_summary), ("eval", side_b.eval_metrics)) if not v]
    if missing_a or missing_b:
        logger.warning(
            "Compare sides missing artifacts",
            extra={"missing_a": missing_a, "missing_b": missing_b},
        )
    return report


def write_compare_report(
    report: MusicTransformerCompareReportV1,
    out_path: Path | str,
) -> Path:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    logger.info(
        "Compare report written",
        extra={"basename": path.name, "a_id": report.a_id, "b_id": report.b_id},
    )
    return path


class _Side:
    __slots__ = (
        "side_id",
        "kind",
        "config_digest",
        "seed",
        "architecture_digest",
        "tokenizer_version",
        "vocab_hash",
        "metrics_summary",
        "eval_metrics",
        "listening",
    )

    def __init__(self, side_id: str, kind: str) -> None:
        self.side_id = side_id
        self.kind = kind
        self.config_digest: str | None = None
        self.seed: int | None = None
        self.architecture_digest: str | None = None
        self.tokenizer_version: str | None = None
        self.vocab_hash: str | None = None
        self.metrics_summary: dict[str, Any] = {}
        self.eval_metrics: dict[str, Any] = {}
        self.listening: dict[str, dict[str, Any]] = {}


def _load_side(path: Path, *, label: str) -> _Side:
    if not path.exists():
        raise MusicTransformerEvalError(
            "compare_path_missing",
            f"Compare path missing ({label}): {path.name}",
            details={"label": label, "basename": path.name},
        )
    if path.is_dir():
        return _load_experiment_dir(path)
    if path.is_file() and path.suffix in {".pt", ".pth"}:
        return _load_checkpoint_side(path)
    # sidecar card.json next to checkpoint, or .card.json
    if path.is_file() and path.name.endswith(".card.json"):
        return _load_card_file(path)
    raise MusicTransformerEvalError(
        "compare_path_invalid",
        f"Compare path must be experiment dir or checkpoint ({label})",
        details={"label": label, "basename": path.name},
    )


def _load_experiment_dir(root: Path) -> _Side:
    side = _Side(side_id=root.name, kind="experiment")
    meta = _read_json(root / "metadata.json")
    if meta:
        side.config_digest = meta.get("config_digest")
        side.seed = meta.get("seed")
        side.side_id = str(meta.get("experiment_id") or root.name)
        tok = meta.get("tokenizer") or {}
        if isinstance(tok, dict):
            side.tokenizer_version = tok.get("expected_tokenizer_version")
            side.vocab_hash = tok.get("vocab_hash")
    config = _read_json(root / "config.json")
    if config:
        arch = config.get("architecture") or {}
        if isinstance(arch, dict) and arch:
            # digest may be recomputed by caller; prefer metadata.config_digest
            pass
        if side.seed is None:
            side.seed = config.get("seed")
        if side.config_digest is None:
            # fall back to hashing frozen config without wall-clock fields
            payload = dict(config)
            for key in ("created_at", "git_commit", "project_version"):
                payload.pop(key, None)
            side.config_digest = hashlib.sha256(
                canonical_json_dumps(payload).encode("utf-8")
            ).hexdigest()
        train = config.get("train") or {}
        if isinstance(train, dict) and side.seed is None:
            side.seed = train.get("seed")
        tok_exp = config.get("tokenizer_expectation") or {}
        if isinstance(tok_exp, dict):
            side.tokenizer_version = side.tokenizer_version or tok_exp.get(
                "expected_tokenizer_version"
            )
            side.vocab_hash = side.vocab_hash or tok_exp.get("vocab_hash")
        arch = config.get("architecture")
        if isinstance(arch, dict):
            try:
                from app.music_transformer.schemas import MusicTransformerConfigV1

                side.architecture_digest = MusicTransformerConfigV1.model_validate(
                    arch
                ).config_digest()
            except Exception:  # noqa: BLE001
                side.architecture_digest = hashlib.sha256(
                    canonical_json_dumps(arch).encode("utf-8")
                ).hexdigest()

    summary = _read_json(root / "metrics_summary.json")
    if summary:
        side.metrics_summary = {
            k: summary[k]
            for k in (
                "final_step",
                "final_train_loss",
                "best_val_loss",
                "best_step",
                "total_tokens",
                "wall_time_sec",
                "early_stopped",
                "row_count",
            )
            if k in summary
        }

    eval_latest = _read_json(root / "eval" / "latest.json")
    if eval_latest:
        side.eval_metrics = _flatten_eval_metrics(eval_latest)

    listening_dir = root / "listening"
    if listening_dir.is_dir():
        side.listening = _listening_digests(listening_dir)

    # Prefer architecture digest from a checkpoint card when present
    latest_card = root / "checkpoints" / "latest.pt.card.json"
    if not latest_card.is_file():
        # alternate naming
        for cand in (root / "checkpoints").glob("*.card.json") if (root / "checkpoints").is_dir() else []:
            latest_card = cand
            break
    if latest_card.is_file():
        card = _read_json(latest_card)
        if card:
            side.architecture_digest = card.get("architecture_digest") or side.architecture_digest
            tok = card.get("tokenizer") or {}
            if isinstance(tok, dict):
                side.tokenizer_version = side.tokenizer_version or tok.get(
                    "expected_tokenizer_version"
                )
                side.vocab_hash = side.vocab_hash or tok.get("vocab_hash")
            training = card.get("training") or {}
            if isinstance(training, dict) and side.seed is None:
                side.seed = training.get("seed")

    return side


def _load_checkpoint_side(path: Path) -> _Side:
    side = _Side(side_id=path.stem, kind="checkpoint")
    card_path = path.with_suffix(path.suffix + ".card.json")
    if not card_path.is_file():
        # try sibling .card.json pattern used by save_checkpoint
        alt = Path(str(path) + ".card.json")
        if alt.is_file():
            card_path = alt
        else:
            # load embedded card via torch (lazy)
            try:
                from app.music_transformer.checkpoint import load_checkpoint

                _payload, card = load_checkpoint(
                    path,
                    map_location="cpu",
                    require_tokenizer_version=False,
                )
                side.architecture_digest = card.architecture_digest
                side.tokenizer_version = card.tokenizer.expected_tokenizer_version
                side.vocab_hash = card.tokenizer.vocab_hash
                side.seed = card.training.seed
                side.side_id = card.training.experiment_id or path.stem
                side.config_digest = card.architecture_digest
                return side
            except Exception as exc:  # noqa: BLE001
                raise MusicTransformerEvalError(
                    "compare_checkpoint_unreadable",
                    f"Could not read checkpoint card: {path.name}",
                    details={"error_type": type(exc).__name__},
                ) from exc
    return _load_card_file(card_path, side_id=path.stem)


def _load_card_file(path: Path, *, side_id: str | None = None) -> _Side:
    card = _read_json(path)
    if not card:
        raise MusicTransformerEvalError(
            "compare_card_invalid",
            f"Checkpoint card unreadable: {path.name}",
        )
    side = _Side(side_id=side_id or path.stem, kind="checkpoint")
    side.architecture_digest = card.get("architecture_digest")
    tok = card.get("tokenizer") or {}
    if isinstance(tok, dict):
        side.tokenizer_version = tok.get("expected_tokenizer_version")
        side.vocab_hash = tok.get("vocab_hash")
    training = card.get("training") or {}
    if isinstance(training, dict):
        side.seed = training.get("seed")
        if training.get("experiment_id"):
            side.side_id = str(training["experiment_id"])
    # Checkpoint-only compare: config digest falls back to architecture digest
    side.config_digest = side.architecture_digest
    return side


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "Failed reading compare artifact",
            extra={"basename": path.name, "error_type": type(exc).__name__},
        )
        return None
    return raw if isinstance(raw, dict) else None


def _flatten_eval_metrics(eval_report: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "sample_count" in eval_report:
        out["sample_count"] = eval_report["sample_count"]
    for item in eval_report.get("metrics") or []:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not name:
            continue
        value = item.get("value")
        status = item.get("status")
        if isinstance(value, (int, float)):
            out[str(name)] = value
        elif isinstance(value, dict):
            if "violation_count" in value:
                out[f"{name}.violation_count"] = value["violation_count"]
            if "mean_notes_per_bar" in value:
                out[f"{name}.mean_notes_per_bar"] = value["mean_notes_per_bar"]
            if "normalized" in value and isinstance(value["normalized"], dict):
                # skip huge histograms in compare — only scalar summaries
                pass
        out[f"{name}.status"] = status
    return out


def _listening_digests(listening_dir: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(listening_dir.glob("*.json")):
        if path.name.endswith(".report.json"):
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        # Skip non-composition files
        if raw.get("schema_version") != "composition.v2" and "tracks" not in raw:
            continue
        digest = hashlib.sha256(
            canonical_json_dumps(raw).encode("utf-8")
        ).hexdigest()
        notes = 0
        for track in raw.get("tracks") or []:
            if isinstance(track, dict):
                notes += len(track.get("events") or [])
        out[path.stem] = {
            "digest": digest,
            "notes_out": notes,
            "basename": path.name,
        }
    return out


def _scalar_deltas(
    a: dict[str, Any],
    b: dict[str, Any],
) -> list[CompareDeltaV1]:
    keys = sorted(set(a) | set(b))
    deltas: list[CompareDeltaV1] = []
    for key in keys:
        va, vb = a.get(key), b.get(key)
        delta = None
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
            delta = float(vb) - float(va)
        deltas.append(CompareDeltaV1(key=key, a=va, b=vb, delta=delta))
    return deltas


def _eval_deltas(
    a: dict[str, Any],
    b: dict[str, Any],
) -> list[CompareDeltaV1]:
    return _scalar_deltas(a, b)


def _listening_deltas(
    a: dict[str, dict[str, Any]],
    b: dict[str, dict[str, Any]],
) -> list[CompareDeltaV1]:
    keys = sorted(set(a) | set(b))
    deltas: list[CompareDeltaV1] = []
    for key in keys:
        la, lb = a.get(key), b.get(key)
        digest_a = None if la is None else la.get("digest")
        digest_b = None if lb is None else lb.get("digest")
        notes_a = None if la is None else la.get("notes_out")
        notes_b = None if lb is None else lb.get("notes_out")
        deltas.append(
            CompareDeltaV1(
                key=f"{key}.digest",
                a=digest_a,
                b=digest_b,
                delta=None,
            )
        )
        note_delta = None
        if isinstance(notes_a, (int, float)) and isinstance(notes_b, (int, float)):
            note_delta = float(notes_b) - float(notes_a)
        deltas.append(
            CompareDeltaV1(
                key=f"{key}.notes_out",
                a=notes_a,
                b=notes_b,
                delta=note_delta,
            )
        )
    return deltas
