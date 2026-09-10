"""Multi-candidate composition development orchestration.

Each candidate is an independent provider invocation + realization. Failures of
one candidate never mutate another. Partial success returns valid candidates
with warning codes; total failure raises a sanitized provider/domain error.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

from pydantic import ValidationError

from app.composition_development_schemas import (
    DEVELOPMENT_ALGORITHM_VERSION,
    CompositionDevelopmentDraft,
    CompositionDevelopmentError,
    CompositionDevelopmentPreviewRequest,
    CompositionDevelopmentPreviewResponse,
    DevelopmentCandidate,
    validate_development_request_limits,
    normalized_development_request_fingerprint_payload,
)
from app.llm_settings import LLMProviderSettings, LLMSettings, load_llm_settings
from app.services.composition_development_context import (
    build_development_source_context,
    development_context_prompt_payload,
)
from app.services.composition_development_patch import realize_development_draft
from app.services.composition_development_fingerprint import derive_development_candidate_id
from app.services.composition_edit_fingerprint import (
    composition_edit_fingerprint,
    edit_fingerprint_log_prefix,
)
from app.services.llm_music_generator import (
    InvalidLLMOutputError,
    LLMGenerationError,
    select_llm_provider,
)


logger = logging.getLogger(__name__)

CREATIVE_DIRECTIONS = (
    "stay close to the source contour and rhythm",
    "develop motivic cells with moderate rhythmic variation",
    "increase contrast while keeping orchestration and seam continuity",
    "emphasize harmonic continuation with restrained register shifts",
)


def _select_development_provider(
    request: CompositionDevelopmentPreviewRequest,
    settings: LLMSettings,
) -> LLMProviderSettings:
    return select_llm_provider(
        provider=request.selection.provider,
        model=request.selection.model,
        settings=settings,
    )


def _instruction_meta(instruction: str | None) -> dict[str, Any]:
    text = (instruction or "").strip()
    return {
        "instruction_len": len(text),
        "instruction_digest": hashlib.sha256(text.encode("utf-8")).hexdigest()[:12] if text else None,
    }


def _build_development_prompt(
    *,
    request: CompositionDevelopmentPreviewRequest,
    context_payload: dict[str, Any],
    candidate_ordinal: int,
    creative_direction: str,
    repair_codes: list[str] | None = None,
) -> str:
    hard = {
        "operation": request.operation,
        "development_intent": request.development_intent,
        "variation_strength": request.variation_strength,
        "output_bars": request.output_bars,
        "target_section_type": request.target_section_type,
        "allow_modulation": request.allow_modulation,
        "candidate_ordinal": candidate_ordinal,
        "creative_direction": creative_direction,
        "immutable_source": True,
        "relative_draft_only": True,
        "continue_every_track": True,
        "tracks_events_only_playable": True,
    }
    repair_block = ""
    if repair_codes:
        repair_block = (
            "\nRepair the previous draft using only these diagnostic codes "
            f"(do not dump notes): {', '.join(repair_codes[:16])}\n"
        )
    return (
        "You are extending or varying a canonical composition.v2 document.\n"
        "Return ONLY a relative CompositionDevelopmentDraft JSON object.\n"
        "Do not return a full composition. Do not invent track ids.\n"
        "Preserve the immutable source; bridge the seam musically.\n"
        f"Hard constraints: {hard}\n"
        f"Bounded musical context: {context_payload}\n"
        f"Optional user instruction (bounded): {(request.instruction or '').strip()[:500]!r}\n"
        f"Optional instruction length={_instruction_meta(request.instruction)['instruction_len']}\n"
        f"{repair_block}"
    )


async def _invoke_structured_draft(
    *,
    provider: LLMProviderSettings,
    prompt: str,
    temperature: float | None,
    timeout_seconds: int,
) -> CompositionDevelopmentDraft:
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise LLMGenerationError("LangChain OpenAI dependencies are not installed") from exc

    client = ChatOpenAI(
        api_key=provider.api_key,
        base_url=provider.base_url,
        model=provider.model,
        temperature=0.4 if temperature is None else temperature,
        timeout=timeout_seconds,
    )
    structured = client.with_structured_output(CompositionDevelopmentDraft)
    logger.debug(
        "Calling development LLM provider",
        extra={
            "provider": provider.provider,
            "model": provider.model,
            "prompt_chars": len(prompt),
            "prompt_bytes": len(prompt.encode("utf-8")),
        },
    )
    try:
        result = await structured.ainvoke(prompt)
    except Exception as exc:  # noqa: BLE001
        raise LLMGenerationError(
            f"LLM provider request failed during composition development: {type(exc).__name__}"
        ) from exc

    if isinstance(result, CompositionDevelopmentDraft):
        return result
    try:
        return CompositionDevelopmentDraft.model_validate(result)
    except ValidationError as exc:
        raise InvalidLLMOutputError("Provider returned invalid development draft schema") from exc


async def _generate_one_candidate_draft(
    *,
    request: CompositionDevelopmentPreviewRequest,
    provider: LLMProviderSettings,
    context_payload: dict[str, Any],
    candidate_ordinal: int,
    repair_codes: list[str] | None = None,
) -> CompositionDevelopmentDraft:
    from app.services.fake_llm import FakeLLMError, draft_fake_composition_development, is_fake_provider

    creative_direction = CREATIVE_DIRECTIONS[(candidate_ordinal - 1) % len(CREATIVE_DIRECTIONS)]
    if is_fake_provider(provider):
        try:
            return await draft_fake_composition_development(
                request,
                provider,
                candidate_ordinal=candidate_ordinal,
                creative_direction=creative_direction,
                repair_codes=repair_codes,
            )
        except FakeLLMError as exc:
            raise InvalidLLMOutputError(str(exc)) from exc

    prompt = _build_development_prompt(
        request=request,
        context_payload=context_payload,
        candidate_ordinal=candidate_ordinal,
        creative_direction=creative_direction,
        repair_codes=repair_codes,
    )
    timeout = request.options.timeout_seconds or load_llm_settings().request_timeout_seconds
    return await _invoke_structured_draft(
        provider=provider,
        prompt=prompt,
        temperature=request.options.temperature,
        timeout_seconds=timeout,
    )


async def run_composition_development_preview(
    request: CompositionDevelopmentPreviewRequest,
    settings: LLMSettings | None = None,
) -> CompositionDevelopmentPreviewResponse:
    """Generate 1-4 independently realized development candidates."""
    started = time.perf_counter()
    validate_development_request_limits(request)
    active = settings or load_llm_settings()
    provider = _select_development_provider(request, active)
    model = request.selection.model or provider.model

    # Freeze source fingerprint before any work.
    edit_source_fingerprint = composition_edit_fingerprint(request.composition)
    context = build_development_source_context(request)
    context_payload = development_context_prompt_payload(context)

    logger.info(
        "Composition development preview started",
        extra={
            "provider": provider.provider,
            "model": model,
            "operation": request.operation,
            "intent": request.development_intent,
            "strength": request.variation_strength,
            "candidate_count": request.candidate_count,
            "output_bars": request.output_bars,
            "edit_source_prefix": edit_fingerprint_log_prefix(edit_source_fingerprint),
            **_instruction_meta(request.instruction),
        },
    )

    candidates: list[DevelopmentCandidate] = []
    warning_codes: list[str] = []

    for ordinal in range(1, request.candidate_count + 1):
        candidate_started = time.perf_counter()
        outcome_code = "ok"
        attempt = 0
        max_repairs = request.options.max_repairs
        repair_codes: list[str] = []
        realized = None
        last_error: Exception | None = None

        while attempt <= max_repairs:
            stage = "draft" if attempt == 0 else "repair"
            try:
                logger.info(
                    "Composition development candidate stage",
                    extra={
                        "provider": provider.provider,
                        "model": model,
                        "candidate_ordinal": ordinal,
                        "operation": request.operation,
                        "strength": request.variation_strength,
                        "stage": stage,
                        "attempt": attempt,
                    },
                )
                draft = await _generate_one_candidate_draft(
                    request=request,
                    provider=provider,
                    context_payload=context_payload,
                    candidate_ordinal=ordinal,
                    repair_codes=repair_codes or None,
                )
                realized = realize_development_draft(
                    request,
                    draft,
                    context=context,
                    candidate_ordinal=ordinal,
                )
                break
            except CompositionDevelopmentError as exc:
                last_error = exc
                outcome_code = exc.code
                repair_codes = list(exc.details.get("error_codes") or [])[:16]
                if not repair_codes:
                    repair_codes = [exc.code]
                attempt += 1
                if attempt <= max_repairs:
                    warning_codes.append("candidate_repaired")
                    logger.info(
                        "Composition development candidate repair scheduled",
                        extra={
                            "candidate_ordinal": ordinal,
                            "attempt": attempt,
                            "outcome_code": outcome_code,
                            "diagnostic_code_count": len(repair_codes),
                        },
                    )
                    continue
            except (InvalidLLMOutputError, LLMGenerationError, ValidationError) as exc:
                last_error = exc
                outcome_code = type(exc).__name__
                attempt += 1
                if attempt <= max_repairs:
                    warning_codes.append("candidate_repaired")
                    repair_codes = ["development_draft_invalid"]
                    continue
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                outcome_code = type(exc).__name__
                break

        elapsed_ms = int((time.perf_counter() - candidate_started) * 1000)
        if realized is None:
            warning_codes.append("candidate_failed_validation")
            logger.info(
                "Composition development candidate failed",
                extra={
                    "provider": provider.provider,
                    "model": model,
                    "candidate_ordinal": ordinal,
                    "operation": request.operation,
                    "strength": request.variation_strength,
                    "stage": "failed",
                    "attempt": attempt,
                    "elapsed_ms": elapsed_ms,
                    "outcome_code": outcome_code,
                    "error_type": type(last_error).__name__ if last_error else None,
                },
            )
            continue

        candidate_fp = composition_edit_fingerprint(realized.composition)
        candidate_id = derive_development_candidate_id(
            edit_source_fingerprint=edit_source_fingerprint,
            request=request,
            candidate_fingerprint=candidate_fp,
            candidate_ordinal=ordinal,
        )
        candidates.append(
            DevelopmentCandidate(
                candidate_id=candidate_id,
                candidate_fingerprint=candidate_fp,
                edit_source_fingerprint=edit_source_fingerprint,
                algorithm_version=DEVELOPMENT_ALGORITHM_VERSION,
                operation=request.operation,
                development_intent=request.development_intent,
                variation_strength=request.variation_strength,
                composition=realized.composition,
                source_range=realized.source_range,
                output_range=realized.output_range,
                section_changes=realized.section_changes,
                harmony_changes=realized.harmony_changes,
                motif_changes=realized.motif_changes,
                track_changes=realized.track_changes,
                preservation=realized.preservation,
                identity_diagnostics=list(realized.identity_diagnostics),
                provider=provider.provider,  # type: ignore[arg-type]
                model=model,
                warning_codes=[
                    code
                    for code in realized.warning_codes
                    if code in {
                        "candidate_failed_validation",
                        "candidate_failed_identity",
                        "candidate_repaired",
                        "candidate_partial_success",
                        "context_truncated",
                        "empty_harmony_context",
                        "empty_motif_context",
                        "modulation_at_boundary",
                        "restart_like_opening",
                        "sparse_track_draft",
                        "identity_anchor_weak",
                        "seam_gap_advisory",
                        "seam_leap_advisory",
                        "density_divergence_advisory",
                        "register_divergence_advisory",
                        "rhythm_divergence_advisory",
                        "harmonic_continuity_advisory",
                    }
                ][:32],
            )
        )
        logger.info(
            "Composition development candidate completed",
            extra={
                "provider": provider.provider,
                "model": model,
                "candidate_ordinal": ordinal,
                "operation": request.operation,
                "strength": request.variation_strength,
                "stage": "realized",
                "attempt": attempt,
                "elapsed_ms": elapsed_ms,
                "outcome_code": "ok",
                "created_event_count": realized.created_event_count,
                "candidate_prefix": edit_fingerprint_log_prefix(candidate_fp),
            },
        )

    if not candidates:
        raise CompositionDevelopmentError(
            "development_candidate_exhausted",
            http_status=502,
            details={
                "requested_candidate_count": request.candidate_count,
                "returned_candidate_count": 0,
                "warning_codes": list(dict.fromkeys(warning_codes))[:32],
            },
        )

    if len(candidates) < request.candidate_count:
        warning_codes.append("candidate_partial_success")

    response = CompositionDevelopmentPreviewResponse(
        edit_source_fingerprint=edit_source_fingerprint,
        algorithm_version=DEVELOPMENT_ALGORITHM_VERSION,
        operation=request.operation,
        development_intent=request.development_intent,
        variation_strength=request.variation_strength,
        requested_candidate_count=request.candidate_count,
        candidates=candidates,
        warning_codes=list(dict.fromkeys(warning_codes))[:32],
        provider=provider.provider,  # type: ignore[arg-type]
        model=model,
    )
    logger.info(
        "Composition development preview completed",
        extra={
            "provider": provider.provider,
            "model": model,
            "operation": request.operation,
            "intent": request.development_intent,
            "strength": request.variation_strength,
            "requested_candidate_count": request.candidate_count,
            "returned_candidate_count": len(candidates),
            "warning_code_count": len(response.warning_codes),
            "edit_source_prefix": edit_fingerprint_log_prefix(edit_source_fingerprint),
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "request_fingerprint_keys": sorted(
                normalized_development_request_fingerprint_payload(request).keys()
            ),
        },
    )
    return response
