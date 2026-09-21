"""Filesystem-backed experiment run directories (never ``PROJECT_DB_PATH``)."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.music_transformer.errors import MusicTransformerExperimentError, MusicTransformerIOError
from app.music_transformer.experiment_schemas import MusicTransformerExperimentV1
from app.music_transformer.settings import load_music_transformer_settings
from app.music_transformer.version_info import capture_git_commit, capture_project_version
from app.tokenizer.schemas import TokenizerConfigV1, default_tokenizer_config
from app.tokenizer.versioning import expected_tokenizer_record
from app.tokenizer.vocab import build_vocab


logger = logging.getLogger(__name__)

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")


class ExperimentPaths:
    """Resolved layout under ``<root>/<experiment_id>/``."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.config_json = self.root / "config.json"
        self.metadata_json = self.root / "metadata.json"
        self.metrics_jsonl = self.root / "metrics.jsonl"
        self.metrics_summary = self.root / "metrics_summary.json"
        self.checkpoints = self.root / "checkpoints"
        self.latest_checkpoint = self.checkpoints / "latest.pt"
        self.eval_dir = self.root / "eval"
        self.listening_dir = self.root / "listening"
        self.compare_dir = self.root / "compare"

    def ensure_layout(self) -> None:
        for path in (
            self.root,
            self.checkpoints,
            self.eval_dir,
            self.listening_dir,
            self.compare_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


def validate_experiment_id(experiment_id: str) -> str:
    if not _SAFE_ID.match(experiment_id):
        raise MusicTransformerExperimentError(
            "invalid_config",
            "experiment_id must be filesystem-safe "
            "(letters, digits, ._-; max 200 chars)",
            details={"experiment_id": experiment_id[:64]},
        )
    return experiment_id


def auto_experiment_id(*, config_digest: str, now: datetime | None = None) -> str:
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{config_digest[:8]}"


def resolve_experiment_root(
    output_root: str | Path | None = None,
    *,
    settings_env: dict[str, str] | None = None,
) -> Path:
    if output_root is not None:
        return Path(output_root)
    settings = load_music_transformer_settings(settings_env)
    return Path(settings.experiment_root)


def create_experiment(
    experiment: MusicTransformerExperimentV1,
    *,
    force: bool = False,
    tokenizer_config: TokenizerConfigV1 | None = None,
    settings_env: dict[str, str] | None = None,
) -> tuple[ExperimentPaths, MusicTransformerExperimentV1]:
    """Create run dir, freeze ``config.json`` + ``metadata.json``."""
    experiment_id = validate_experiment_id(experiment.experiment_id)
    root_base = resolve_experiment_root(experiment.output_root, settings_env=settings_env)
    paths = ExperimentPaths(root_base / experiment_id)
    if paths.root.exists() and any(paths.root.iterdir()):
        if not force:
            logger.error(
                "Experiment directory already exists",
                extra={
                    "experiment_id": experiment_id,
                    "root_basename": root_base.name,
                },
            )
            raise MusicTransformerExperimentError(
                "experiment_exists",
                f"Experiment already exists: {experiment_id}",
                details={"experiment_id": experiment_id, "root_basename": root_base.name},
            )
        logger.warning(
            "Overwriting experiment directory (--force)",
            extra={"experiment_id": experiment_id, "root_basename": root_base.name},
        )

    tok_cfg = tokenizer_config or default_tokenizer_config()
    vocab = build_vocab(tok_cfg)
    expectation = expected_tokenizer_record(tok_cfg, vocab)
    frozen = experiment.model_copy(
        update={
            "experiment_id": experiment_id,
            "output_root": str(root_base),
            "created_at": datetime.now(timezone.utc),
            "git_commit": capture_git_commit(),
            "project_version": capture_project_version(),
            "tokenizer_expectation": expectation,
            "dataset_version_id": experiment.dataset_version_id
            or experiment.train.dataset_version_id,
        }
    )
    digest = frozen.config_digest()
    paths.ensure_layout()
    _write_json(paths.config_json, frozen.model_dump(mode="json"))
    metadata = {
        "schema_version": frozen.schema_version,
        "experiment_id": frozen.experiment_id,
        "config_digest": digest,
        "created_at": frozen.created_at.isoformat() if frozen.created_at else None,
        "git_commit": frozen.git_commit,
        "project_version": frozen.project_version,
        "dataset_version_id": frozen.dataset_version_id,
        "dataset_name": frozen.dataset_name,
        "tokenizer": expectation.model_dump(mode="json"),
        "seed": frozen.seed,
        "device": frozen.device,
        "musical_quality_claim": False,
    }
    _write_json(paths.metadata_json, metadata)
    logger.info(
        "Experiment created",
        extra={
            "experiment_id": experiment_id,
            "root_basename": root_base.name,
            "config_digest_prefix": digest[:12],
            "tokenizer_version": expectation.expected_tokenizer_version,
            "vocab_hash_prefix": expectation.vocab_hash[:12],
        },
    )
    return paths, frozen


def load_experiment(
    experiment_id_or_path: str | Path,
    *,
    settings_env: dict[str, str] | None = None,
) -> tuple[ExperimentPaths, MusicTransformerExperimentV1]:
    candidate = Path(experiment_id_or_path)
    if candidate.is_dir() and (candidate / "config.json").is_file():
        paths = ExperimentPaths(candidate)
    else:
        experiment_id = validate_experiment_id(str(experiment_id_or_path))
        root_base = resolve_experiment_root(settings_env=settings_env)
        paths = ExperimentPaths(root_base / experiment_id)
    if not paths.config_json.is_file():
        logger.error(
            "Experiment missing",
            extra={"basename": paths.root.name},
        )
        raise MusicTransformerExperimentError(
            "experiment_missing",
            f"Experiment config not found: {paths.root.name}",
            details={"basename": paths.root.name},
        )
    raw = json.loads(paths.config_json.read_text(encoding="utf-8"))
    experiment = MusicTransformerExperimentV1.model_validate(raw)
    digest = experiment.config_digest()
    logger.info(
        "Experiment loaded",
        extra={
            "experiment_id": experiment.experiment_id,
            "root_basename": paths.root.name,
            "config_digest_prefix": digest[:12],
        },
    )
    return paths, experiment


def checkpoint_step_path(paths: ExperimentPaths, global_step: int) -> Path:
    return paths.checkpoints / f"step_{global_step:08d}.pt"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise MusicTransformerIOError(
            "invalid_config",
            f"Failed to write {path.name}",
            details={"error_type": type(exc).__name__},
        ) from exc
