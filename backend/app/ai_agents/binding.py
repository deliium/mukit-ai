"""Per-agent model binding via ``ai_runtime`` resolve + env/request overrides."""

from __future__ import annotations

import logging
import os
from typing import Any, Mapping

from app.ai_agents.errors import AgentModelUnresolvedError
from app.ai_agents.schemas import (
    AgentResourceHints,
    BoundModelPublic,
    CostClass,
    LocalityPreference,
    MusicAgentCapability,
)
from app.ai_runtime.capabilities import ModelCapability
from app.ai_runtime.errors import AiRuntimeError
from app.ai_runtime.operations import AiOperation
from app.ai_runtime.routing import ModelSelectionInput, resolve_model_for_operation
from app.ai_runtime.types import ModelDescriptor, ResolvedModel

logger = logging.getLogger(__name__)

# Default AiOperation used when resolving a bound model for each agent.
AGENT_DEFAULT_OPERATION: dict[str, AiOperation] = {
    "creative_director": AiOperation.GENERATE_PLANNER,
    "structure_form": AiOperation.GENERATE_PLANNER,
    "harmony": AiOperation.REHARMONIZE_AI,
    "melody_motif": AiOperation.GENERATE_COMPOSER,
    "arrangement": AiOperation.ARRANGE_PREVIEW,
    "orchestration": AiOperation.ARRANGE_PREVIEW,
    "performance_expression": AiOperation.GENERATE_PLANNER,
    "production": AiOperation.AUDIO_RENDER,
    "critic": AiOperation.GENERATE_PLANNER,
}

AGENT_ENV_MODEL_KEY_TEMPLATE = "AI_AGENT_{agent_id}_MODEL"


def agent_model_env_key(agent_id: str) -> str:
    return AGENT_ENV_MODEL_KEY_TEMPLATE.format(agent_id=agent_id.upper())


def resolve_agent_model(
    agent_id: str,
    *,
    overrides: Mapping[str, str] | None = None,
    selection: Any | None = None,
    env: Mapping[str, str] | None = None,
    operation: AiOperation | None = None,
    collapse_reserved_generate: bool = False,
) -> ResolvedModel:
    """Resolve the model bound to an agent.

    Order: request ``overrides[agent_id]`` → ``AI_AGENT_<ID>_MODEL`` → operation default.
    """
    source = env if env is not None else os.environ
    op = operation or AGENT_DEFAULT_OPERATION.get(agent_id)
    if op is None:
        raise AgentModelUnresolvedError(
            f"No default operation for agent {agent_id}",
            agent_id=agent_id,
        )

    override_id = None
    if overrides:
        override_id = (overrides.get(agent_id) or "").strip() or None
    if not override_id:
        override_id = (source.get(agent_model_env_key(agent_id)) or "").strip() or None

    sel = ModelSelectionInput.from_selection(selection)
    if override_id:
        sel = ModelSelectionInput(model_id=override_id, provider=sel.provider, model=sel.model)

    try:
        resolved = resolve_model_for_operation(
            op,
            sel,
            env=source,
            collapse_reserved_generate=collapse_reserved_generate,
        )
    except AiRuntimeError as exc:
        logger.warning(
            "Agent model unresolved",
            extra={"agent_id": agent_id, "operation": op.value, "error": type(exc).__name__},
        )
        raise AgentModelUnresolvedError(
            f"Could not resolve model for agent {agent_id}",
            agent_id=agent_id,
            details={"operation": op.value, "reason": getattr(exc, "code", type(exc).__name__)},
        ) from exc

    logger.info(
        "Agent model bound",
        extra={
            "agent_id": agent_id,
            "model_id": resolved.resolved_model_id,
            "operation": op.value,
            "path": resolved.resolution_path,
        },
    )
    return resolved


def bound_model_public(resolved: ResolvedModel) -> BoundModelPublic:
    d = resolved.descriptor
    return BoundModelPublic(
        model_id=resolved.resolved_model_id,
        runtime=d.runtime,
        capability=d.primary_capability.value,
        status=d.status,
        locality=d.locality,
    )


def resource_hints_from_descriptor(
    *,
    locality_preference: LocalityPreference = "any",
    bound: ModelDescriptor | None = None,
    parallelizable: bool = False,
) -> AgentResourceHints:
    uses_gpu = False
    cost: CostClass = "low"
    locality: LocalityPreference = locality_preference
    if bound is not None:
        locality = bound.locality if locality_preference == "any" else locality_preference
        limits = bound.limits or {}
        uses_gpu = bool(limits.get("uses_gpu") or limits.get("gpu"))
        if bound.locality == "local" and (bound.runtime.startswith("fake") or bound.id.startswith("fake:")):
            cost = "free_local"
        elif bound.locality == "remote":
            cost = "medium"
        if uses_gpu:
            cost = "high"
    return AgentResourceHints(
        locality_preference=locality,
        estimated_cost_class=cost,
        parallelizable=parallelizable,
        uses_gpu=uses_gpu,
    )


def capability_for_agent_id(agent_id: str) -> MusicAgentCapability:
    return MusicAgentCapability(agent_id)


def required_model_capabilities_for(agent_id: str) -> list[ModelCapability]:
    mapping: dict[str, list[ModelCapability]] = {
        "creative_director": [ModelCapability.LANGUAGE_PLANNER],
        "structure_form": [ModelCapability.LANGUAGE_PLANNER],
        "harmony": [ModelCapability.SYMBOLIC_EDITOR],
        "melody_motif": [ModelCapability.SYMBOLIC_COMPOSER, ModelCapability.SYMBOLIC_EDITOR],
        "arrangement": [ModelCapability.SYMBOLIC_EDITOR],
        "orchestration": [ModelCapability.SYMBOLIC_EDITOR],
        "performance_expression": [ModelCapability.LANGUAGE_PLANNER],
        "production": [ModelCapability.AUDIO_GENERATION],
        "critic": [ModelCapability.LANGUAGE_PLANNER],
    }
    return list(mapping.get(agent_id, []))
