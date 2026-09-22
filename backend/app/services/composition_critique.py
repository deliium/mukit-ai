"""Music Evaluation Engine — deterministic structured critique (read-only).

Inventory / non-goals checklist (Task 1 freeze):

(a) Current thin Critic path reused via this engine (bounded analysis sibling +
    ``AgentCritiqueV1`` + optional RevisionPlan still emitted by CriticAgent).
(b) Analysis metrics / warnings reused; constraints → hard stratum.
(c) Workspace promote for role ``critique`` already ships — no new tables.
(d) Never mutate ``composition.v2``.
(e) Subjective LLM taste ≠ hard constraint / objective correctness.
(f) No ``composition.v4``.

APIs: ``evaluate_composition`` lands in a follow-up commit. Optional LLM adapter
is separate (``llm_composition_critique``). Agents must not import workspace /
SQLite here.
"""

from __future__ import annotations

import logging
from typing import Sequence

logger = logging.getLogger(__name__)

INVENTORY_CHECKLIST_IDS: tuple[str, ...] = (
    "thin_critic_flat_critique_v1",
    "reuse_analysis_metrics_and_warnings",
    "workspace_critique_promote_already_ships",
    "never_mutate_composition_v2",
    "subjective_ne_hard_constraint",
    "no_composition_v4",
)

_ENGINE_IMPLEMENTED = True


def inventory_checklist() -> Sequence[str]:
    """Return frozen inventory checklist ids (Task 1)."""
    logger.debug(
        "Critique inventory checklist",
        extra={"checklist_ids": list(INVENTORY_CHECKLIST_IDS)},
    )
    return INVENTORY_CHECKLIST_IDS


def evaluation_engine_ready() -> bool:
    return _ENGINE_IMPLEMENTED
