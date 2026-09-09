"""Bounded LangGraph drafting/repair for creative motif variations."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Literal, Sequence, TypedDict

from pydantic import ValidationError

from app.composition_schemas import CompositionV2
from app.llm_settings import LLMProviderSettings, LLMSettings, load_llm_settings
from app.motif_schemas import MotifApplyRequest
from app.schemas import LLMGenerationOptions
from app.services.composition_motif_similarity import CREATIVE_OPERATIONS, identity_threshold
from app.services.composition_motif_transform import (
    AnswerCounterphraseProposal,
    MelodicVariationProposal,
    MotifDestinationSpec,
    MotifTransformError,
    MotifTransformResult,
    RelativeMotifNote,
    RhythmicVariationProposal,
    realize_melodic_variation,
    realize_rhythmic_variation,
    validate_answer_or_counterphrase,
)
from app.services.llm_music_generator import (
    InvalidLLMOutputError,
    LLMGenerationError,
    _extract_json,
)


logger = logging.getLogger(__name__)

MOTIF_EDIT_STAGES = (
    "draft_variation",
    "validate_variation",
    "repair_variation",
    "apply_variation",
)

MAX_MOTIF_REPAIR_ATTEMPTS = 1

CreativeMotifOperation = Literal[
    "rhythmic_variation",
    "melodic_variation",
    "answer",
    "counterphrase",
]


@dataclass(frozen=True)
class CreativeMotifDraftOutcome:
    transform_result: MotifTransformResult
    warnings: tuple[str, ...]
    provider: LLMProviderSettings


class _MotifEditState(TypedDict, total=False):
    operation: CreativeMotifOperation
    source_notes: tuple[RelativeMotifNote, ...]
    composition: CompositionV2
    destination: MotifDestinationSpec
    id_seed: str
    variation_strength: float
    provider: LLMProviderSettings
    options: LLMGenerationOptions
    destination_section_id: str | None
    destination_start_bar: int
    destination_track_id: str
    prompt_context: dict[str, Any]
    raw_output: str
    parsed_json: dict[str, Any]
    proposal: dict[str, Any]
    transform_result: MotifTransformResult
    warnings: list[str]
    diagnostics: list[dict[str, Any]]
    validation_ok: bool
    current_stage: str
    failed_stage: str
    repair_count: int
    retry_limit: int
    identity_score: float | None


def _mutation_budget(operation: str, variation_strength: float) -> dict[str, int]:
    strength = max(0.0, min(1.0, variation_strength))
    max_onset_delta = int(round(30 + 150 * strength))
    max_duration_delta = int(round(30 + 120 * strength))
    max_pitch_offset = int(round(1 + 5 * strength))
    budget = {
        "max_onset_delta_ticks": max_onset_delta,
        "max_duration_delta_ticks": max_duration_delta,
        "max_pitch_semitone_offset": max_pitch_offset,
    }
    logger.debug(
        "Computed motif mutation budget",
        extra={"operation": operation, "variation_strength": strength, **budget},
    )
    return budget


def _relative_cells_payload(notes: Sequence[RelativeMotifNote]) -> list[dict[str, int]]:
    return [
        {
            "relative_start_tick": note.relative_start_tick,
            "duration_ticks": note.duration_ticks,
            "pitch_semitone_offset": note.pitch_semitone_offset,
        }
        for note in notes
    ]


def _bounded_prompt_context(
    composition: CompositionV2,
    *,
    destination: MotifDestinationSpec,
    destination_section_id: str | None,
    destination_start_bar: int,
    destination_track_id: str,
) -> dict[str, Any]:
    section = None
    if destination_section_id:
        section = next((item for item in composition.sections if item.id == destination_section_id), None)
    if section is None:
        for item in composition.sections:
            end_bar = item.start_bar + item.bar_count - 1
            if item.start_bar <= destination_start_bar <= end_bar:
                section = item
                break

    track = destination.track
    section_summary = None
    if section is not None:
        section_summary = {
            "id": section.id,
            "type": section.type,
            "start_bar": section.start_bar,
            "bar_count": section.bar_count,
        }

    harmony_summary: list[dict[str, Any]] = []
    if section is not None:
        section_end = section.start_bar + section.bar_count - 1
        from app.services.composition_harmony_spans import (
            harmony_change_points_by_bar,
        )
        from app.services.composition_timeline import compile_timeline

        timeline = compile_timeline(composition)
        by_bar = harmony_change_points_by_bar(
            composition.harmony,
            boundaries=timeline.bar_boundaries,
            duration_ticks=timeline.duration_ticks,
            bar_count=timeline.bar_count,
        )
        for bar in range(section.start_bar, section_end + 1):
            chord = by_bar.get(bar)
            if chord is None:
                continue
            harmony_summary.append({"bar": bar, "chord": chord})
            if len(harmony_summary) >= 8:
                break

    return {
        "tempo": composition.tempo,
        "key": composition.key,
        "time_signature": composition.time_signature,
        "ticks_per_quarter": composition.ticks_per_quarter,
        "section": section_summary,
        "track": {
            "id": track.id,
            "role": track.role,
            "instrument": track.instrument,
            "name": track.name,
        },
        "destination_start_tick": destination.start_tick,
        "destination_track_id": destination_track_id,
        "destination_start_bar": destination_start_bar,
        "harmony": harmony_summary,
    }


def _proposal_schema(operation: CreativeMotifOperation, note_count: int) -> dict[str, Any]:
    if operation == "rhythmic_variation":
        return {
            "note_count": note_count,
            "onset_delta_ticks": [0] + [0] * (note_count - 1),
            "duration_ticks": [480] * note_count,
        }
    if operation == "melodic_variation":
        return {
            "note_count": note_count,
            "pitch_semitone_offsets": [0] * note_count,
        }
    return {
        "note_count": note_count,
        "pitch_semitone_offsets": [0] * note_count,
        "onset_delta_ticks": None,
        "duration_ticks": None,
    }


def _build_draft_prompt(
    *,
    operation: CreativeMotifOperation,
    source_notes: Sequence[RelativeMotifNote],
    variation_strength: float,
    prompt_context: dict[str, Any],
    diagnostics: list[dict[str, Any]] | None,
) -> str:
    note_count = len(source_notes)
    budget = _mutation_budget(operation, variation_strength)
    threshold = identity_threshold(operation, variation_strength)
    cells = _relative_cells_payload(source_notes)
    schema = _proposal_schema(operation, note_count)

    rules = [
        "Return ONLY a JSON object matching the proposal schema.",
        "Use relative motif cells only; do not emit absolute pitches or full composition events.",
        "Keep note_count identical to the source motif.",
        "Stay within the mutation budget derived from variation_strength.",
        f"Transformed material must remain recognizable (identity score >= {threshold}).",
        "Do not include unrelated composition events, analysis reports, or event arrays.",
    ]
    if operation == "rhythmic_variation":
        rules.append("onset_delta_ticks[0] must be 0; duration_ticks entries must be positive.")
        rules.append("Preserve pitch_semitone_offset from the source (proposal has no pitch fields).")
    elif operation == "melodic_variation":
        rules.append("pitch_semitone_offsets are deltas added to each source pitch_semitone_offset.")
    else:
        rules.append(
            "pitch_semitone_offsets are required; onset_delta_ticks/duration_ticks may be null to keep source rhythm."
        )
        if operation == "answer":
            rules.append("Produce a responsive answer phrase that still resembles the source motif.")
        else:
            rules.append("Produce a counterphrase that remains related to the source motif.")

    prompt = (
        "You are proposing a constrained creative motif variation for Composition V2.\n"
        f"Operation: {operation}\n"
        f"Variation strength: {variation_strength}\n"
        f"Identity threshold: {threshold}\n"
        f"Mutation budget: {json.dumps(budget, ensure_ascii=True)}\n"
        f"Context summary: {json.dumps(prompt_context, ensure_ascii=True)}\n"
        f"Source relative cells ({note_count}): {json.dumps(cells, ensure_ascii=True)}\n"
        f"Rules:\n- " + "\n- ".join(rules) + "\n"
        f"Proposal schema example:\n{json.dumps(schema, ensure_ascii=True)}\n"
    )
    if diagnostics:
        prompt += (
            "Previous proposal failed validation. Fix these diagnostics without exceeding the mutation budget:\n"
            f"{json.dumps(diagnostics, ensure_ascii=True)}\n"
        )
    return prompt


def _parse_proposal(
    operation: CreativeMotifOperation,
    parsed: dict[str, Any],
) -> RhythmicVariationProposal | MelodicVariationProposal | AnswerCounterphraseProposal:
    if operation == "rhythmic_variation":
        return RhythmicVariationProposal.model_validate(parsed)
    if operation == "melodic_variation":
        return MelodicVariationProposal.model_validate(parsed)
    return AnswerCounterphraseProposal.model_validate(parsed)


def _realize_proposal(
    *,
    operation: CreativeMotifOperation,
    proposal: RhythmicVariationProposal | MelodicVariationProposal | AnswerCounterphraseProposal,
    source_notes: Sequence[RelativeMotifNote],
    composition: CompositionV2,
    destination: MotifDestinationSpec,
    id_seed: str,
    variation_strength: float,
) -> MotifTransformResult:
    if operation == "rhythmic_variation":
        assert isinstance(proposal, RhythmicVariationProposal)
        return realize_rhythmic_variation(
            source_notes,
            proposal,
            composition=composition,
            destination=destination,
            id_seed=id_seed,
            variation_strength=variation_strength,
        )
    if operation == "melodic_variation":
        assert isinstance(proposal, MelodicVariationProposal)
        return realize_melodic_variation(
            source_notes,
            proposal,
            composition=composition,
            destination=destination,
            id_seed=id_seed,
            variation_strength=variation_strength,
        )
    assert isinstance(proposal, AnswerCounterphraseProposal)
    return validate_answer_or_counterphrase(
        source_notes,
        proposal,
        operation=operation,
        composition=composition,
        destination=destination,
        id_seed=id_seed,
        variation_strength=variation_strength,
    )


async def draft_creative_motif_transform(
    *,
    request: MotifApplyRequest,
    provider: LLMProviderSettings,
    source_notes: Sequence[RelativeMotifNote],
    composition: CompositionV2,
    destination: MotifDestinationSpec,
    id_seed: str,
    settings: LLMSettings | None = None,
) -> CreativeMotifDraftOutcome:
    """Draft/validate/repair a creative motif proposal and realize it deterministically."""
    _ = settings or load_llm_settings()
    operation = request.operation
    if operation not in CREATIVE_OPERATIONS:
        raise InvalidLLMOutputError(f"Unsupported creative motif operation: {operation}")

    from app.services.fake_llm import FakeLLMError, draft_fake_motif_variation, is_fake_provider

    variation_strength = float(request.variation_strength if request.variation_strength is not None else 0.5)
    model_name = request.selection.model or provider.model

    if is_fake_provider(provider):
        logger.info(
            "Routing creative motif draft to fake LLM provider",
            extra={
                "provider": provider.provider,
                "model": model_name,
                "operation": operation,
                "variation_strength": variation_strength,
                "source_note_count": len(source_notes),
            },
        )
        try:
            return await draft_fake_motif_variation(
                operation=operation,  # type: ignore[arg-type]
                source_notes=source_notes,
                composition=composition,
                destination=destination,
                id_seed=id_seed,
                variation_strength=variation_strength,
                provider=provider,
            )
        except FakeLLMError as exc:
            raise InvalidLLMOutputError(str(exc)) from exc

    prompt_context = _bounded_prompt_context(
        composition,
        destination=destination,
        destination_section_id=request.destination.section_id,
        destination_start_bar=request.destination.start_bar,
        destination_track_id=request.destination.track_id,
    )
    logger.info(
        "LLM creative motif draft started",
        extra={
            "provider": provider.provider,
            "model": model_name,
            "operation": operation,
            "variation_strength": variation_strength,
            "source_note_count": len(source_notes),
            "destination_track_id": request.destination.track_id,
            "destination_start_bar": request.destination.start_bar,
        },
    )

    state: _MotifEditState = {
        "operation": operation,  # type: ignore[typeddict-item]
        "source_notes": tuple(source_notes),
        "composition": composition,
        "destination": destination,
        "id_seed": id_seed,
        "variation_strength": variation_strength,
        "provider": provider,
        "options": request.options,
        "destination_section_id": request.destination.section_id,
        "destination_start_bar": request.destination.start_bar,
        "destination_track_id": request.destination.track_id,
        "prompt_context": prompt_context,
        "warnings": [],
        "diagnostics": [],
        "validation_ok": False,
        "repair_count": 0,
        "retry_limit": min(request.options.max_retries, MAX_MOTIF_REPAIR_ATTEMPTS),
        "current_stage": "draft_variation",
        "identity_score": None,
    }

    graph = _build_motif_edit_graph()
    final_state: _MotifEditState = await graph.ainvoke(state)

    if not final_state.get("validation_ok") or final_state.get("transform_result") is None:
        diagnostics = final_state.get("diagnostics") or []
        logger.error(
            "LLM creative motif draft failed after validation/repair",
            extra={
                "provider": provider.provider,
                "model": model_name,
                "operation": operation,
                "failed_stage": final_state.get("failed_stage") or final_state.get("current_stage"),
                "repair_count": final_state.get("repair_count", 0),
                "diagnostic_count": len(diagnostics),
                "diagnostic_codes": [item.get("code") for item in diagnostics[:10]],
            },
        )
        detail = diagnostics[0].get("message") if diagnostics else "Creative motif proposal validation failed"
        raise InvalidLLMOutputError(str(detail)[:500])

    transform_result = final_state["transform_result"]
    warnings = list(final_state.get("warnings") or [])
    logger.info(
        "LLM creative motif draft completed",
        extra={
            "provider": provider.provider,
            "model": model_name,
            "operation": operation,
            "repair_count": final_state.get("repair_count", 0),
            "identity_score": transform_result.verification.components.combined_score,
            "created_event_count": len(transform_result.events),
            "warning_count": len(warnings),
        },
    )
    return CreativeMotifDraftOutcome(
        transform_result=transform_result,
        warnings=tuple(warnings),
        provider=provider,
    )


def _build_motif_edit_graph():
    try:
        from langgraph.graph import END, StateGraph
    except ImportError as exc:
        raise LLMGenerationError("LangGraph dependencies are not installed") from exc

    logger.info("Building LLM creative motif edit graph", extra={"stages": list(MOTIF_EDIT_STAGES)})
    workflow = StateGraph(_MotifEditState)
    workflow.add_node("draft_variation", _draft_variation)
    workflow.add_node("validate_variation", _validate_variation)
    workflow.add_node("repair_variation", _repair_variation)
    workflow.add_node("apply_variation", _apply_variation_noop)

    workflow.set_entry_point("draft_variation")
    workflow.add_edge("draft_variation", "validate_variation")
    workflow.add_conditional_edges(
        "validate_variation",
        _route_after_validation,
        {
            "apply": "apply_variation",
            "repair": "repair_variation",
            "fail": END,
        },
    )
    workflow.add_edge("repair_variation", "validate_variation")
    workflow.add_edge("apply_variation", END)
    return workflow.compile()


async def _draft_variation(state: _MotifEditState) -> _MotifEditState:
    operation = state["operation"]
    logger.debug(
        "Motif edit graph stage transition",
        extra={"stage": "draft_variation", "from_stage": state.get("current_stage"), "operation": operation},
    )
    prompt = _build_draft_prompt(
        operation=operation,
        source_notes=state["source_notes"],
        variation_strength=state["variation_strength"],
        prompt_context=state["prompt_context"],
        diagnostics=None,
    )
    try:
        raw_output = await _invoke_motif_chat(state, prompt)
        parsed = _extract_json(raw_output)
        if not isinstance(parsed, dict):
            raise ValueError("Motif proposal JSON must be an object")
        proposal_model = _parse_proposal(operation, parsed)
        proposal = proposal_model.model_dump(mode="json")
        diagnostics: list[dict[str, Any]] = []
    except (json.JSONDecodeError, ValidationError, ValueError, TypeError, InvalidLLMOutputError) as exc:
        logger.warning(
            "Draft motif variation parse failed",
            extra={
                "stage": "draft_variation",
                "operation": operation,
                "error_type": type(exc).__name__,
                "code": "motif_proposal_parse_failed",
            },
        )
        raw_output = state.get("raw_output") or ""
        parsed = {}
        proposal = {}
        diagnostics = [
            {
                "code": "motif_proposal_parse_failed",
                "message": f"LLM returned invalid motif proposal JSON: {str(exc)[:200]}",
            }
        ]
    except LLMGenerationError:
        raise

    logger.debug(
        "Drafted motif variation proposal shape",
        extra={
            "stage": "draft_variation",
            "operation": operation,
            "prompt_length": len(prompt),
            "has_proposal": bool(proposal),
            "diagnostic_count": len(diagnostics),
        },
    )
    return {
        **state,
        "raw_output": raw_output,
        "parsed_json": parsed if isinstance(parsed, dict) else {},
        "proposal": proposal,
        "diagnostics": diagnostics,
        "validation_ok": False,
        "current_stage": "draft_variation",
        "failed_stage": "draft_variation" if diagnostics else "",
    }


def _validate_variation(state: _MotifEditState) -> _MotifEditState:
    operation = state["operation"]
    logger.debug(
        "Motif edit graph stage transition",
        extra={"stage": "validate_variation", "from_stage": state.get("current_stage"), "operation": operation},
    )
    existing_diagnostics = list(state.get("diagnostics") or [])
    proposal_payload = state.get("proposal") or {}
    if not proposal_payload:
        diagnostic = existing_diagnostics[0] if existing_diagnostics else {
            "code": "motif_proposal_missing",
            "message": "No motif variation proposal was produced",
        }
        logger.warning(
            "Motif variation validation rejected",
            extra={
                "stage": "validate_variation",
                "operation": operation,
                "diagnostic_codes": [diagnostic.get("code")],
                "repair_count": state.get("repair_count", 0),
            },
        )
        return {
            **state,
            "validation_ok": False,
            "diagnostics": [diagnostic],
            "failed_stage": "validate_variation",
            "current_stage": "validate_variation",
            "identity_score": None,
        }

    try:
        proposal = _parse_proposal(operation, proposal_payload)
        transform_result = _realize_proposal(
            operation=operation,
            proposal=proposal,
            source_notes=state["source_notes"],
            composition=state["composition"],
            destination=state["destination"],
            id_seed=state["id_seed"],
            variation_strength=state["variation_strength"],
        )
    except (ValidationError, ValueError, TypeError) as exc:
        diagnostic = {
            "code": "motif_proposal_parse_failed",
            "message": f"Motif proposal schema invalid: {str(exc)[:200]}",
        }
        logger.warning(
            "Motif variation validation rejected",
            extra={
                "stage": "validate_variation",
                "operation": operation,
                "diagnostic_codes": [diagnostic["code"]],
                "repair_count": state.get("repair_count", 0),
            },
        )
        return {
            **state,
            "validation_ok": False,
            "diagnostics": [diagnostic],
            "failed_stage": "validate_variation",
            "current_stage": "validate_variation",
            "identity_score": None,
        }
    except MotifTransformError as exc:
        diagnostic = {
            "code": exc.code,
            "message": str(exc),
            "context": {
                key: value
                for key, value in exc.context.items()
                if isinstance(value, (str, int, float, bool)) or value is None
            },
        }
        logger.warning(
            "Motif variation identity/range validation rejected",
            extra={
                "stage": "validate_variation",
                "operation": operation,
                "diagnostic_codes": [exc.code],
                "repair_count": state.get("repair_count", 0),
                "score": None,
            },
        )
        return {
            **state,
            "validation_ok": False,
            "diagnostics": [diagnostic],
            "failed_stage": "validate_variation",
            "current_stage": "validate_variation",
            "identity_score": None,
        }

    score = transform_result.verification.components.combined_score
    warnings = list(state.get("warnings") or []) + list(transform_result.warning_codes)
    logger.debug(
        "Motif variation validation passed",
        extra={
            "stage": "validate_variation",
            "operation": operation,
            "score": score,
            "threshold": transform_result.verification.components.threshold,
            "created_event_count": len(transform_result.events),
        },
    )
    return {
        **state,
        "transform_result": transform_result,
        "warnings": warnings,
        "diagnostics": [],
        "validation_ok": True,
        "failed_stage": "",
        "current_stage": "validate_variation",
        "identity_score": score,
    }


async def _repair_variation(state: _MotifEditState) -> _MotifEditState:
    operation = state["operation"]
    diagnostics = state.get("diagnostics") or []
    repair_count = int(state.get("repair_count") or 0) + 1
    logger.warning(
        "Attempting motif variation repair",
        extra={
            "stage": "repair_variation",
            "operation": operation,
            "repair_count": repair_count,
            "diagnostic_count": len(diagnostics),
            "diagnostic_codes": [item.get("code") for item in diagnostics[:10]],
        },
    )
    prompt = _build_draft_prompt(
        operation=operation,
        source_notes=state["source_notes"],
        variation_strength=state["variation_strength"],
        prompt_context=state["prompt_context"],
        diagnostics=diagnostics,
    )
    try:
        raw_output = await _invoke_motif_chat(state, prompt)
        parsed = _extract_json(raw_output)
        if not isinstance(parsed, dict):
            raise ValueError("Motif proposal JSON must be an object")
        proposal_model = _parse_proposal(operation, parsed)
        proposal = proposal_model.model_dump(mode="json")
        next_diagnostics: list[dict[str, Any]] = []
    except (json.JSONDecodeError, ValidationError, ValueError, TypeError, InvalidLLMOutputError) as exc:
        logger.warning(
            "Motif variation repair parse failed",
            extra={
                "stage": "repair_variation",
                "operation": operation,
                "error_type": type(exc).__name__,
                "repair_count": repair_count,
                "code": "motif_proposal_parse_failed",
            },
        )
        raw_output = state.get("raw_output") or ""
        parsed = {}
        proposal = {}
        next_diagnostics = [
            {
                "code": "motif_proposal_parse_failed",
                "message": f"LLM repair returned invalid motif proposal JSON: {str(exc)[:200]}",
            }
        ]
    except LLMGenerationError:
        raise

    logger.debug(
        "Repaired motif variation proposal shape",
        extra={
            "stage": "repair_variation",
            "operation": operation,
            "prompt_length": len(prompt),
            "repair_count": repair_count,
            "has_proposal": bool(proposal),
        },
    )
    return {
        **state,
        "raw_output": raw_output,
        "parsed_json": parsed if isinstance(parsed, dict) else {},
        "proposal": proposal,
        "diagnostics": next_diagnostics,
        "repair_count": repair_count,
        "validation_ok": False,
        "current_stage": "repair_variation",
        "failed_stage": "",
        "identity_score": None,
    }


def _apply_variation_noop(state: _MotifEditState) -> _MotifEditState:
    logger.debug(
        "Motif edit graph stage transition",
        extra={
            "stage": "apply_variation",
            "from_stage": state.get("current_stage"),
            "validation_ok": state.get("validation_ok"),
            "score": state.get("identity_score"),
        },
    )
    return {**state, "current_stage": "apply_variation"}


def _route_after_validation(state: _MotifEditState) -> str:
    if state.get("validation_ok"):
        return "apply"
    repair_count = int(state.get("repair_count") or 0)
    retry_limit = int(state.get("retry_limit") or 0)
    if repair_count < retry_limit:
        return "repair"
    logger.error(
        "Motif variation repair exhausted",
        extra={
            "stage": "validate_variation",
            "operation": state.get("operation"),
            "repair_count": repair_count,
            "retry_limit": retry_limit,
            "diagnostic_codes": [item.get("code") for item in (state.get("diagnostics") or [])[:10]],
        },
    )
    return "fail"


async def _invoke_motif_chat(state: _MotifEditState, prompt: str) -> str:
    provider = state["provider"]
    options = state["options"]
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise LLMGenerationError("LangChain OpenAI dependencies are not installed") from exc

    active_settings = load_llm_settings()
    timeout_seconds = options.timeout_seconds or active_settings.request_timeout_seconds
    temperature = options.temperature
    if temperature is None:
        temperature = active_settings.temperature

    model_name = provider.model
    client = ChatOpenAI(
        api_key=provider.api_key,
        base_url=provider.base_url,
        model=model_name,
        temperature=temperature,
        timeout=timeout_seconds,
    )
    logger.debug(
        "Calling LLM provider for creative motif draft",
        extra={
            "provider": provider.provider,
            "model": model_name,
            "stage": state.get("current_stage"),
            "operation": state.get("operation"),
            "prompt_length": len(prompt),
            "attempt": int(state.get("repair_count") or 0) + 1,
        },
    )
    try:
        response = await client.ainvoke(prompt)
    except Exception as exc:
        logger.error(
            "LLM provider call failed during creative motif draft",
            extra={
                "provider": provider.provider,
                "model": model_name,
                "stage": state.get("current_stage"),
                "operation": state.get("operation"),
                "error_type": type(exc).__name__,
            },
        )
        raise LLMGenerationError(f"LLM provider call failed: {type(exc).__name__}") from exc

    content = getattr(response, "content", response)
    if isinstance(content, list):
        content = "".join(str(part) for part in content)
    return str(content)


__all__ = [
    "CreativeMotifDraftOutcome",
    "MAX_MOTIF_REPAIR_ATTEMPTS",
    "MOTIF_EDIT_STAGES",
    "draft_creative_motif_transform",
]
