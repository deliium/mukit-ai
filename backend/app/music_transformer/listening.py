"""Fixed listening-set generation for human A/B comparison (not quality scoring)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.music_transformer.errors import MusicTransformerEvalError, MusicTransformerGenerateError
from app.music_transformer.experiment_schemas import (
    MusicTransformerListeningConfigV1,
    MusicTransformerListeningSetV1,
)
from app.music_transformer.inference import generate_composition
from app.music_transformer.reproducibility import seed_everything
from app.music_transformer.schemas import MusicTransformerSampleConfigV1
from app.tokenizer.schemas import TokenizerConfigV1, TokenizerConditioningV1


logger = logging.getLogger(__name__)


def load_listening_set(path: Path | str) -> MusicTransformerListeningSetV1:
    """Load and validate ``music_transformer.listening_set.v1`` JSON."""
    set_path = Path(path)
    if not set_path.is_file():
        raise MusicTransformerEvalError(
            "listening_set_missing",
            f"Listening set not found: {set_path.name}",
            details={"basename": set_path.name},
        )
    try:
        raw = json.loads(set_path.read_text(encoding="utf-8"))
        listening_set = MusicTransformerListeningSetV1.model_validate(raw)
    except Exception as exc:  # noqa: BLE001
        raise MusicTransformerEvalError(
            "listening_set_invalid",
            f"Listening set failed validation: {set_path.name}",
            details={"error_type": type(exc).__name__},
        ) from exc
    logger.info(
        "Listening set loaded",
        extra={
            "basename": set_path.name,
            "prompt_count": len(listening_set.prompts),
        },
    )
    return listening_set


def run_listening_set(
    checkpoint: Path | str,
    listening_config: MusicTransformerListeningConfigV1,
    out_dir: Path | str,
    *,
    device: str | None = None,
    tokenizer_config: TokenizerConfigV1 | None = None,
) -> list[dict[str, Any]]:
    """Generate one Composition V2 (+ report) per listening prompt.

    Writes ``{id}__seed{seed}.json`` and ``{id}__seed{seed}.report.json``.
    """
    if not listening_config.enabled and listening_config.set_path is None:
        logger.info("Listening skipped (disabled, no set_path)")
        return []

    set_path = listening_config.set_path
    if not set_path:
        raise MusicTransformerEvalError(
            "listening_set_missing",
            "listening_config.set_path is required",
        )

    listening_set = load_listening_set(set_path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    set_dir = Path(set_path).resolve().parent

    base_sample = listening_config.sample or MusicTransformerSampleConfigV1(
        greedy=True,
        max_new_tokens=64,
    )

    results: list[dict[str, Any]] = []
    for prompt in listening_set.prompts:
        seed_everything(prompt.seed)
        sample_cfg = _merge_sample_config(base_sample, prompt.sample_overrides)
        prefix = _load_prefix(prompt.prefix_path, set_dir=set_dir)
        conditioning = prompt.conditioning or None

        row: dict[str, Any] = {
            "id": prompt.id,
            "seed": prompt.seed,
            "status": "error",
            "notes_out": 0,
        }
        stem = f"{prompt.id}__seed{prompt.seed}"
        try:
            composition, report = generate_composition(
                checkpoint,
                conditioning=_conditioning(conditioning),
                prefix_composition=prefix,
                sample_config=sample_cfg,
                tokenizer_config=tokenizer_config,
                device=device,
                seed=prompt.seed,
            )
            comp_path = out / f"{stem}.json"
            report_path = out / f"{stem}.report.json"
            comp_path.write_text(
                json.dumps(composition.model_dump(mode="json"), indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
            report_path.write_text(
                json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            row.update(
                {
                    "status": report.status,
                    "notes_out": report.notes_out,
                    "composition_basename": comp_path.name,
                    "report_basename": report_path.name,
                }
            )
        except MusicTransformerGenerateError as exc:
            logger.warning(
                "Listening prompt generate rejected",
                extra={
                    "prompt_id": prompt.id,
                    "seed": prompt.seed,
                    "error_code": exc.code,
                },
            )
            row["status"] = "rejected"
            row["error_code"] = exc.code
            _write_error_report(out / f"{stem}.report.json", prompt, exc)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Listening prompt failed",
                extra={
                    "prompt_id": prompt.id,
                    "seed": prompt.seed,
                    "error_type": type(exc).__name__,
                },
            )
            row["status"] = "error"
            row["error_type"] = type(exc).__name__
            _write_error_report(out / f"{stem}.report.json", prompt, exc)

        logger.info(
            "Listening prompt finished",
            extra={
                "prompt_id": prompt.id,
                "seed": prompt.seed,
                "notes_out": row.get("notes_out", 0),
                "status": row["status"],
            },
        )
        results.append(row)
    return results


def _merge_sample_config(
    base: MusicTransformerSampleConfigV1,
    overrides: dict[str, Any],
) -> MusicTransformerSampleConfigV1:
    merged = base.model_dump(mode="json")
    # Listening defaults to greedy unless the set or overrides say otherwise.
    if "greedy" not in overrides:
        merged["greedy"] = True
        if overrides.get("temperature") is None and merged.get("temperature", 1.0) > 0:
            merged["temperature"] = 0.0
    merged.update(overrides)
    return MusicTransformerSampleConfigV1.model_validate(merged)


def _load_prefix(prefix_path: str | None, *, set_dir: Path) -> dict[str, Any] | None:
    if not prefix_path:
        return None
    candidate = Path(prefix_path)
    if not candidate.is_file():
        candidate = set_dir / prefix_path
    if not candidate.is_file():
        # Basename-only relative to set dir
        candidate = set_dir / Path(prefix_path).name
    if not candidate.is_file():
        raise MusicTransformerEvalError(
            "listening_prefix_missing",
            f"Listening prefix not found: {Path(prefix_path).name}",
            details={"basename": Path(prefix_path).name},
        )
    return json.loads(candidate.read_text(encoding="utf-8"))


def _conditioning(
    raw: dict[str, Any] | None,
) -> TokenizerConditioningV1 | dict[str, Any] | None:
    if not raw:
        return None
    try:
        return TokenizerConditioningV1.model_validate(raw)
    except Exception:  # noqa: BLE001 — pass through as dict
        return raw


def _write_error_report(path: Path, prompt: Any, exc: BaseException) -> None:
    payload = {
        "status": "error",
        "prompt_id": getattr(prompt, "id", None),
        "seed": getattr(prompt, "seed", None),
        "error_type": type(exc).__name__,
        "error_code": getattr(exc, "code", None),
        "musical_quality_claim": False,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
