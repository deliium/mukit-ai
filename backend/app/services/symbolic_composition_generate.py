"""Symbolic note-generation adapter for hybrid / continuation / variation pipelines.

Maps ``CompositionPlan`` (+ optional prefix composition) to tokenizer conditioning and
either the Music Transformer inference path or the deterministic ``fake:symbolic-tiny``
composer. Never invents playable notes from harmony spans alone outside the documented
fake pitch rule.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

from ..composition_plan_schemas import CompositionPlan, summarize_composition_plan
from ..composition_schemas import CompositionV2
from ..tokenizer.schemas import TokenizerConditioningV1
from .fake_symbolic_composer import (
    FAKE_SYMBOLIC_MODEL_ID,
    FakeSymbolicComposerError,
    generate_fake_symbolic_composition,
)


logger = logging.getLogger(__name__)

SymbolicBackend = Literal["fake", "music_transformer", "plugin"]

SYMBOLIC_COMPOSER_MODEL_ID_MT = "local:music-transformer"
SYMBOLIC_GENERATE_FAILED = "symbolic_generate_failed"
SYMBOLIC_UNAVAILABLE = "symbolic_composer_unavailable"


class SymbolicCompositionGenerateError(RuntimeError):
    def __init__(self, message: str, *, code: str = SYMBOLIC_GENERATE_FAILED) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SymbolicGenerateResult:
    composition: CompositionV2
    report: dict[str, Any]
    model_id: str
    backend: SymbolicBackend
    seed: int | None
    conditioning: TokenizerConditioningV1 | None


def plan_to_tokenizer_conditioning(
    plan: CompositionPlan,
    *,
    genre: str | None = None,
    mood: str | None = None,
) -> TokenizerConditioningV1:
    """Bridge plan → tokenizer conditioning (genre/key when confidently present)."""
    form = plan.form
    instrument_set = None
    if form.instrumentation:
        # Bounded label only — not artist/style ids.
        instrument_set = ",".join(form.instrumentation[:6])
    section_type = form.sections[0].type if form.sections else None
    conditioning = TokenizerConditioningV1(
        key=form.key,
        genre=genre,
        mood=mood,
        instrument_set=instrument_set,
        section_type=section_type,
    )
    logger.debug(
        "Built tokenizer conditioning from plan",
        extra={
            "has_key": bool(conditioning.key),
            "has_genre": bool(conditioning.genre),
            "has_mood": bool(conditioning.mood),
            "has_instrument_set": bool(conditioning.instrument_set),
            "section_type": conditioning.section_type,
            "density": plan.density.global_band,
        },
    )
    return conditioning


def resolve_symbolic_backend(
    *,
    prefer_fake: bool | None = None,
    env: Mapping[str, str] | None = None,
) -> SymbolicBackend:
    """Choose fake vs Music Transformer backend.

    Fake is used when ``prefer_fake`` is True, ``LLM_FAKE_MODE`` is truthy, or no
    checkpoint is configured. Real MT requires checkpoint + optional API gate.
    """
    source = env if env is not None else os.environ
    if prefer_fake is True:
        return "fake"
    fake_mode = (source.get("LLM_FAKE_MODE") or "").strip().lower() in {"1", "true", "yes", "on"}
    if prefer_fake is None and fake_mode:
        return "fake"
    checkpoint = (source.get("MUSIC_TRANSFORMER_CHECKPOINT") or "").strip()
    if not checkpoint:
        return "fake"
    return "music_transformer"


def symbolic_composer_available(
    *,
    prefer_fake: bool | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[bool, str, str | None]:
    """Return (ready, model_id, unavailable_reason_code)."""
    backend = resolve_symbolic_backend(prefer_fake=prefer_fake, env=env)
    if backend == "fake":
        return True, FAKE_SYMBOLIC_MODEL_ID, None
    source = env if env is not None else os.environ
    api_enabled = (source.get("MUSIC_TRANSFORMER_API_ENABLED") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    checkpoint = (source.get("MUSIC_TRANSFORMER_CHECKPOINT") or "").strip()
    if not checkpoint:
        return False, SYMBOLIC_COMPOSER_MODEL_ID_MT, SYMBOLIC_UNAVAILABLE
    # API gate mirrors opt-in HTTP path; graph may still call MT when checkpoint set
    # even if API disabled — but discovery marks ready only when API or explicit allow.
    allow_graph = (source.get("MUSIC_TRANSFORMER_GRAPH_ENABLED") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not api_enabled and not allow_graph:
        return False, SYMBOLIC_COMPOSER_MODEL_ID_MT, SYMBOLIC_UNAVAILABLE
    from app.services.model_path_resolve import (
        MODEL_PATH_REJECTED,
        ModelPathRejectedError,
        resolve_music_transformer_checkpoint,
    )

    try:
        path = resolve_music_transformer_checkpoint(checkpoint, env=source)
    except ModelPathRejectedError:
        logger.warning(
            "Music Transformer checkpoint path rejected",
            extra={
                "model_id": SYMBOLIC_COMPOSER_MODEL_ID_MT,
                "checkpoint_basename": Path(checkpoint).name,
                "code": MODEL_PATH_REJECTED,
            },
        )
        return False, SYMBOLIC_COMPOSER_MODEL_ID_MT, MODEL_PATH_REJECTED
    if not path.is_file():
        logger.warning(
            "Music Transformer checkpoint missing",
            extra={
                "model_id": SYMBOLIC_COMPOSER_MODEL_ID_MT,
                "checkpoint_basename": path.name,
                "code": SYMBOLIC_UNAVAILABLE,
            },
        )
        return False, SYMBOLIC_COMPOSER_MODEL_ID_MT, SYMBOLIC_UNAVAILABLE
    return True, SYMBOLIC_COMPOSER_MODEL_ID_MT, None


def _generate_via_personal(
    plan: CompositionPlan,
    *,
    model_id: str,
    seed: int | None,
    conditioning: TokenizerConditioningV1,
    prefix_composition: CompositionV2 | None = None,
    env: Mapping[str, str] | None = None,
) -> SymbolicGenerateResult:
    """Run a completed personal adapter. Incomplete rows are unavailable."""
    from app.ai_runtime.errors import ModelNotFoundError, ModelUnavailableError
    from app.services.personal_composer_store import get_by_registry_id

    row = get_by_registry_id(model_id)
    if row is None:
        raise ModelNotFoundError(f"personal composer {model_id} is not registered")
    if row.job.status != "complete":
        raise ModelUnavailableError(f"personal composer {model_id} is not ready")
    logger.info(
        "Personal composer generate started",
        extra={"composer_model_id": model_id, "engine": row.job.engine},
    )
    if row.job.engine == "fake":
        try:
            music, report = generate_fake_symbolic_composition(
                plan,
                seed=seed,
                prefix_composition=prefix_composition,
            )
        except FakeSymbolicComposerError as exc:
            raise SymbolicCompositionGenerateError(str(exc), code=SYMBOLIC_GENERATE_FAILED) from exc
        return SymbolicGenerateResult(
            composition=music,
            report=report,
            model_id=model_id,
            backend="fake",
            seed=seed,
            conditioning=conditioning,
        )
    del env
    from app.music_transformer.errors import (
        MusicTransformerDependencyError,
        MusicTransformerGenerateError,
    )
    from app.personal_composer.trainer import generate_from_personal_adapter
    from app.personal_composer_settings import load_personal_composer_settings

    adapter_dir = load_personal_composer_settings().root / row.job.adapter_id
    try:
        music, mt_report = generate_from_personal_adapter(
            adapter_dir,
            base_model_id=row.job.base_model_id,
            base_checkpoint_basename=row.job.manifest.base_checkpoint_basename,
            conditioning=conditioning,
            prefix_composition=prefix_composition,
            seed=seed,
        )
    except MusicTransformerDependencyError as exc:
        raise SymbolicCompositionGenerateError(
            "PyTorch is not installed for this personal composer",
            code=SYMBOLIC_UNAVAILABLE,
        ) from exc
    except (MusicTransformerGenerateError, FileNotFoundError, OSError) as exc:
        logger.error(
            "[FIX] Personal torch generate failed",
            extra={"composer_model_id": model_id, "error_type": type(exc).__name__},
        )
        raise SymbolicCompositionGenerateError(str(exc), code=SYMBOLIC_GENERATE_FAILED) from exc
    return SymbolicGenerateResult(
        composition=music,
        report=mt_report.model_dump(mode="json"),
        model_id=model_id,
        backend="music_transformer",
        seed=seed,
        conditioning=conditioning,
    )


def _generate_via_lab(
    plan: CompositionPlan,
    *,
    model_id: str,
    seed: int | None,
    conditioning: TokenizerConditioningV1,
    prefix_composition: CompositionV2 | None = None,
    env: Mapping[str, str] | None = None,
) -> SymbolicGenerateResult:
    """Run a registered Model Lab checkpoint. Fake never calls ``load_checkpoint``."""
    from pathlib import Path

    from app.ai_runtime.errors import ModelNotFoundError, ModelUnavailableError
    from app.model_lab_settings import load_model_lab_settings
    from app.services.model_lab_store import get_by_registry_id

    row = get_by_registry_id(model_id)
    if row is None:
        raise ModelNotFoundError(f"model lab {model_id} is not registered")
    if row.status != "complete" or row.registered_checkpoint_step is None:
        raise ModelUnavailableError(f"model lab {model_id} is not ready")
    logger.info(
        "Model Lab generate started",
        extra={"composer_model_id": model_id, "engine": row.engine},
    )
    if row.engine == "fake":
        try:
            music, report = generate_fake_symbolic_composition(
                plan,
                seed=seed,
                prefix_composition=prefix_composition,
            )
        except FakeSymbolicComposerError as exc:
            raise SymbolicCompositionGenerateError(str(exc), code=SYMBOLIC_GENERATE_FAILED) from exc
        return SymbolicGenerateResult(
            composition=music,
            report=report,
            model_id=model_id,
            backend="fake",
            seed=seed,
            conditioning=conditioning,
        )
    del env
    from app.music_transformer.errors import (
        MusicTransformerDependencyError,
        MusicTransformerGenerateError,
    )
    from app.music_transformer.inference import generate_composition

    settings = load_model_lab_settings()
    step = int(row.registered_checkpoint_step)
    checkpoint = settings.root / row.id / "checkpoints" / f"step_{step:08d}.pt"
    if not checkpoint.is_file():
        raise ModelUnavailableError(f"model lab checkpoint missing for {model_id}")
    # Defense-in-depth: only Lab-owned paths under MODEL_LAB_ROOT/<id>/checkpoints/.
    try:
        checkpoint.resolve().relative_to((settings.root / row.id / "checkpoints").resolve())
    except ValueError as exc:
        raise ModelUnavailableError(f"model lab checkpoint path refused for {model_id}") from exc
    try:
        music, mt_report = generate_composition(
            checkpoint,
            conditioning=conditioning,
            prefix_composition=prefix_composition,
            seed=seed,
            device="cpu",
        )
    except MusicTransformerDependencyError as exc:
        raise SymbolicCompositionGenerateError(
            "PyTorch is not installed for this Model Lab composer",
            code=SYMBOLIC_UNAVAILABLE,
        ) from exc
    except (MusicTransformerGenerateError, FileNotFoundError, OSError) as exc:
        logger.error(
            "Model Lab torch generate failed",
            extra={"composer_model_id": model_id, "error_type": type(exc).__name__},
        )
        raise SymbolicCompositionGenerateError(str(exc), code=SYMBOLIC_GENERATE_FAILED) from exc
    return SymbolicGenerateResult(
        composition=music,
        report=mt_report.model_dump(mode="json"),
        model_id=model_id,
        backend="music_transformer",
        seed=seed,
        conditioning=conditioning,
    )


def generate_symbolic_composition(
    plan: CompositionPlan,
    *,
    seed: int | None = None,
    prefix_composition: CompositionV2 | None = None,
    genre: str | None = None,
    mood: str | None = None,
    prefer_fake: bool | None = None,
    env: Mapping[str, str] | None = None,
    resample_attempt: int = 0,
    model_id: str | None = None,
) -> SymbolicGenerateResult:
    """Generate Composition V2 notes via fake, Music Transformer, or a plugin composer."""
    effective_seed = None if seed is None else int(seed) + int(resample_attempt)
    conditioning = plan_to_tokenizer_conditioning(plan, genre=genre, mood=mood)
    if model_id and str(model_id).startswith("personal:"):
        return _generate_via_personal(
            plan,
            model_id=model_id,
            seed=effective_seed,
            conditioning=conditioning,
            prefix_composition=prefix_composition,
            env=env,
        )
    if model_id and str(model_id).startswith("lab:"):
        return _generate_via_lab(
            plan,
            model_id=model_id,
            seed=effective_seed,
            conditioning=conditioning,
            prefix_composition=prefix_composition,
            env=env,
        )
    if model_id:
        plugin_result = _generate_via_plugin(
            plan,
            model_id=model_id,
            seed=effective_seed,
            conditioning=conditioning,
            genre=genre,
            mood=mood,
        )
        if plugin_result is not None:
            return plugin_result
    backend = resolve_symbolic_backend(prefer_fake=prefer_fake, env=env)

    logger.info(
        "Symbolic composition generate started",
        extra={
            "backend": backend,
            "seed": effective_seed,
            "resample_attempt": resample_attempt,
            "has_prefix": prefix_composition is not None,
            "bar_count": plan.form.bar_count,
            "plan_summary": {
                k: summarize_composition_plan(plan).get(k)
                for k in ("schema_version", "section_count", "theme_enabled", "density_global")
            },
        },
    )

    if backend == "fake":
        try:
            music, report = generate_fake_symbolic_composition(
                plan,
                seed=effective_seed,
                prefix_composition=prefix_composition,
            )
        except FakeSymbolicComposerError as exc:
            raise SymbolicCompositionGenerateError(str(exc), code=SYMBOLIC_GENERATE_FAILED) from exc
        return SymbolicGenerateResult(
            composition=music,
            report=report,
            model_id=FAKE_SYMBOLIC_MODEL_ID,
            backend="fake",
            seed=effective_seed,
            conditioning=conditioning,
        )

    return _generate_via_music_transformer(
        plan,
        conditioning=conditioning,
        seed=effective_seed,
        prefix_composition=prefix_composition,
        env=env,
    )


def _generate_via_plugin(
    plan: CompositionPlan,
    *,
    model_id: str,
    seed: int | None,
    conditioning: TokenizerConditioningV1,
    genre: str | None,
    mood: str | None,
) -> SymbolicGenerateResult | None:
    """Run a plugin composer when ``model_id`` is an active symbolic plugin.

    Non-plugin ids return None so the caller can keep fake / Music Transformer.
    This path does not call ``resolve_symbolic_backend``.
    """
    from app.ai_runtime.capabilities import ModelCapability
    from app.ai_runtime.errors import ModelNotFoundError
    from app.ai_runtime.registry import get_model
    from app.plugin_host.invoke import PluginHost
    from app.plugin_sdk.errors import PluginError

    try:
        descriptor = get_model(model_id)
    except ModelNotFoundError as exc:
        logger.info("symbolic plugin compose", extra={"model_id": model_id, "valid": False})
        raise SymbolicCompositionGenerateError("plugin_not_found", code="plugin_not_found") from exc
    if descriptor.runtime != "plugin" or descriptor.primary_capability != ModelCapability.SYMBOLIC_COMPOSER:
        logger.debug(
            "symbolic model id is not a plugin composer",
            extra={"model_id": model_id, "runtime": descriptor.runtime},
        )
        return None
    request = {
        "bar_count": plan.form.bar_count,
        "key": plan.form.key,
        "genre": genre,
        "mood": mood,
    }
    host = PluginHost()
    try:
        raw = host.compose(descriptor.id, request)
        music = host.validated_composition(descriptor.id, raw)
    except PluginError as exc:
        logger.info("symbolic plugin compose", extra={"model_id": descriptor.id, "valid": False})
        raise SymbolicCompositionGenerateError(exc.code, code=exc.code) from exc
    note_count = sum(len(track.events) for track in music.tracks)
    logger.info("symbolic plugin compose", extra={"model_id": descriptor.id, "valid": True})
    logger.debug(
        "symbolic plugin compose note count",
        extra={"model_id": descriptor.id, "note_count": note_count},
    )
    return SymbolicGenerateResult(
        composition=music,
        report={
            "model_id": descriptor.id,
            "backend": "plugin",
            "fallback_applied": False,
            "note_count": note_count,
        },
        model_id=descriptor.id,
        backend="plugin",
        seed=seed,
        conditioning=conditioning,
    )


def _generate_via_music_transformer(
    plan: CompositionPlan,
    *,
    conditioning: TokenizerConditioningV1,
    seed: int | None,
    prefix_composition: CompositionV2 | None,
    env: Mapping[str, str] | None,
) -> SymbolicGenerateResult:
    source = env if env is not None else os.environ
    ready, model_id, reason = symbolic_composer_available(prefer_fake=False, env=source)
    if not ready:
        raise SymbolicCompositionGenerateError(
            "Symbolic Music Transformer composer is unavailable",
            code=reason or SYMBOLIC_UNAVAILABLE,
        )
    checkpoint = (source.get("MUSIC_TRANSFORMER_CHECKPOINT") or "").strip()
    from app.services.model_path_resolve import (
        MODEL_PATH_REJECTED,
        ModelPathRejectedError,
        resolve_music_transformer_checkpoint,
    )

    try:
        checkpoint_path = resolve_music_transformer_checkpoint(checkpoint, env=source)
    except ModelPathRejectedError as exc:
        raise SymbolicCompositionGenerateError(
            str(exc),
            code=MODEL_PATH_REJECTED,
        ) from exc
    try:
        from app.music_transformer.inference import generate_composition
        from app.music_transformer.schemas import MusicTransformerSampleConfigV1
    except ImportError as exc:
        raise SymbolicCompositionGenerateError(
            "Music Transformer dependencies are not installed",
            code=SYMBOLIC_UNAVAILABLE,
        ) from exc

    sample = MusicTransformerSampleConfigV1(greedy=True, max_new_tokens=128)
    try:
        music, mt_report = generate_composition(
            checkpoint_path,
            conditioning=conditioning,
            prefix_composition=prefix_composition,
            sample_config=sample,
            seed=seed,
        )
    except Exception as exc:
        logger.error(
            "Music Transformer generate failed",
            extra={
                "model_id": model_id,
                "checkpoint_basename": checkpoint_path.name,
                "seed": seed,
                "error_type": type(exc).__name__,
                "detail": str(exc)[:200],
            },
        )
        raise SymbolicCompositionGenerateError(str(exc), code=SYMBOLIC_GENERATE_FAILED) from exc

    report = {
        "model_id": model_id,
        "runtime": "music_transformer",
        "seed": seed,
        "status": getattr(mt_report, "status", "ok"),
        "prompt_tokens": getattr(mt_report, "prompt_tokens", None),
        "generated_tokens": getattr(mt_report, "generated_tokens", None),
        "decode_result": getattr(getattr(mt_report, "decode", None), "result", None)
        if hasattr(mt_report, "decode")
        else getattr(mt_report, "decode_result", None),
        "checkpoint_basename": checkpoint_path.name,
        "fallback_applied": False,
        "plan_bar_count": plan.form.bar_count,
    }
    logger.info(
        "Music Transformer symbolic generate completed",
        extra={
            "model_id": model_id,
            "seed": seed,
            "prompt_tokens": report.get("prompt_tokens"),
            "generated_tokens": report.get("generated_tokens"),
            "decode_result": report.get("decode_result"),
            "checkpoint_basename": checkpoint_path.name,
        },
    )
    return SymbolicGenerateResult(
        composition=music,
        report=report,
        model_id=model_id,
        backend="music_transformer",
        seed=seed,
        conditioning=conditioning,
    )


__all__ = [
    "SYMBOLIC_COMPOSER_MODEL_ID_MT",
    "SYMBOLIC_GENERATE_FAILED",
    "SYMBOLIC_UNAVAILABLE",
    "SymbolicBackend",
    "SymbolicCompositionGenerateError",
    "SymbolicGenerateResult",
    "generate_symbolic_composition",
    "plan_to_tokenizer_conditioning",
    "resolve_symbolic_backend",
    "symbolic_composer_available",
]
