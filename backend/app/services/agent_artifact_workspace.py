"""Immutable agent-artifact workspace (trust-boundary service).

Inventory / non-goals checklist (Task 1 freeze):

(a) Reuse existing contracts:
    - ``AgentArtifactV1`` envelope (``agent.artifact.v1``)
    - ``agent.brief.v1`` / ``agent.critique.v1`` / ``agent.workflow_plan.v1``
    - ``composition.plan.v1`` (CompositionPlan; forbidden playable top-level fields)
    - ``composition.analysis.v1`` (MusicAnalysis sidecar; durable = bounded projection)

(b) Forbidden playable field set (shared with plan schemas):
    tracks, events, notes, note_events, musicxml, midi, wav, composition, music, analysis
    Plans and typed agent payloads must never become alternate playable scores.

(c) Agents under ``ai_agents/`` must NOT write SQLite, import this module, or touch
    ``PROJECT_DB_PATH`` / ``project_store`` / ``project_history_store``.
    Durable writes happen only at router / Apply trust boundaries.

(d) No ``composition.v4``. Canonical playable schema remains ``composition.v2``.
    Product V4 = multi-agent orchestration only.

(e) Default staging is session-only (``artifact_log`` in workflow preview) until
    ``multi-agent-apply``. Optional temporary SQLite rows require
    ``project_id`` + ``persist_workspace_artifacts=true``. Promote durable rows
    + ``revision_artifact_links`` in the same transaction as DurableCommit.

Stub phase: inventory helpers only. INSERT / promote / GC land in the durable
workspace commit (Task 7).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

INVENTORY_CHECKLIST_IDS: tuple[str, ...] = (
    "reuse_envelope_brief_critique_plan_analysis",
    "forbidden_playable_field_set",
    "agents_must_not_write_sqlite_or_import_workspace",
    "no_composition_v4",
    "session_only_until_apply_default",
)

_WORKSPACE_IMPLEMENTED = False


def inventory_checklist() -> tuple[str, ...]:
    logger.debug(
        "Agent artifact workspace inventory checklist",
        extra={"checklist_ids": list(INVENTORY_CHECKLIST_IDS)},
    )
    return INVENTORY_CHECKLIST_IDS


def workspace_writes_enabled() -> bool:
    return _WORKSPACE_IMPLEMENTED
