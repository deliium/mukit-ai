"""Shared helpers for real (and stub) music agent adapters."""

from __future__ import annotations

import logging
from typing import Any, Mapping

from app.ai_agents.binding import (
    bound_model_public,
    required_model_capabilities_for,
    resolve_agent_model,
    resource_hints_from_descriptor,
)
from app.ai_agents.errors import AgentError, AgentOperationUnsupportedError
from app.ai_agents.schemas import (
    AgentArtifactProvenance,
    AgentDescriptor,
    AgentOperation,
    AgentRunRequest,
    AgentRunResult,
    AgentStatus,
    MusicAgentCapability,
)

logger = logging.getLogger(__name__)

DISPLAY_NAMES: dict[str, str] = {
    "creative_director": "Creative Director",
    "structure_form": "Structure / Form",
    "harmony": "Harmony",
    "melody_motif": "Melody / Motif",
    "arrangement": "Arrangement",
    "orchestration": "Orchestration",
    "performance_expression": "Performance / Expression",
    "production": "Production",
    "critic": "Critic",
}

SUPPORTED_OPS: dict[str, list[AgentOperation]] = {
    "creative_director": [AgentOperation.PLAN, AgentOperation.RUN],
    "structure_form": [AgentOperation.PLAN, AgentOperation.RUN],
    "harmony": [AgentOperation.PLAN, AgentOperation.PROPOSE, AgentOperation.RUN],
    "melody_motif": [AgentOperation.PLAN, AgentOperation.PROPOSE, AgentOperation.RUN],
    "arrangement": [AgentOperation.PROPOSE, AgentOperation.RUN],
    "orchestration": [AgentOperation.PROPOSE, AgentOperation.ADVISE, AgentOperation.RUN],
    "performance_expression": [AgentOperation.ADVISE, AgentOperation.RUN],
    "production": [AgentOperation.ADVISE, AgentOperation.RUN],
    "critic": [AgentOperation.CRITIQUE, AgentOperation.RUN],
}

ACCEPTED_TYPES = ["composition.v2", "agent.brief.v1", "agent.workflow_plan.v1"]
PRODUCED_TYPES = [
    "agent.brief.v1",
    "agent.workflow_plan.v1",
    "agent.critique.v1",
    "composition.analysis.v1",
    "arrangement.candidate",
    "reharmonize.candidate",
    "melody.draft",
    "motif.draft",
]


def build_agent_descriptor(
    agent_id: str,
    *,
    env: Mapping[str, str] | None = None,
    health_detail: str = "ready",
    overrides: Mapping[str, str] | None = None,
) -> AgentDescriptor:
    """Build a discovery descriptor with model binding (degrades softly)."""
    bound_model_id: str | None = None
    status = AgentStatus.READY
    detail = health_detail
    resource = resource_hints_from_descriptor(locality_preference="any")
    try:
        resolved = resolve_agent_model(agent_id, env=env, overrides=overrides)
        bound_model_id = resolved.resolved_model_id
        resource = resource_hints_from_descriptor(
            locality_preference="any",
            bound=resolved.descriptor,
        )
        _ = bound_model_public(resolved)
    except Exception as exc:  # noqa: BLE001 — degrade, do not crash bootstrap
        logger.warning(
            "Agent binding degraded",
            extra={"agent_id": agent_id, "reason": type(exc).__name__},
        )
        status = AgentStatus.DEGRADED
        bound_model_id = bound_model_id or "unresolved"
        detail = "binding_degraded"

    return AgentDescriptor(
        id=agent_id,
        display_name=DISPLAY_NAMES[agent_id],
        capability=MusicAgentCapability(agent_id),
        supported_operations=SUPPORTED_OPS[agent_id],
        accepted_artifact_types=list(ACCEPTED_TYPES),
        produced_artifact_types=list(PRODUCED_TYPES),
        required_model_capabilities=required_model_capabilities_for(agent_id),
        resource=resource,
        status=status,
        bound_model_id=bound_model_id,
        health_detail=detail,
    )


class BaseMusicAgent:
    """Common invoke/provenance surface for real adapters."""

    def __init__(self, descriptor: AgentDescriptor) -> None:
        self._descriptor = descriptor

    @property
    def descriptor(self) -> AgentDescriptor:
        return self._descriptor

    async def run(self, request: AgentRunRequest) -> AgentRunResult:
        if request.agent_id != self._descriptor.id:
            raise AgentOperationUnsupportedError(
                f"Agent id mismatch: {request.agent_id}",
                agent_id=self._descriptor.id,
            )
        if request.operation not in self._descriptor.supported_operations:
            raise AgentOperationUnsupportedError(
                f"Unsupported operation {request.operation.value}",
                agent_id=self._descriptor.id,
            )
        logger.info(
            "Agent run",
            extra={
                "agent_id": self._descriptor.id,
                "operation": request.operation.value,
                "model_id": self._descriptor.bound_model_id,
            },
        )
        return await self._run_impl(request)

    async def _run_impl(self, request: AgentRunRequest) -> AgentRunResult:
        raise NotImplementedError

    def _stage(self, operation: str, *, runtime: str | None = None) -> dict[str, Any]:
        return {
            "operation": operation,
            "agent_id": self._descriptor.id,
            "agent_capability": self._descriptor.capability.value,
            "model_id": self._descriptor.bound_model_id,
            "runtime": runtime or "service_adapter",
            "capability": (
                self._descriptor.required_model_capabilities[0].value
                if self._descriptor.required_model_capabilities
                else None
            ),
            "model_version": self._descriptor.bound_model_id,
        }

    def _provenance(self, operation: str, *, runtime: str | None = None) -> AgentArtifactProvenance:
        stage = self._stage(operation, runtime=runtime)
        return AgentArtifactProvenance(
            operation=stage["operation"],
            model_id=stage.get("model_id"),
            runtime=stage.get("runtime"),
            capability=stage.get("capability"),
            agent_id=stage.get("agent_id"),
            agent_capability=stage.get("agent_capability"),
            model_version=stage.get("model_version"),
        )


def selection_int(selection: Mapping[str, Any], key: str, default: int) -> int:
    raw = selection.get(key, default)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def selection_str(selection: Mapping[str, Any], key: str, default: str | None = None) -> str | None:
    raw = selection.get(key, default)
    if raw is None:
        return default
    text = str(raw).strip()
    return text or default


def ensure_known_agent(agent_id: str) -> None:
    from app.ai_agents.schemas import KNOWN_AGENT_IDS

    if agent_id not in KNOWN_AGENT_IDS:
        raise AgentError(f"Unknown agent id: {agent_id}", agent_id=agent_id, code="agent_not_found")
