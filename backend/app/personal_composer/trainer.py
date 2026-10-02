"""Step loop for a personal adapter.

Does not import FastAPI or ``project_store``. Status is read through a new
SQLite connection at each step boundary. Fake mode never imports torch.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.personal_composer_schemas import (
    FAKE_ADAPTER_SCHEMA,
    TRAINING_STEP_SCHEMA,
    PersonalFakeAdapterV1,
    PersonalTrainingManifestV1,
)

logger = logging.getLogger(__name__)


def _read_status(adapter_id: str, db_path: Path | None) -> str:
    from app.db.connection import get_connection

    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT status FROM personal_composer_adapters WHERE id = ?",
            (adapter_id,),
        ).fetchone()
    if row is None:
        return "deleted"
    return str(row["status"])


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def completed_step(adapter_dir: Path) -> int:
    step_path = adapter_dir / "step.json"
    if not step_path.is_file():
        return 0
    payload = _read_json(step_path)
    return int(payload.get("step") or 0)


def _write_step(adapter_dir: Path, *, adapter_id: str, step: int, max_steps: int) -> None:
    payload = {
        "schema_version": TRAINING_STEP_SCHEMA,
        "adapter_id": adapter_id,
        "step": step,
        "max_steps": max_steps,
    }
    (adapter_dir / "step.json").write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    logger.debug(
        "Personal composer step file written",
        extra={"adapter_id": adapter_id, "step": step, "basename": "step.json"},
    )


def _write_fake_adapter(adapter_dir: Path, manifest: PersonalTrainingManifestV1, *, step: int) -> None:
    document = PersonalFakeAdapterV1(
        adapter_id=manifest.adapter_id,
        base_model_id="fake:symbolic-tiny",
        snapshot_version=manifest.snapshot_version,
        adapter_config=manifest.adapter_config,
        step=step,
    )
    (adapter_dir / "adapter.fake.json").write_text(
        json.dumps(document.model_dump(mode="json"), sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    logger.debug(
        "Personal fake adapter written",
        extra={
            "adapter_id": manifest.adapter_id,
            "step": step,
            "basename": "adapter.fake.json",
            "schema_version": FAKE_ADAPTER_SCHEMA,
        },
    )


def load_snapshot_compositions(adapter_dir: Path) -> list[Any]:
    """Read copied scores from the snapshot directory. Does not open projects."""
    from app.composition_schemas import CompositionV2

    index = _read_json(adapter_dir / "snapshot" / "index.json")
    compositions = []
    for item in index.get("items") or []:
        relative = str(item["relative_path"])
        payload = _read_json(adapter_dir / "snapshot" / relative)
        compositions.append(CompositionV2.model_validate(payload))
    return compositions


def run_torch_steps_on_snapshot(
    adapter_dir: Path,
    config: Any,
    *,
    max_steps: int = 2,
    rank: int = 4,
    alpha: int = 8,
) -> tuple[Any, dict[str, Any]]:
    """Train LoRA on a snapshot. Returns the model and pre-step base clones.

    The caller supplies ``tiny_test_config``. This function does not open
    ``PROJECT_DB_PATH``.
    """
    import torch

    from app.personal_composer.lora import attach_lora, run_next_token_steps
    from app.music_transformer.model import MusicTransformerLM
    from app.tokenizer.encode import encode_composition

    compositions = load_snapshot_compositions(adapter_dir)
    token_rows = [list(encode_composition(composition).token_ids) for composition in compositions]
    model = MusicTransformerLM(config)
    attach_lora(model, rank=rank, alpha=alpha, dropout=0.0)
    clones = {
        name: parameter.detach().cpu().clone()
        for name, parameter in model.named_parameters()
        if parameter.requires_grad is False
    }
    run_next_token_steps(model, token_rows, max_steps=max_steps, device="cpu")
    del torch
    return model, clones


def run_personal_training(
    adapter_id: str,
    *,
    root: Path,
    db_path: Path | None = None,
    read_status: Callable[[], str] | None = None,
) -> str:
    """Advance one job from the saved step through ``max_steps``."""
    from app.services.personal_composer_store import get_adapter, update_adapter

    adapter_dir = root / adapter_id
    manifest = PersonalTrainingManifestV1.model_validate(_read_json(adapter_dir / "manifest.json"))
    row = get_adapter(adapter_id, db_path=db_path)
    status_reader = read_status or (lambda: _read_status(adapter_id, db_path))
    done = completed_step(adapter_dir)
    logger.info(
        "Personal composer training started",
        extra={
            "adapter_id": adapter_id,
            "step": done,
            "max_steps": row.job.max_steps,
            "engine": manifest.engine,
        },
    )
    if manifest.engine == "torch":
        _run_torch_job(
            adapter_dir,
            manifest,
            start_step=done,
            max_steps=row.job.max_steps,
            status_reader=status_reader,
            db_path=db_path,
        )
    else:
        for step in range(done + 1, row.job.max_steps + 1):
            status = status_reader()
            if status in {"stopped", "deleted"}:
                logger.info(
                    "Personal composer training halted",
                    extra={
                        "adapter_id": adapter_id,
                        "step": done,
                        "max_steps": row.job.max_steps,
                        "engine": manifest.engine,
                        "status": status,
                    },
                )
                return status
            _write_fake_adapter(adapter_dir, manifest, step=step)
            _write_step(adapter_dir, adapter_id=adapter_id, step=step, max_steps=row.job.max_steps)
            update_adapter(adapter_id, status="running", step=step, db_path=db_path)
            done = step
            logger.info(
                "Personal composer step finished",
                extra={
                    "adapter_id": adapter_id,
                    "step": step,
                    "max_steps": row.job.max_steps,
                    "engine": manifest.engine,
                },
            )
    final = get_adapter(adapter_id, db_path=db_path)
    if final.job.step >= final.job.max_steps and final.job.status == "running":
        final = update_adapter(adapter_id, status="complete", step=final.job.step, db_path=db_path)
    logger.info(
        "Personal composer training finished",
        extra={
            "adapter_id": adapter_id,
            "step": final.job.step,
            "max_steps": final.job.max_steps,
            "engine": manifest.engine,
            "status": final.job.status,
        },
    )
    return final.job.status


def _run_torch_job(
    adapter_dir: Path,
    manifest: PersonalTrainingManifestV1,
    *,
    start_step: int,
    max_steps: int,
    status_reader: Callable[[], str],
    db_path: Path | None,
) -> str:
    import torch

    from app.personal_composer.lora import attach_lora, lora_state_dict, run_next_token_steps
    from app.music_transformer.model import MusicTransformerLM
    from app.music_transformer.schemas import tiny_test_config
    from app.services.personal_composer_store import update_adapter
    from app.tokenizer.encode import encode_composition
    from app.tokenizer.schemas import default_tokenizer_config
    from app.tokenizer.vocab import build_vocab

    compositions = load_snapshot_compositions(adapter_dir)
    token_rows = [list(encode_composition(composition).token_ids) for composition in compositions]
    vocab = build_vocab(default_tokenizer_config())
    config = tiny_test_config(vocab_size=vocab.size)
    model = MusicTransformerLM(config)
    attach_lora(
        model,
        rank=manifest.adapter_config.rank,
        alpha=manifest.adapter_config.alpha,
        dropout=manifest.adapter_config.dropout,
    )
    checkpoint = adapter_dir / "adapter.pt"
    if checkpoint.is_file() and start_step > 0:
        payload = torch.load(checkpoint, map_location="cpu", weights_only=False)
        model.load_state_dict(payload.get("lora_state_dict") or {}, strict=False)
    for step in range(start_step + 1, max_steps + 1):
        status = status_reader()
        if status in {"stopped", "deleted"}:
            logger.info(
                "Personal composer training halted",
                extra={
                    "adapter_id": manifest.adapter_id,
                    "step": start_step,
                    "max_steps": max_steps,
                    "engine": "torch",
                    "status": status,
                },
            )
            return status
        run_next_token_steps(model, token_rows, max_steps=1, device="cpu")
        torch.save(
            {
                "schema_version": "personal.adapter.v1",
                "base_model_id": manifest.base_model_id,
                "snapshot_version": manifest.snapshot_version,
                "adapter_config": manifest.adapter_config.model_dump(mode="json"),
                "lora_state_dict": lora_state_dict(model),
            },
            checkpoint,
        )
        _write_step(
            adapter_dir,
            adapter_id=manifest.adapter_id,
            step=step,
            max_steps=max_steps,
        )
        update_adapter(manifest.adapter_id, status="running", step=step, db_path=db_path)
        start_step = step
        logger.info(
            "Personal composer step finished",
            extra={
                "adapter_id": manifest.adapter_id,
                "step": step,
                "max_steps": max_steps,
                "engine": "torch",
            },
        )
    return "running"


def load_personal_lora_model(
    adapter_dir: Path,
    *,
    base_model_id: str,
    base_checkpoint_basename: str | None,
):
    """Build the frozen recorded base and load saved LoRA tensors. Does not train."""
    import torch

    from app.music_transformer.checkpoint import load_checkpoint
    from app.music_transformer.model import MusicTransformerLM
    from app.music_transformer.schemas import tiny_test_config
    from app.personal_composer.lora import attach_lora
    from app.tokenizer.schemas import default_tokenizer_config
    from app.tokenizer.vocab import build_vocab

    artifact = adapter_dir / "adapter.pt"
    if not artifact.is_file():
        raise FileNotFoundError(artifact.name)
    payload = torch.load(artifact, map_location="cpu", weights_only=False)
    card = payload.get("adapter_config") or {}
    tok_cfg = default_tokenizer_config()
    vocab = build_vocab(tok_cfg)
    if base_model_id == "music_transformer" and base_checkpoint_basename:
        from app.services.model_path_resolve import resolve_music_transformer_checkpoint

        checkpoint = resolve_music_transformer_checkpoint(base_checkpoint_basename)
        weights, train_card = load_checkpoint(checkpoint, map_location="cpu")
        model = MusicTransformerLM(train_card.architecture)
        model.load_state_dict(weights["model_state_dict"])
    else:
        model = MusicTransformerLM(tiny_test_config(vocab_size=vocab.size))
    attach_lora(
        model,
        rank=int(card.get("rank") or 4),
        alpha=int(card.get("alpha") or 8),
        dropout=float(card.get("dropout") or 0.0),
    )
    model.load_state_dict(payload.get("lora_state_dict") or {}, strict=False)
    model.eval()
    logger.info(
        "[FIX] Personal LoRA weights loaded",
        extra={"base_model_id": base_model_id, "artifact": artifact.name},
    )
    return model


def generate_from_personal_adapter(
    adapter_dir: Path,
    *,
    base_model_id: str,
    base_checkpoint_basename: str | None,
    conditioning: Any,
    prefix_composition: Any = None,
    seed: int | None = None,
    sample_config: Any = None,
) -> tuple[Any, Any]:
    """Load a saved adapter and sample through the existing decode path."""
    from app.music_transformer.inference import sample_and_decode
    from app.music_transformer.reproducibility import seed_everything
    from app.music_transformer.schemas import MusicTransformerSampleConfigV1
    from app.tokenizer.schemas import TOKENIZER_VERSION, default_tokenizer_config
    from app.tokenizer.vocab import build_vocab

    if seed is not None:
        seed_everything(seed)
    model = load_personal_lora_model(
        adapter_dir,
        base_model_id=base_model_id,
        base_checkpoint_basename=base_checkpoint_basename,
    )
    vocab = build_vocab(default_tokenizer_config())
    sample = sample_config or MusicTransformerSampleConfigV1(greedy=True, max_new_tokens=64)
    composition, report = sample_and_decode(
        model,
        conditioning=conditioning,
        prefix_composition=prefix_composition,
        sample_config=sample,
        device="cpu",
        tokenizer_version=TOKENIZER_VERSION,
        vocab_hash_prefix=vocab.vocab_hash[:12],
    )
    logger.info(
        "[FIX] Personal torch sample-and-decode finished",
        extra={
            "base_model_id": base_model_id,
            "generated_tokens": report.generated_tokens,
            "notes_out": report.notes_out,
            "repair_result": report.repair_result,
        },
    )
    return composition, report
