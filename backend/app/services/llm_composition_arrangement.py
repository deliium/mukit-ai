"""Multi-candidate composition arrangement orchestration.

Each candidate is an independent provider invocation + realization + validation.
Failures of one candidate never mutate another. Partial success returns valid
candidates with rejected-attempt summaries; total failure raises a sanitized
``arrangement_candidate_exhausted`` (502).
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

from pydantic import ValidationError

from app.arrangement_schemas import (
    ARRANGEMENT_ALGORITHM_VERSION,
    ARRANGEMENT_WARNING_CODES,
    ArrangementCandidate,
    ArrangementRejectedAttempt,
    ArrangementRejectedStage,
    CompositionArrangementDraft,
    CompositionArrangementError,
    CompositionArrangementPreviewRequest,
    CompositionArrangementPreviewResponse,
    normalized_arrangement_request_fingerprint_payload,
    validate_arrangement_request_limits,
)
from app.llm_settings import LLMProviderSettings, LLMSettings, load_llm_settings
from app.services.composition_arrangement_context import (
    ArrangementSourceContext,
    arrangement_context_prompt_payload,
    build_arrangement_source_context,
)
from app.services.composition_arrangement_patch import realize_arrangement_draft
from app.services.composition_arrangement_validation import validate_arrangement_candidate
from app.services.composition_edit_fingerprint import (
    canonical_edit_json_dumps,
    composition_edit_fingerprint,
    edit_fingerprint_log_prefix,
)
from app.services.llm_music_generator import (
    InvalidLLMOutputError,
    LLMGenerationError,
    NoLLMProviderConfiguredError,
    UnsupportedLLMProviderError,
    select_llm_provider,
)


logger = logging.getLogger(__name__)

CREATIVE_DIRECTIONS = (
    "preserve melody contour; redistribute support across target instruments",
    "favor clearer bass register and restrained accompaniment density",
    "emphasize ensemble blend while keeping protected material exact",
    "increase textural contrast without inventing alternate melodies",
)

_WARNING_ALLOWLIST = ARRANGEMENT_WARNING_CODES


def derive_arrangement_candidate_id(
    *,
    edit_source_fingerprint: str,
    request: CompositionArrangementPreviewRequest,
    candidate_fingerprint: str,
    candidate_ordinal: int,
    catalog_fingerprint: str,
    algorithm_version: str = ARRANGEMENT_ALGORITHM_VERSION,
) -> str:
    """Stable arrangement candidate id from source, request, catalog, and ordinal."""
    if candidate_ordinal < 1:
        raise ValueError("candidate_ordinal must be >= 1")
    payload = {
        "algorithm_version": algorithm_version,
        "candidate_fingerprint": candidate_fingerprint,
        "candidate_ordinal": candidate_ordinal,
        "catalog_fingerprint": catalog_fingerprint,
        "edit_source_fingerprint": edit_source_fingerprint,
        "request": normalized_arrangement_request_fingerprint_payload(request),
    }
    encoded = canonical_edit_json_dumps(payload)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    logger.debug(
        "Arrangement candidate id derived",
        extra={
            "algorithm_version": algorithm_version,
            "candidate_ordinal": candidate_ordinal,
            "edit_source_prefix": edit_fingerprint_log_prefix(edit_source_fingerprint),
            "candidate_prefix": edit_fingerprint_log_prefix(candidate_fingerprint),
            "catalog_prefix": edit_fingerprint_log_prefix(catalog_fingerprint),
            "candidate_id_prefix": edit_fingerprint_log_prefix(digest),
            "encoded_bytes": len(encoded),
        },
    )
    return digest


def _select_arrangement_provider(
    request: CompositionArrangementPreviewRequest,
    settings: LLMSettings,
) -> LLMProviderSettings:
    try:
        return select_llm_provider(
            provider=request.selection.provider,
            model=request.selection.model,
            settings=settings,
        )
    except NoLLMProviderConfiguredError as exc:
        raise CompositionArrangementError(
            "arrangement_provider_unavailable",
            http_status=503,
            details={"reason": "no_providers"},
        ) from exc
    except UnsupportedLLMProviderError as exc:
        raise CompositionArrangementError(
            "arrangement_provider_unavailable",
            http_status=503,
            details={"reason": "unsupported_provider"},
        ) from exc


def _instruction_meta(instruction: str | None) -> dict[str, Any]:
    text = (instruction or "").strip()
    return {
        "instruction_len": len(text),
        "instruction_digest": hashlib.sha256(text.encode("utf-8")).hexdigest()[:12] if text else None,
    }


def _build_arrangement_prompt(
    *,
    request: CompositionArrangementPreviewRequest,
    context_payload: dict[str, Any],
    candidate_ordinal: int,
    creative_direction: str,
    repair_codes: list[str] | None = None,
) -> str:
    hard = {
        "operation": request.operation,
        "source_track_ids": list(request.source_track_ids),
        "protected_track_ids": list(request.protected_track_ids),
        "before_part_count": request.instrumentation.before_part_count,
        "after_part_count": request.instrumentation.after_part_count,
        "allow_unlisted_after": request.allow_unlisted_after,
        "preserve_melody": request.preserve_melody,
        "preserve_harmony": request.preserve_harmony,
        "range_adjustment": request.range_adjustment,
        "candidate_ordinal": candidate_ordinal,
        "creative_direction": creative_direction,
        "immutable_source": True,
        "relative_draft_only": True,
        "no_provider_ids_programs_channels": True,
        "tracks_events_only_playable": True,
    }
    repair_block = ""
    if repair_codes:
        repair_block = (
            "\nRepair the previous draft using only these diagnostic codes "
            f"(do not dump notes): {', '.join(repair_codes[:16])}\n"
        )
    return (
        "You are rearranging a canonical composition.v2 document.\n"
        "Return ONLY a relative CompositionArrangementDraft JSON object.\n"
        "Do not return a full composition. Do not invent persistent ids, channels, or programs.\n"
        "Reference only provided transient source_note refs. Preserve protected material.\n"
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
) -> CompositionArrangementDraft:
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise LLMGenerationError("LangChain OpenAI dependencies are not installed") from exc

    client = ChatOpenAI(
        api_key=provider.api_key,
        base_url=provider.base_url,
        model=provider.model,
        temperature=0.35 if temperature is None else temperature,
        timeout=timeout_seconds,
    )
    structured = client.with_structured_output(CompositionArrangementDraft)
    logger.debug(
        "Calling arrangement LLM provider",
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
            f"LLM provider request failed during composition arrangement: {type(exc).__name__}"
        ) from exc

    if isinstance(result, CompositionArrangementDraft):
        return result
    try:
        return CompositionArrangementDraft.model_validate(result)
    except ValidationError as exc:
        raise InvalidLLMOutputError("Provider returned invalid arrangement draft schema") from exc


async def _generate_one_candidate_draft(
    *,
    request: CompositionArrangementPreviewRequest,
    provider: LLMProviderSettings,
    context: ArrangementSourceContext,
    context_payload: dict[str, Any],
    candidate_ordinal: int,
    repair_codes: list[str] | None = None,
) -> CompositionArrangementDraft:
    from app.services.fake_llm import FakeLLMError, draft_fake_composition_arrangement, is_fake_provider

    creative_direction = CREATIVE_DIRECTIONS[(candidate_ordinal - 1) % len(CREATIVE_DIRECTIONS)]
    if is_fake_provider(provider):
        try:
            return await draft_fake_composition_arrangement(
                request,
                provider,
                context=context,
                candidate_ordinal=candidate_ordinal,
                creative_direction=creative_direction,
                repair_codes=repair_codes,
            )
        except FakeLLMError as exc:
            raise InvalidLLMOutputError(str(exc)) from exc

    prompt = _build_arrangement_prompt(
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


def _filter_warning_codes(*groups: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for code in group:
            item = code.strip()
            if not item or item in seen or item not in _WARNING_ALLOWLIST:
                continue
            seen.add(item)
            cleaned.append(item)
    return cleaned[:32]


def _rejected_attempt(
    *,
    ordinal: int,
    stage: ArrangementRejectedStage,
    codes: list[str],
    reasons: list[str] | None = None,
) -> ArrangementRejectedAttempt:
    stable_codes = [code for code in codes if code][:8] or ["arrangement_draft_invalid"]
    return ArrangementRejectedAttempt(
        ordinal=ordinal,
        stage=stage,
        codes=stable_codes,
        reasons=list(reasons or [])[:8],
    )


def _stage_from_error_code(code: str) -> ArrangementRejectedStage:
    mapping: dict[str, ArrangementRejectedStage] = {
        "arrangement_draft_invalid": "draft_validation",
        "arrangement_preservation_failed": "preservation",
        "arrangement_range_failed": "range",
        "arrangement_inventory_mismatch": "realization",
        "arrangement_invalid_source": "context",
        "arrangement_request_too_large": "request",
        "arrangement_provider_error": "provider",
    }
    return mapping.get(code, "realization")


async def run_composition_arrangement_preview(
    request: CompositionArrangementPreviewRequest,
    settings: LLMSettings | None = None,
) -> CompositionArrangementPreviewResponse:
    """Generate 1-4 independently realized arrangement candidates."""
    started = time.perf_counter()
    validate_arrangement_request_limits(request)
    active = settings or load_llm_settings()
    provider = _select_arrangement_provider(request, active)
    model = request.selection.model or provider.model

    # Context freezes source fingerprint and rejects over-limit selections before LLM.
    context = build_arrangement_source_context(request)
    edit_source_fingerprint = context.edit_source_fingerprint
    context_payload = arrangement_context_prompt_payload(context)

    logger.info(
        "Composition arrangement preview started",
        extra={
            "provider": provider.provider,
            "model": model,
            "operation": request.operation,
            "candidate_count": request.candidate_count,
            "source_track_count": len(request.source_track_ids),
            "protected_track_count": len(request.protected_track_ids),
            "before_part_count": request.instrumentation.before_part_count,
            "after_part_count": request.instrumentation.after_part_count,
            "catalog_version": context.catalog_version,
            "edit_source_prefix": edit_fingerprint_log_prefix(edit_source_fingerprint),
            "catalog_prefix": edit_fingerprint_log_prefix(context.catalog_fingerprint),
            **_instruction_meta(request.instruction),
        },
    )

    candidates: list[ArrangementCandidate] = []
    rejected_attempts: list[ArrangementRejectedAttempt] = []
    warning_codes: list[str] = list(context.warning_codes)

    for ordinal in range(1, request.candidate_count + 1):
        candidate_started = time.perf_counter()
        outcome_code = "ok"
        attempt = 0
        max_repairs = request.options.max_repairs
        repair_codes: list[str] = []
        realized = None
        validation = None
        last_error: Exception | None = None
        last_stage: ArrangementRejectedStage = "provider"
        last_reasons: list[str] = []

        while attempt <= max_repairs:
            stage = "draft" if attempt == 0 else "repair"
            try:
                logger.info(
                    "Composition arrangement candidate stage",
                    extra={
                        "provider": provider.provider,
                        "model": model,
                        "candidate_ordinal": ordinal,
                        "operation": request.operation,
                        "stage": stage,
                        "attempt": attempt,
                    },
                )
                draft = await _generate_one_candidate_draft(
                    request=request,
                    provider=provider,
                    context=context,
                    context_payload=context_payload,
                    candidate_ordinal=ordinal,
                    repair_codes=repair_codes or None,
                )
                realized = realize_arrangement_draft(
                    request,
                    draft,
                    context=context,
                    candidate_ordinal=ordinal,
                )
                validation = validate_arrangement_candidate(
                    request,
                    realized,
                    context=context,
                )
                if not validation.ok:
                    outcome_code = (validation.rejection_codes or validation.error_codes or ["arrangement_preservation_failed"])[0]
                    last_stage = validation.rejection_stage or _stage_from_error_code(outcome_code)
                    last_reasons = list(validation.rejection_reasons)
                    repair_codes = list(validation.rejection_codes or validation.error_codes)[:16]
                    if not repair_codes:
                        repair_codes = [outcome_code]
                    attempt += 1
                    if attempt <= max_repairs:
                        warning_codes.append("candidate_repaired")
                        logger.info(
                            "Composition arrangement candidate repair scheduled",
                            extra={
                                "candidate_ordinal": ordinal,
                                "attempt": attempt,
                                "outcome_code": outcome_code,
                                "diagnostic_code_count": len(repair_codes),
                            },
                        )
                        realized = None
                        validation = None
                        continue
                    break
                break
            except CompositionArrangementError as exc:
                last_error = exc
                outcome_code = exc.code
                last_stage = _stage_from_error_code(exc.code)
                detail_codes = list(exc.details.get("error_codes") or [])[:16]
                repair_codes = detail_codes or [exc.code]
                last_reasons = [exc.message] if exc.message else []
                attempt += 1
                if attempt <= max_repairs:
                    warning_codes.append("candidate_repaired")
                    logger.info(
                        "Composition arrangement candidate repair scheduled",
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
                last_stage = "provider" if isinstance(exc, LLMGenerationError) else "draft_validation"
                repair_codes = ["arrangement_draft_invalid"]
                last_reasons = [type(exc).__name__]
                attempt += 1
                if attempt <= max_repairs:
                    warning_codes.append("candidate_repaired")
                    continue
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                outcome_code = type(exc).__name__
                last_stage = "provider"
                last_reasons = [type(exc).__name__]
                break

        elapsed_ms = int((time.perf_counter() - candidate_started) * 1000)
        if realized is None or validation is None or not validation.ok:
            reject_codes = repair_codes or [outcome_code]
            if len(rejected_attempts) < 16:
                rejected_attempts.append(
                    _rejected_attempt(
                        ordinal=ordinal,
                        stage=last_stage if attempt > max_repairs else (last_stage or "repair"),
                        codes=reject_codes,
                        reasons=last_reasons,
                    )
                )
            if validation and validation.warning_codes:
                warning_codes.extend(validation.warning_codes)
            else:
                warning_codes.append("candidate_failed_validation")
            logger.info(
                "Composition arrangement candidate failed",
                extra={
                    "provider": provider.provider,
                    "model": model,
                    "candidate_ordinal": ordinal,
                    "operation": request.operation,
                    "stage": last_stage,
                    "attempt": attempt,
                    "elapsed_ms": elapsed_ms,
                    "outcome_code": outcome_code,
                    "error_type": type(last_error).__name__ if last_error else None,
                    "diagnostic_code_count": len(reject_codes),
                },
            )
            continue

        candidate_fp = composition_edit_fingerprint(realized.composition)
        candidate_id = derive_arrangement_candidate_id(
            edit_source_fingerprint=edit_source_fingerprint,
            request=request,
            candidate_fingerprint=candidate_fp,
            candidate_ordinal=ordinal,
            catalog_fingerprint=context.catalog_fingerprint,
        )
        merged_warnings = _filter_warning_codes(
            list(realized.warning_codes),
            list(validation.warning_codes),
            ["candidate_repaired"] if attempt > 0 else [],
        )
        candidates.append(
            ArrangementCandidate(
                candidate_id=candidate_id,
                candidate_fingerprint=candidate_fp,
                edit_source_fingerprint=edit_source_fingerprint,
                algorithm_version=ARRANGEMENT_ALGORITHM_VERSION,
                catalog_version=context.catalog_version,
                range_policy_version=context.range_policy_version,
                catalog_fingerprint=context.catalog_fingerprint,
                target_profile_fingerprints=list(realized.target_profile_fingerprints),
                operation=request.operation,
                composition=realized.composition,
                provider=provider.provider,  # type: ignore[arg-type]
                model=model,
                before_inventory=list(realized.before_inventory),
                after_inventory=list(realized.after_inventory),
                manifest=realized.manifest,
                event_counts=realized.event_counts,
                density=validation.density,
                range_findings=list(validation.range_findings)[:64],
                duplicate_findings=list(validation.duplicate_findings)[:64],
                harmony_compatibility=validation.harmony_compatibility,
                assertions=list(validation.assertions)[:64],
                warning_codes=merged_warnings,
            )
        )
        logger.info(
            "Composition arrangement candidate completed",
            extra={
                "provider": provider.provider,
                "model": model,
                "candidate_ordinal": ordinal,
                "operation": request.operation,
                "stage": "realized",
                "attempt": attempt,
                "elapsed_ms": elapsed_ms,
                "outcome_code": "ok",
                "copied_event_count": realized.event_counts.copied,
                "generated_event_count": realized.event_counts.generated,
                "moved_event_count": realized.event_counts.moved,
                "candidate_prefix": edit_fingerprint_log_prefix(candidate_fp),
            },
        )

    if not candidates:
        raise CompositionArrangementError(
            "arrangement_candidate_exhausted",
            http_status=502,
            details={
                "requested_candidate_count": request.candidate_count,
                "returned_candidate_count": 0,
                "rejected_attempt_count": len(rejected_attempts),
                "warning_codes": _filter_warning_codes(warning_codes),
            },
        )

    if len(candidates) < request.candidate_count:
        warning_codes.append("candidate_partial_success")

    response = CompositionArrangementPreviewResponse(
        edit_source_fingerprint=edit_source_fingerprint,
        algorithm_version=ARRANGEMENT_ALGORITHM_VERSION,
        catalog_version=context.catalog_version,
        range_policy_version=context.range_policy_version,
        catalog_fingerprint=context.catalog_fingerprint,
        operation=request.operation,
        requested_candidate_count=request.candidate_count,
        candidates=candidates,
        rejected_attempts=rejected_attempts[:16],
        warning_codes=_filter_warning_codes(warning_codes),
        provider=provider.provider,  # type: ignore[arg-type]
        model=model,
    )
    logger.info(
        "Composition arrangement preview completed",
        extra={
            "provider": provider.provider,
            "model": model,
            "operation": request.operation,
            "requested_candidate_count": request.candidate_count,
            "returned_candidate_count": len(candidates),
            "rejected_attempt_count": len(rejected_attempts),
            "warning_code_count": len(response.warning_codes),
            "edit_source_prefix": edit_fingerprint_log_prefix(edit_source_fingerprint),
            "catalog_prefix": edit_fingerprint_log_prefix(context.catalog_fingerprint),
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "request_fingerprint_keys": sorted(
                normalized_arrangement_request_fingerprint_payload(request).keys()
            ),
        },
    )
    return response
