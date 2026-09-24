"""Active-head stem selection for mix analysis (pure; no I/O).

Per stem_role, the latest complete stem that is not referenced as
``supersedes_stem_id`` by a later complete sibling.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Sequence

logger = logging.getLogger(__name__)


def select_active_head_stems(
    members: Sequence[Mapping[str, Any]],
    *,
    stem_ids: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Return active-head complete stems, or explicit ``stem_ids`` filter.

    Explicit filter still requires each stem to be ``complete``.
    """
    rows = [dict(m) for m in members]
    if stem_ids is not None:
        wanted = {str(s).strip() for s in stem_ids if str(s).strip()}
        selected = [
            r
            for r in rows
            if str(r.get("id") or "") in wanted and str(r.get("status") or "") == "complete"
        ]
        logger.debug(
            "Mix analysis stem filter applied",
            extra={
                "requested": len(wanted),
                "selected": len(selected),
                "mode": "explicit",
            },
        )
        return selected

    complete = [r for r in rows if str(r.get("status") or "") == "complete"]
    # Order by created_at ascending so "later" means later in list
    complete.sort(key=lambda r: str(r.get("created_at") or ""))

    superseded: set[str] = set()
    for row in complete:
        parent = row.get("supersedes_stem_id")
        if parent:
            superseded.add(str(parent))

    # Keep latest complete per role that is not superseded by a later complete sibling
    by_role: dict[str, dict[str, Any]] = {}
    for row in complete:
        stem_id = str(row.get("id") or "")
        if stem_id in superseded:
            continue
        role = str(row.get("stem_role") or "other")
        # Later created_at wins for same role
        prev = by_role.get(role)
        if prev is None or str(row.get("created_at") or "") >= str(prev.get("created_at") or ""):
            by_role[role] = row

    selected = list(by_role.values())
    logger.debug(
        "Mix analysis active heads selected",
        extra={
            "complete_count": len(complete),
            "superseded_count": len(superseded),
            "active_head_count": len(selected),
            "mode": "active_head",
        },
    )
    return selected
