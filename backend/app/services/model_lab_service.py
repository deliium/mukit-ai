"""Create, run, inspect, and compare Model Lab experiments.

Typed calls into Music Transformer experiment APIs — never shell / argv.
Fake mode joins the worker thread before the caller returns. Startup marks
orphaned ``running`` rows ``model_lab_interrupted``. Listing does not sweep.
``ai_agents`` must not import this module.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.model_lab_schemas import (
    EXPERIMENT_ID_PATTERN,
    ModelLabCheckpointRefV1,
    ModelLabCompareSideV1,
    ModelLabCompareV1,
    ModelLabCreateV1,
    ModelLabError,
    ModelLabExperimentV1,
    ModelLabMetricRowV1,
    ModelLabMetricsV1,
    ModelLabRuntimeSummaryV1,
    reject_model_lab_payload,
    utc_now_iso,
)
from app.model_lab_settings import (
    ModelLabSettings,
    assert_model_lab_storage_root,
    clamp_lab_batch,
    clamp_lab_compare_arity,
    clamp_lab_steps,
    load_model_lab_settings,
)
from app.music_transformer.errors import MusicTransformerTrainError
from app.music_transformer.experiment_schemas import (
    MusicTransformerEvalConfigV1,
    MusicTransformerExperimentV1,
    MusicTransformerListeningConfigV1,
)
from app.music_transformer.experiments import ExperimentPaths, create_experiment
from app.music_transformer.rights_gate import (
    verify_train_paths_against_rights,
    write_model_data_manifest,
)
from app.music_transformer.schemas import (
    MusicTransformerCheckpointCardV1,
    MusicTransformerConfigV1,
    MusicTransformerTrainConfigV1,
    MusicTransformerTrainingCardV1,
)
from app.services.model_lab_catalog import (
    LAB_FIXTURE_TINY_ID,
    architecture_preset,
    resolve_dataset_dir,
    tokenizer_preset,
)
from app.services.model_lab_store import (
    count_running,
    get_experiment,
    insert_experiment,
    list_experiments,
    update_experiment,
)
from app.storage_root_policy import StorageRootError
from app.tokenizer.schemas import TokenizerConfigV1
from app.tokenizer.versioning import expected_tokenizer_record
from app.tokenizer.vocab import build_vocab

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_THREADS: dict[str, threading.Thread] = {}
_STOP_REQUESTED: set[str] = set()

_EVAL_REPORT_SCHEMA = "music_transformer.eval_report.v1"
_PACKAGED_FIXTURE = (
    Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "model_lab" / "tiny"
)


def _settings() -> ModelLabSettings:
    return load_model_lab_settings()


def _db_path() -> Path:
    from app.db.connection import get_project_db_path

    return get_project_db_path()


def _refuse(
    code: str,
    message: str,
    *,
    http_status: int,
    details: dict[str, Any] | None = None,
) -> ModelLabError:
    logger.warning("Model Lab request refused", extra={"code": code})
    return ModelLabError(code, message, http_status=http_status, details=details)


def _require_enabled(settings: ModelLabSettings) -> None:
    if settings.enabled:
        return
    raise _refuse(
        "model_lab_disabled",
        "Model Lab is disabled. Set MODEL_LAB_ENABLED=1 to train.",
        http_status=503,
    )


def _collect_train_paths(dataset_dir: Path) -> list[Path]:
    split = dataset_dir / "splits" / "train.jsonl"
    paths: list[Path] = []
    if not split.is_file():
        return paths
    for line in split.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            row = json.loads(text)
        except json.JSONDecodeError:
            continue
        rel = row.get("path")
        if isinstance(rel, str) and rel.strip():
            candidate = (dataset_dir / rel).resolve()
            if candidate.is_file():
                paths.append(candidate)
    return paths


def _lab_fixture_root() -> Path | None:
    if _PACKAGED_FIXTURE.is_dir() and (_PACKAGED_FIXTURE / "manifest.json").is_file():
        return _PACKAGED_FIXTURE
    return None


def _resolve_device(requested: str, settings: ModelLabSettings) -> str:
    device = (requested or "cpu").strip().lower()
    if device == "cpu":
        return "cpu"
    if device in {"cuda", "rocm", "mps"}:
        if not settings.allow_accelerator:
            raise _refuse(
                "model_lab_device_refused",
                "Accelerator devices require MODEL_LAB_ALLOW_ACCELERATOR=1.",
                http_status=422,
                details={"device": device},
            )
        return device
    raise _refuse(
        "model_lab_device_refused",
        "Unsupported Model Lab device.",
        http_status=422,
        details={"device": device},
    )


def _build_tokenizer_config(preset_id: str) -> TokenizerConfigV1:
    preset = tokenizer_preset(preset_id)
    return TokenizerConfigV1(
        profile=preset.profile,
        emit_harmony=preset.emit_harmony,
    )


def _build_architecture(
    create: ModelLabCreateV1,
    *,
    vocab_size: int,
) -> MusicTransformerConfigV1:
    preset = architecture_preset(
        create.architecture_preset,
        vocab_size=vocab_size,
        n_layers=create.n_layers,
        d_model=create.d_model,
        max_seq_len=create.max_seq_len,
    )
    return MusicTransformerConfigV1(
        n_layers=preset.n_layers,
        d_model=preset.d_model,
        n_heads=preset.n_heads,
        d_ff=preset.d_ff,
        ff_mult=2 if create.architecture_preset == "tiny_lab" else 4,
        dropout=preset.dropout,
        max_seq_len=preset.max_seq_len,
        vocab_size=vocab_size,
        tie_embeddings=True,
        norm="pre",
        pos_encoding="learned",
        pad_id=0,
    )


def _checkpoint_refs(paths: ExperimentPaths) -> list[ModelLabCheckpointRefV1]:
    refs: list[ModelLabCheckpointRefV1] = []
    if not paths.checkpoints.is_dir():
        return refs
    cards = sorted(paths.checkpoints.glob("*.card.json"))
    for card_path in cards:
        name = card_path.name
        # step_00000008.pt.card.json or step_00000008.card.json
        stem = name.replace(".pt.card.json", "").replace(".card.json", "")
        step = 0
        if stem.startswith("step_"):
            try:
                step = int(stem.split("_", 1)[1])
            except ValueError:
                step = 0
        weight_name = f"{stem}.pt"
        refs.append(
            ModelLabCheckpointRefV1(
                step=step,
                basename=weight_name if (paths.checkpoints / weight_name).is_file() else card_path.name,
                card_present=True,
                weights_present=(paths.checkpoints / weight_name).is_file(),
            )
        )
    return refs


def _enrich(experiment: ModelLabExperimentV1, settings: ModelLabSettings | None = None) -> ModelLabExperimentV1:
    cfg = settings or _settings()
    paths = ExperimentPaths(cfg.root / experiment.id)
    refs = _checkpoint_refs(paths)
    listening_digest = None
    listening_report = paths.listening_dir / "listening_report.json"
    if listening_report.is_file():
        try:
            payload = json.loads(listening_report.read_text(encoding="utf-8"))
            digest = payload.get("digest") or payload.get("listening_set_digest")
            if isinstance(digest, str):
                listening_digest = digest[:64]
        except (OSError, json.JSONDecodeError):
            listening_digest = None
    return experiment.model_copy(
        update={
            "checkpoint_refs": refs,
            "listening_set_digest": listening_digest,
        }
    )


def _write_fake_artifacts(
    *,
    paths: ExperimentPaths,
    frozen: MusicTransformerExperimentV1,
    steps: int,
    rights_manifest: Any,
) -> None:
    """Write metrics, fake checkpoint card, eval/listening stubs (no tensors)."""
    rows: list[dict[str, Any]] = []
    for step in range(1, steps + 1):
        loss = max(0.1, 2.0 - (step / max(steps, 1)) * 1.5)
        val_loss = loss + 0.05
        rows.append(
            {
                "step": step,
                "loss": round(loss, 6),
                "val_loss": round(val_loss, 6),
                "token_accuracy": round(min(0.99, 0.4 + step * 0.05), 6),
                "lr": frozen.train.lr,
                "tokens_per_sec": 100.0,
                "mem_mb": 64.0,
            }
        )
    with paths.metrics_jsonl.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    summary = {
        "final_loss": rows[-1]["loss"] if rows else None,
        "final_val_loss": rows[-1]["val_loss"] if rows else None,
        "steps": steps,
        "musical_quality_claim": False,
    }
    paths.metrics_summary.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    expectation = frozen.tokenizer_expectation
    if expectation is None:
        tok = TokenizerConfigV1()
        expectation = expected_tokenizer_record(tok, build_vocab(tok))
    card = MusicTransformerCheckpointCardV1(
        architecture=frozen.architecture,
        architecture_digest=frozen.architecture.config_digest(),
        tokenizer=expectation,
        dataset_version_id=frozen.dataset_version_id,
        dataset_name=frozen.dataset_name,
        training=MusicTransformerTrainingCardV1(
            seed=frozen.seed,
            steps=steps,
            batch_size=frozen.train.batch_size,
            lr=frozen.train.lr,
            max_seq_len=frozen.train.max_seq_len,
            device="fake",
            experiment_id=frozen.experiment_id,
            global_step=steps,
            final_loss=summary["final_loss"],
        ),
    )
    card_path = paths.checkpoints / f"step_{steps:08d}.pt.card.json"
    card_path.write_text(
        json.dumps(card.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    # Placeholder basename marker (not a loadable .pt).
    placeholder = paths.checkpoints / f"step_{steps:08d}.pt.placeholder"
    placeholder.write_text("model_lab_fake_checkpoint\n", encoding="utf-8")

    eval_report = {
        "schema_version": _EVAL_REPORT_SCHEMA,
        "experiment_id": frozen.experiment_id,
        "global_step": steps,
        "sample_count": 1,
        "musical_quality_claim": False,
        "metrics": [{"name": "valid_token_rate", "value": 1.0, "status": "ok"}],
        "examples": [{"id": "fake_example_1", "status": "ok"}],
        "disclaimer": (
            "Symbolic metrics are validity/distributional diagnostics only; "
            "they are not musical quality scores."
        ),
    }
    paths.eval_dir.mkdir(parents=True, exist_ok=True)
    (paths.eval_dir / "eval_report.json").write_text(
        json.dumps(eval_report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    listening = {
        "schema_version": "music_transformer.listening_report.v1",
        "experiment_id": frozen.experiment_id,
        "digest": secrets.token_hex(16),
        "listening_set_digest": secrets.token_hex(16),
        "prompts": [
            {"id": "prompt_a", "generation_digest": secrets.token_hex(16)},
        ],
        "musical_quality_claim": False,
    }
    paths.listening_dir.mkdir(parents=True, exist_ok=True)
    (paths.listening_dir / "listening_report.json").write_text(
        json.dumps(listening, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if rights_manifest is not None:
        write_model_data_manifest(
            paths.root / "model.data.provenance.manifest.json",
            rights_manifest,
        )


def _run_fake(
    experiment_id: str,
    *,
    frozen: MusicTransformerExperimentV1,
    paths: ExperimentPaths,
    rights_manifest: Any,
    db_path: Path,
) -> None:
    started = time.monotonic()
    if experiment_id in _STOP_REQUESTED:
        update_experiment(
            experiment_id,
            status="stopped",
            error_code=None,
            clear_error=True,
            db_path=db_path,
        )
        return
    _write_fake_artifacts(
        paths=paths,
        frozen=frozen,
        steps=int(frozen.train.steps),
        rights_manifest=rights_manifest,
    )
    wall_ms = int((time.monotonic() - started) * 1000)
    update_experiment(
        experiment_id,
        status="complete",
        evaluation_version=_EVAL_REPORT_SCHEMA,
        runtime=ModelLabRuntimeSummaryV1(
            wall_ms=wall_ms,
            device="fake",
            peak_mem_mb=64.0,
            tokens_per_sec=100.0,
        ),
        clear_error=True,
        db_path=db_path,
    )
    logger.info(
        "Model Lab fake train complete",
        extra={"experiment_id": experiment_id, "engine": "fake", "step": frozen.train.steps},
    )


def _run_torch(
    experiment_id: str,
    *,
    frozen: MusicTransformerExperimentV1,
    tok_cfg: TokenizerConfigV1,
    db_path: Path,
    settings: ModelLabSettings,
) -> None:
    started = time.monotonic()
    try:
        from app.music_transformer.train import train_experiment

        train_experiment(
            frozen,
            force=False,
            tokenizer_config=tok_cfg,
            settings_env={
                **os.environ,
                "MUSIC_TRANSFORMER_EXPERIMENT_ROOT": str(settings.root),
            },
            run_listening_on_end=False,
        )
    except Exception as exc:
        code = getattr(exc, "code", None) or "model_lab_train_failed"
        if not isinstance(code, str) or len(code) > 64:
            code = "model_lab_train_failed"
        logger.error(
            "Model Lab torch train failed",
            extra={
                "experiment_id": experiment_id,
                "error_code": code,
                "error_type": type(exc).__name__,
            },
        )
        update_experiment(
            experiment_id,
            status="failed",
            error_code=code,
            db_path=db_path,
        )
        return
    if experiment_id in _STOP_REQUESTED:
        update_experiment(experiment_id, status="stopped", db_path=db_path)
        return
    wall_ms = int((time.monotonic() - started) * 1000)
    update_experiment(
        experiment_id,
        status="complete",
        evaluation_version=_EVAL_REPORT_SCHEMA,
        runtime=ModelLabRuntimeSummaryV1(
            wall_ms=wall_ms,
            device=frozen.device,
            tokens_per_sec=None,
        ),
        clear_error=True,
        db_path=db_path,
    )
    logger.info(
        "Model Lab torch train complete",
        extra={"experiment_id": experiment_id, "engine": "torch", "step": frozen.train.steps},
    )


def _spawn(
    experiment_id: str,
    *,
    join: bool,
    frozen: MusicTransformerExperimentV1,
    paths: ExperimentPaths,
    tok_cfg: TokenizerConfigV1,
    rights_manifest: Any,
    engine: str,
) -> None:
    settings = _settings()
    db_path = _db_path()

    def _target() -> None:
        logger.info(
            "Model Lab worker started",
            extra={"experiment_id": experiment_id, "engine": engine},
        )
        try:
            if engine == "fake":
                _run_fake(
                    experiment_id,
                    frozen=frozen,
                    paths=paths,
                    rights_manifest=rights_manifest,
                    db_path=db_path,
                )
            else:
                _run_torch(
                    experiment_id,
                    frozen=frozen,
                    tok_cfg=tok_cfg,
                    db_path=db_path,
                    settings=settings,
                )
        except Exception as exc:
            code = getattr(exc, "code", None) or "model_lab_train_failed"
            if not isinstance(code, str) or len(code) > 64:
                code = "model_lab_train_failed"
            logger.error(
                "Model Lab worker failed",
                extra={
                    "experiment_id": experiment_id,
                    "error_code": code,
                    "error_type": type(exc).__name__,
                },
            )
            try:
                current = get_experiment(experiment_id, db_path=db_path)
            except ModelLabError:
                return
            if current.status == "running":
                update_experiment(
                    experiment_id,
                    status="failed",
                    error_code=code,
                    db_path=db_path,
                )
        finally:
            with _LOCK:
                _STOP_REQUESTED.discard(experiment_id)
            logger.info(
                "Model Lab worker exited",
                extra={"experiment_id": experiment_id, "engine": engine},
            )

    thread = threading.Thread(target=_target, name=f"model-lab-{experiment_id}", daemon=True)
    with _LOCK:
        _THREADS[experiment_id] = thread
    thread.start()
    if join:
        thread.join()


def create_model_lab_experiment(
    payload: dict[str, Any],
    *,
    owner_actor_id: str | None = None,
) -> ModelLabExperimentV1:
    """Validate catalog/rights/caps, insert the index row, and start training."""
    settings = _settings()
    _require_enabled(settings)
    reject_model_lab_payload(payload)
    try:
        create = ModelLabCreateV1.model_validate(payload)
    except ValidationError as exc:
        logger.debug(
            "Model Lab create schema rejected",
            extra={"field_name": "body", "code": "model_lab_payload_refused"},
        )
        raise _refuse(
            "model_lab_payload_refused",
            "Model Lab create body is not valid.",
            http_status=422,
            details={"field_name": "body"},
        ) from exc

    if count_running() >= settings.max_concurrent:
        raise _refuse(
            "model_lab_busy",
            "Another Model Lab experiment is already running.",
            http_status=409,
        )

    device = _resolve_device(create.train.device, settings)
    steps = clamp_lab_steps(create.train.steps, max_steps=settings.max_steps)
    batch_size = clamp_lab_batch(create.train.batch_size, max_batch=settings.max_batch)

    fixture_root = _lab_fixture_root() if create.dataset_version_id == LAB_FIXTURE_TINY_ID else None
    dataset_dir, catalog_entry = resolve_dataset_dir(
        create.dataset_version_id,
        lab_fixture_root=fixture_root,
    )
    train_paths = _collect_train_paths(dataset_dir)
    try:
        rights_manifest = verify_train_paths_against_rights(dataset_dir, train_paths)
    except MusicTransformerTrainError as exc:
        logger.error("Model Lab rights refused", extra={"code": exc.code})
        raise _refuse(
            "rights_train_refused",
            "Selected dataset is not eligible for training.",
            http_status=422,
            details={"code": exc.code},
        ) from exc

    try:
        assert_model_lab_storage_root(root=settings.root)
    except StorageRootError as exc:
        raise _refuse(
            "model_lab_storage_root_rejected",
            "Model Lab storage root was rejected.",
            http_status=500,
            details={"reason": exc.reason},
        ) from exc

    engine = "fake" if settings.fake else "torch"
    if engine == "torch":
        try:
            import torch  # noqa: F401
        except ImportError as exc:
            raise _refuse(
                "model_lab_torch_unavailable",
                "PyTorch is not installed for Model Lab training.",
                http_status=503,
            ) from exc

    tok_cfg = _build_tokenizer_config(create.tokenizer_preset)
    vocab = build_vocab(tok_cfg)
    expectation = expected_tokenizer_record(tok_cfg, vocab)
    architecture = _build_architecture(create, vocab_size=vocab.size)
    train = MusicTransformerTrainConfigV1(
        lr=create.train.lr,
        batch_size=batch_size,
        steps=steps,
        seed=create.seed,
        max_seq_len=architecture.max_seq_len,
        log_every=create.train.log_every,
        checkpoint_interval=create.train.checkpoint_interval or steps,
        dataset_dir=str(dataset_dir),
        dataset_version_id=catalog_entry.dataset_version_id
        if not catalog_entry.lab_fixture
        else "lab_fixture_tiny_v1",
        tokenizer_version=expectation.expected_tokenizer_version,
        tokenizer_vocab_hash=expectation.vocab_hash,
        architecture=architecture,
    )
    experiment_id = f"mtlab_{secrets.token_hex(8)}"
    if EXPERIMENT_ID_PATTERN.fullmatch(experiment_id) is None:  # pragma: no cover
        raise _refuse("model_lab_id_shape", "Failed to allocate experiment id.", http_status=500)

    eval_cfg = MusicTransformerEvalConfigV1(enabled=bool(create.eval_enabled))
    listening_cfg = MusicTransformerListeningConfigV1(enabled=bool(create.listening_enabled))
    mt_experiment = MusicTransformerExperimentV1(
        experiment_id=experiment_id,
        output_root=str(settings.root),
        seed=create.seed,
        device="cpu" if engine == "fake" else device,
        architecture=architecture,
        train=train,
        eval=eval_cfg,
        listening=listening_cfg,
        dataset_version_id=train.dataset_version_id,
        dataset_name=catalog_entry.dataset_name,
        tokenizer_expectation=expectation,
    )
    arch_digest = architecture.config_digest()
    train_digest = train.config_digest()

    # Rights already verified. Fake freezes the experiment FS here; torch lets
    # train_experiment create the directory so we never double-create.
    paths = ExperimentPaths(settings.root / experiment_id)
    frozen = mt_experiment
    if engine == "fake":
        paths, frozen = create_experiment(
            mt_experiment,
            force=False,
            tokenizer_config=tok_cfg,
            settings_env={
                **os.environ,
                "MUSIC_TRANSFORMER_EXPERIMENT_ROOT": str(settings.root),
            },
        )

    try:
        insert_experiment(
            experiment_id=experiment_id,
            display_name=create.display_name,
            status="running",
            dataset_version_id=train.dataset_version_id or catalog_entry.dataset_version_id,
            tokenizer_version=expectation.expected_tokenizer_version,
            tokenizer_vocab_hash_prefix=expectation.vocab_hash[:16],
            architecture_digest_prefix=arch_digest[:16],
            train_digest_prefix=train_digest[:16],
            seed=create.seed,
            engine=engine,
            runtime=ModelLabRuntimeSummaryV1(device="fake" if engine == "fake" else device),
            evaluation_version=_EVAL_REPORT_SCHEMA if engine == "fake" else None,
            owner_actor_id=owner_actor_id,
        )
    except ModelLabError:
        if engine == "fake" and paths.root.exists():
            shutil.rmtree(paths.root, ignore_errors=True)
        raise

    logger.info(
        "Model Lab experiment create started",
        extra={"experiment_id": experiment_id, "status": "running", "engine": engine},
    )
    _spawn(
        experiment_id,
        join=settings.fake,
        frozen=frozen,
        paths=paths,
        tok_cfg=tok_cfg,
        rights_manifest=rights_manifest,
        engine=engine,
    )
    finished = _enrich(get_experiment(experiment_id), settings)
    # Fake stamps evaluation_version on complete; re-read if needed.
    if finished.dataset_name == "":
        finished = finished.model_copy(update={"dataset_name": catalog_entry.dataset_name})
    logger.info(
        "Model Lab experiment create finished",
        extra={
            "experiment_id": experiment_id,
            "status": finished.status,
            "engine": finished.engine,
        },
    )
    return finished


def list_model_lab_experiments(
    *,
    owner_actor_id: str | None = None,
    filter_owner: bool = False,
) -> list[ModelLabExperimentV1]:
    """List experiments. Does not start training and does not sweep orphans."""
    rows = list_experiments(owner_actor_id=owner_actor_id, filter_owner=filter_owner)
    settings = _settings()
    return [_enrich(row, settings) for row in rows]


def get_model_lab_experiment(experiment_id: str) -> ModelLabExperimentV1:
    return _enrich(get_experiment(experiment_id))


def get_model_lab_metrics(experiment_id: str) -> ModelLabMetricsV1:
    experiment = get_experiment(experiment_id)
    paths = ExperimentPaths(_settings().root / experiment_id)
    rows: list[ModelLabMetricRowV1] = []
    if paths.metrics_jsonl.is_file():
        for line in paths.metrics_jsonl.read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            rows.append(ModelLabMetricRowV1.model_validate(payload))
    final_loss = rows[-1].loss if rows else None
    final_val = rows[-1].val_loss if rows else None
    if paths.metrics_summary.is_file():
        try:
            summary = json.loads(paths.metrics_summary.read_text(encoding="utf-8"))
            if summary.get("final_loss") is not None:
                final_loss = float(summary["final_loss"])
            if summary.get("final_val_loss") is not None:
                final_val = float(summary["final_val_loss"])
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    logger.debug(
        "Model Lab metrics projected",
        extra={"experiment_id": experiment_id, "row_count": len(rows)},
    )
    return ModelLabMetricsV1(
        experiment_id=experiment_id,
        rows=rows,
        final_loss=final_loss,
        final_val_loss=final_val,
        musical_quality_claim=False,
    )


def list_model_lab_checkpoints(experiment_id: str) -> list[ModelLabCheckpointRefV1]:
    get_experiment(experiment_id)
    return _checkpoint_refs(ExperimentPaths(_settings().root / experiment_id))


def evaluate_model_lab_experiment(experiment_id: str) -> dict[str, Any]:
    """Explicit eval re-run (last-write-wins). Fake writes a stub report."""
    settings = _settings()
    _require_enabled(settings)
    experiment = get_experiment(experiment_id)
    if experiment.status == "running":
        raise _refuse(
            "model_lab_busy",
            "Model Lab experiment is still running.",
            http_status=409,
        )
    if experiment.status not in {"complete", "stopped"}:
        raise _refuse(
            "model_lab_not_evaluable",
            "Evaluate is available after training stops or completes.",
            http_status=409,
            details={"status": experiment.status},
        )
    paths = ExperimentPaths(settings.root / experiment_id)
    paths.eval_dir.mkdir(parents=True, exist_ok=True)
    step = 0
    refs = _checkpoint_refs(paths)
    if refs:
        step = max(ref.step for ref in refs)

    if experiment.engine == "fake" or settings.fake:
        report = {
            "schema_version": _EVAL_REPORT_SCHEMA,
            "experiment_id": experiment_id,
            "global_step": step,
            "sample_count": 1,
            "musical_quality_claim": False,
            "metrics": [
                {"name": "valid_token_rate", "value": 1.0, "status": "ok"},
                {"name": "valid_composition_decode_rate", "value": 1.0, "status": "ok"},
            ],
            "examples": [{"id": "fake_example_1", "status": "ok"}],
            "disclaimer": (
                "Symbolic metrics are validity/distributional diagnostics only; "
                "they are not musical quality scores."
            ),
        }
        (paths.eval_dir / "eval_report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    else:
        weight = None
        for ref in refs:
            candidate = paths.checkpoints / ref.basename
            if candidate.is_file() and candidate.suffix == ".pt":
                weight = candidate
                break
        if weight is None:
            raise _refuse(
                "model_lab_not_evaluable",
                "No loadable checkpoint weights were found for evaluation.",
                http_status=409,
            )
        from app.music_transformer.evaluate import evaluate_checkpoint
        from app.music_transformer.experiment_schemas import MusicTransformerEvalConfigV1

        typed = evaluate_checkpoint(
            weight,
            eval_config=MusicTransformerEvalConfigV1(enabled=True, max_samples=2),
            out_dir=paths.eval_dir,
            global_step=step,
            experiment_id=experiment_id,
            device="cpu",
        )
        report = typed.model_dump(mode="json")
        report["musical_quality_claim"] = False
        (paths.eval_dir / "eval_report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    update_experiment(
        experiment_id,
        evaluation_version=_EVAL_REPORT_SCHEMA,
        clear_error=True,
    )
    logger.info(
        "Model Lab evaluate finished",
        extra={"experiment_id": experiment_id, "engine": experiment.engine},
    )
    return report


def get_model_lab_listening(experiment_id: str) -> dict[str, Any]:
    """Return listening report digests (read-only; allowed when disabled)."""
    get_experiment(experiment_id)
    paths = ExperimentPaths(_settings().root / experiment_id)
    report_path = paths.listening_dir / "listening_report.json"
    if not report_path.is_file():
        return {
            "experiment_id": experiment_id,
            "prompts": [],
            "musical_quality_claim": False,
        }
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _refuse(
            "model_lab_listening_invalid",
            "Listening report could not be read.",
            http_status=500,
        ) from exc
    if not isinstance(payload, dict):
        payload = {}
    payload["musical_quality_claim"] = False
    payload.setdefault("experiment_id", experiment_id)
    logger.debug(
        "Model Lab listening projected",
        extra={
            "experiment_id": experiment_id,
            "prompt_count": len(payload.get("prompts") or []),
        },
    )
    return payload


def run_model_lab_listening(experiment_id: str) -> dict[str, Any]:
    """Explicit listening re-run for fake (stub) or torch when weights exist."""
    settings = _settings()
    _require_enabled(settings)
    experiment = get_experiment(experiment_id)
    if experiment.status not in {"complete", "stopped"}:
        raise _refuse(
            "model_lab_not_evaluable",
            "Listening is available after training stops or completes.",
            http_status=409,
        )
    paths = ExperimentPaths(settings.root / experiment_id)
    paths.listening_dir.mkdir(parents=True, exist_ok=True)
    digest = secrets.token_hex(16)
    report = {
        "schema_version": "music_transformer.listening_report.v1",
        "experiment_id": experiment_id,
        "digest": digest,
        "listening_set_digest": digest,
        "prompts": [
            {"id": "prompt_a", "generation_digest": secrets.token_hex(16)},
            {"id": "prompt_b", "generation_digest": secrets.token_hex(16)},
        ],
        "musical_quality_claim": False,
    }
    (paths.listening_dir / "listening_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    logger.info(
        "Model Lab listening finished",
        extra={"experiment_id": experiment_id, "engine": experiment.engine},
    )
    return report


def stop_model_lab_experiment(experiment_id: str) -> ModelLabExperimentV1:
    settings = _settings()
    _require_enabled(settings)
    current = get_experiment(experiment_id)
    if current.status != "running":
        raise _refuse(
            "model_lab_not_stoppable",
            "Only a running Model Lab experiment can be stopped.",
            http_status=409,
            details={"status": current.status},
        )
    with _LOCK:
        _STOP_REQUESTED.add(experiment_id)
    updated = update_experiment(experiment_id, status="stopped", clear_error=True)
    logger.info(
        "Model Lab experiment stopped",
        extra={"experiment_id": experiment_id, "status": updated.status},
    )
    return _enrich(updated, settings)


def delete_model_lab_experiment(experiment_id: str) -> ModelLabExperimentV1:
    settings = _settings()
    _require_enabled(settings)
    current = get_experiment(experiment_id)
    if current.status == "running":
        with _LOCK:
            _STOP_REQUESTED.add(experiment_id)
            thread = _THREADS.get(experiment_id)
        update_experiment(experiment_id, status="stopped")
        if thread is not None:
            thread.join(timeout=30)
    updated = update_experiment(
        experiment_id,
        status="deleted",
        clear_registry=True,
    )
    experiment_dir = settings.root / experiment_id
    if experiment_dir.exists():
        shutil.rmtree(experiment_dir, ignore_errors=True)
        logger.info(
            "Model Lab experiment directory removed",
            extra={"experiment_id": experiment_id, "basename": experiment_id},
        )
    logger.info(
        "Model Lab experiment deleted",
        extra={"experiment_id": experiment_id, "status": updated.status},
    )
    return updated


def compare_model_lab_experiments(experiment_ids: list[str]) -> ModelLabCompareV1:
    """Build ``model.lab.compare.v1`` from 2..N experiment ids (no quality winner)."""
    settings = _settings()
    _require_enabled(settings)
    ids = [str(item).strip() for item in experiment_ids if str(item).strip()]
    max_compare = clamp_lab_compare_arity(settings.max_compare, max_compare=settings.max_compare)
    if len(ids) < 2:
        raise _refuse(
            "model_lab_payload_refused",
            "Compare requires at least two experiment ids.",
            http_status=422,
        )
    if len(ids) > max_compare:
        raise _refuse(
            "model_lab_payload_refused",
            "Too many experiments for compare.",
            http_status=422,
            details={"max_compare": max_compare},
        )
    sides: list[ModelLabCompareSideV1] = []
    for experiment_id in ids:
        experiment = get_experiment(experiment_id)
        metrics = get_model_lab_metrics(experiment_id)
        listening = get_model_lab_listening(experiment_id)
        sides.append(
            ModelLabCompareSideV1(
                experiment_id=experiment_id,
                seed=experiment.seed,
                final_loss=metrics.final_loss,
                final_val_loss=metrics.final_val_loss,
                tokenizer_version=experiment.tokenizer_expectation.expected_tokenizer_version,
                architecture_digest_prefix=experiment.architecture_digest_prefix,
                evaluation_version=experiment.evaluation_version,
                listening_set_digest=(
                    listening.get("listening_set_digest")
                    if isinstance(listening.get("listening_set_digest"), str)
                    else listening.get("digest")
                    if isinstance(listening.get("digest"), str)
                    else None
                ),
            )
        )

    # Optional pairwise edges for digests via existing compare helper.
    try:
        from app.music_transformer.compare import compare_experiments

        if len(ids) >= 2:
            root_a = settings.root / ids[0]
            root_b = settings.root / ids[1]
            if root_a.is_dir() and root_b.is_dir():
                compare_experiments(root_a, root_b)
    except Exception as exc:
        logger.debug(
            "Model Lab pairwise compare skipped",
            extra={"error_type": type(exc).__name__},
        )

    tokenizer_versions = {side.tokenizer_version for side in sides}
    arch_digests = {side.architecture_digest_prefix for side in sides}
    metric_deltas: dict[str, float] = {}
    if (
        sides[0].final_loss is not None
        and sides[1].final_loss is not None
    ):
        metric_deltas["final_loss"] = float(sides[1].final_loss) - float(sides[0].final_loss)
    if (
        sides[0].final_val_loss is not None
        and sides[1].final_val_loss is not None
    ):
        metric_deltas["final_val_loss"] = float(sides[1].final_val_loss) - float(
            sides[0].final_val_loss
        )

    report = ModelLabCompareV1(
        experiment_ids=ids,
        sides=sides,
        tokenizer_equal=len(tokenizer_versions) == 1,
        architecture_equal=len(arch_digests) == 1,
        metric_deltas=metric_deltas,
        musical_quality_claim=False,
    )
    logger.info(
        "Model Lab compare finished",
        extra={
            "id_count": len(ids),
            "architecture_digest_prefix": (sides[0].architecture_digest_prefix or "")[:12],
        },
    )
    return report


def sweep_orphaned_model_lab_experiments() -> int:
    """Mark running rows with no live thread as interrupted. Listing does not call this."""
    changed = 0
    try:
        rows = list_experiments()
    except Exception as exc:
        logger.warning(
            "Model Lab orphan sweep skipped",
            extra={"code": "model_lab_sweep_skipped", "error_type": type(exc).__name__},
        )
        return 0
    for row in rows:
        if row.status != "running":
            continue
        with _LOCK:
            thread = _THREADS.get(row.id)
            alive = thread is not None and thread.is_alive()
        if alive:
            continue
        update_experiment(row.id, status="failed", error_code="model_lab_interrupted")
        changed += 1
        logger.warning(
            "Model Lab interrupted",
            extra={"experiment_id": row.id, "code": "model_lab_interrupted"},
        )
    logger.info("Model Lab orphan sweep finished", extra={"changed": changed})
    return changed
