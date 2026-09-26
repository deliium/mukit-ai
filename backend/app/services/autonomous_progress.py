"""Musical progress board for one autonomous run.

Several internal stages feed one row. The row text is the musical label.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

logger = logging.getLogger(__name__)

DONE_STATUSES = frozenset({"completed", "skipped"})
ACTIVE_STATUSES = frozenset({"running", "awaiting_approval"})

MUSICAL_STEPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("composition_plan", "Composition plan", ("plan",)),
    ("harmony", "Harmony", ("harmony_plan",)),
    ("theme", "", ("motif_plan", "symbolic")),
    ("critique", "Critique", ("critique", "revision")),
    ("arrangement", "Arrangement", ("arrangement",)),
    ("performance", "Final performance", ("expression", "render")),
)

CHECKPOINT_STEP: dict[str, str] = {
    "form": "composition_plan",
    "harmony": "harmony",
    "motif": "theme",
    "critique": "critique",
    "arrangement": "arrangement",
    "render": "performance",
}


def musical_progress(
    stages: Sequence[Mapping[str, Any]],
    motif_label: str,
    *,
    run_status: str | None = None,
    checkpoint_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return six board rows in stage-graph order."""
    by_id = {str(stage.get("stage_id")): str(stage.get("status") or "pending") for stage in stages}
    rows: list[dict[str, Any]] = []
    for step_id, label, stage_ids in MUSICAL_STEPS:
        statuses = [by_id.get(stage_id, "pending") for stage_id in stage_ids]
        rows.append(
            {
                "step_id": step_id,
                "label": motif_label if step_id == "theme" else label,
                "status": _base_status(statuses),
                "stage_ids": list(stage_ids),
            }
        )
    if run_status == "awaiting_approval" and checkpoint_id in CHECKPOINT_STEP:
        target = CHECKPOINT_STEP[checkpoint_id]
        for row in rows:
            if row["step_id"] == target and row["status"] != "failed":
                row["status"] = "current"
    if run_status in {"paused", "awaiting_approval"} and not any(
        row["status"] == "current" for row in rows
    ):
        for row in rows:
            if row["status"] not in {"done", "failed"}:
                row["status"] = "current"
                break
    logger.debug(
        "Musical progress computed",
        extra={
            "run_status": run_status,
            "checkpoint_id": checkpoint_id,
            "current_step": next((row["step_id"] for row in rows if row["status"] == "current"), None),
        },
    )
    return rows


def _base_status(statuses: list[str]) -> str:
    if any(status == "failed" for status in statuses):
        return "failed"
    if statuses and all(status in DONE_STATUSES for status in statuses):
        return "done"
    if any(status in ACTIVE_STATUSES for status in statuses):
        return "current"
    return "pending"
