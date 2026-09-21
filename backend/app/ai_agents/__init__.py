"""V4 multi-agent music layer — specialized agents above ``ai_runtime``.

Agents are in-process capabilities/services under this package. They:
- advertise typed descriptors (``MusicAgentCapability``, never ``ModelCapability``);
- exchange typed ``agent.artifact.v1`` envelopes (no freeform prompt bus);
- hold immutable ``AgentWorkflowContext`` slots + progressive ``working_draft_composition``;
- never write SQLite / never mutate durable Composition themselves.

Canonical playable schema remains ``composition.v2``. Product **V4** means
multi-agent orchestration; ``composition.v4`` is unsupported.

V4 must-not-break checklist (inventory anchors — Task 1 / Task 12):
  V4-INV-01  GET /ai/models still lists fake models under LLM_FAKE_MODE
  V4-INV-02  llm_only generate returns V2 + provenance without requiring agent_id
  V4-INV-03  hybrid_plan_symbolic generate returns V2 + provenance without requiring agent_id
  V4-INV-04  arrangement / development / reharm preview remain stateless (no project write)
  V4-INV-05  Default /generate pipeline stays llm_only (multi-agent is a separate route)
  V4-INV-06  ai_agents/ must not reference project persistence modules or PROJECT_DB_PATH
"""

from __future__ import annotations

import logging

from .schemas import (
    AgentArtifactV1,
    AgentDescriptor,
    AgentRunRequest,
    AgentRunResult,
    AgentWorkflowContext,
    MusicAgentCapability,
)

logger = logging.getLogger(__name__)

logger.debug(
    "ai_agents package imported",
    extra={
        "checklist_ids": [
            "V4-INV-01",
            "V4-INV-02",
            "V4-INV-03",
            "V4-INV-04",
            "V4-INV-05",
            "V4-INV-06",
        ]
    },
)

__all__ = [
    "AgentArtifactV1",
    "AgentDescriptor",
    "AgentRunRequest",
    "AgentRunResult",
    "AgentWorkflowContext",
    "MusicAgentCapability",
]
