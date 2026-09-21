"""Checkpoint symbolic evaluation runner (writes ``eval/step_*.json``)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Sequence

from app.composition_schemas import CompositionV2
from app.music_transformer.checkpoint import load_checkpoint
from app.music_transformer.constraints import default_constraints
from app.music_transformer.data import EncodedExample, prompt_ids_from_composition
from app.music_transformer.device import resolve_device
from app.music_transformer.errors import MusicTransformerEvalError
from app.music_transformer.eval_metrics import compute_metrics_for_samples
from app.music_transformer.experiment_schemas import (
    MusicTransformerEvalConfigV1,
    MusicTransformerEvalReportV1,
)
from app.music_transformer.generate import generate_ids
from app.music_transformer.model import MusicTransformerLM
from app.music_transformer.schemas import MusicTransformerSampleConfigV1
from app.music_transformer.settings import load_music_transformer_settings
from app.tokenizer import special_tokens as st
from app.tokenizer.schemas import TokenizerConfigV1, default_tokenizer_config
from app.tokenizer.vocab import build_vocab


logger = logging.getLogger(__name__)


def evaluate_checkpoint(
    checkpoint: Path | str,
    *,
    eval_config: MusicTransformerEvalConfigV1,
    out_dir: Path | str,
    global_step: int | None = None,
    experiment_id: str | None = None,
    val_examples: Sequence[EncodedExample] | None = None,
    device: str | None = None,
    tokenizer_config: TokenizerConfigV1 | None = None,
    prefix_composition: CompositionV2 | dict[str, Any] | None = None,
) -> MusicTransformerEvalReportV1:
    """Run symbolic eval against a checkpoint; write step + latest JSON reports."""
    if not eval_config.enabled:
        logger.info(
            "Eval skipped (disabled)",
            extra={"experiment_id": experiment_id, "global_step": global_step},
        )
        report = MusicTransformerEvalReportV1(
            experiment_id=experiment_id,
            global_step=global_step,
            sample_count=0,
            musical_quality_claim=False,
            metrics=[],
            disclaimer=(
                "Symbolic metrics are validity/distributional diagnostics only; "
                "they are not musical quality scores."
            ),
        )
        return report

    ckpt_path = Path(checkpoint)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    tok_cfg = tokenizer_config or default_tokenizer_config()
    settings = load_music_transformer_settings()
    resolved = resolve_device(device, settings=settings)

    step = global_step if global_step is not None else 0
    logger.info(
        "Eval starting",
        extra={
            "experiment_id": experiment_id,
            "global_step": step,
            "mode": eval_config.mode,
            "max_samples": eval_config.max_samples,
            "checkpoint_basename": ckpt_path.name,
        },
    )

    try:
        if eval_config.mode == "teacher_forced":
            samples = _teacher_forced_samples(
                val_examples=val_examples,
                max_samples=eval_config.max_samples,
            )
        else:
            samples = _generate_from_prefix_samples(
                ckpt_path,
                eval_config=eval_config,
                val_examples=val_examples,
                prefix_composition=prefix_composition,
                tok_cfg=tok_cfg,
                device=resolved,
            )
    except MusicTransformerEvalError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Eval failed",
            extra={
                "experiment_id": experiment_id,
                "global_step": step,
                "error_type": type(exc).__name__,
            },
        )
        raise MusicTransformerEvalError(
            "eval_failed",
            f"Evaluation failed: {type(exc).__name__}",
            details={"error_type": type(exc).__name__},
        ) from exc

    report = compute_metrics_for_samples(
        samples,
        metrics=eval_config.metrics,
        tokenizer_config=tok_cfg,
        experiment_id=experiment_id,
        global_step=step,
    )
    _write_eval_report(report, out, step=step)

    rates = {
        m.name: m.value
        for m in report.metrics
        if m.name in ("valid_token_rate", "valid_composition_decode_rate")
    }
    logger.info(
        "Eval finished",
        extra={
            "experiment_id": experiment_id,
            "global_step": step,
            "sample_count": report.sample_count,
            "valid_token_rate": rates.get("valid_token_rate"),
            "valid_composition_decode_rate": rates.get("valid_composition_decode_rate"),
            "musical_quality_claim": False,
        },
    )
    return report


def _teacher_forced_samples(
    *,
    val_examples: Sequence[EncodedExample] | None,
    max_samples: int,
) -> list[dict[str, Any]]:
    examples = list(val_examples or [])[:max_samples]
    if not examples:
        logger.warning(
            "Teacher-forced eval has no val examples",
            extra={"max_samples": max_samples},
        )
        return []
    return [
        {"token_ids": list(ex.input_ids), "status": "teacher_forced"}
        for ex in examples
    ]


def _generate_from_prefix_samples(
    checkpoint: Path,
    *,
    eval_config: MusicTransformerEvalConfigV1,
    val_examples: Sequence[EncodedExample] | None,
    prefix_composition: CompositionV2 | dict[str, Any] | None,
    tok_cfg: TokenizerConfigV1,
    device: str,
) -> list[dict[str, Any]]:
    vocab = build_vocab(tok_cfg)
    payload, card = load_checkpoint(
        checkpoint,
        map_location=device,
        require_tokenizer_version=True,
        tokenizer_config=tok_cfg,
    )
    model = MusicTransformerLM(card.architecture)
    model.load_state_dict(payload["model_state_dict"])
    model.to(device)
    model.eval()

    sample_cfg = MusicTransformerSampleConfigV1(
        greedy=eval_config.greedy,
        max_new_tokens=eval_config.max_new_tokens,
        on_invalid=eval_config.on_invalid,
        temperature=0.0 if eval_config.greedy else 1.0,
    )
    constraints = default_constraints(
        ban_pad=sample_cfg.ban_pad,
        family_transition_hook=sample_cfg.family_transition_hook,
    )

    prompts: list[list[int]] = []
    examples = list(val_examples or [])[: eval_config.max_samples]
    if examples:
        eos = vocab.token_to_id.get(st.EOS)
        for ex in examples:
            ids = list(ex.input_ids)
            if eos is not None and ids and ids[-1] == eos:
                ids = ids[:-1]
            if ids:
                prompts.append(ids)
    elif prefix_composition is not None:
        prompts.append(
            prompt_ids_from_composition(
                prefix_composition,
                config=tok_cfg,
                vocab=vocab,
                drop_eos=True,
            )
        )
    else:
        logger.warning(
            "Generate-from-prefix eval has no val examples or prefix",
            extra={"max_samples": eval_config.max_samples},
        )
        return []

    samples: list[dict[str, Any]] = []
    for prompt in prompts[: eval_config.max_samples]:
        try:
            full_ids, stop = generate_ids(
                model,
                prompt,
                vocab,
                sample_cfg,
                device=device,
                constraints=constraints,
            )
            samples.append(
                {
                    "token_ids": full_ids,
                    "status": "ok",
                    "stop_reason": stop,
                }
            )
        except Exception as exc:  # noqa: BLE001 — keep eval going
            logger.warning(
                "Eval sample generate failed",
                extra={
                    "error_type": type(exc).__name__,
                    "prompt_tokens": len(prompt),
                },
            )
            samples.append(
                {
                    "token_ids": list(prompt),
                    "status": "error",
                }
            )
    return samples


def _write_eval_report(
    report: MusicTransformerEvalReportV1,
    out_dir: Path,
    *,
    step: int,
) -> None:
    payload = report.model_dump(mode="json")
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    step_path = out_dir / f"step_{step:08d}.json"
    latest_path = out_dir / "latest.json"
    step_path.write_text(text, encoding="utf-8")
    latest_path.write_text(text, encoding="utf-8")
    logger.info(
        "Eval report written",
        extra={
            "step_basename": step_path.name,
            "sample_count": report.sample_count,
            "global_step": step,
        },
    )
