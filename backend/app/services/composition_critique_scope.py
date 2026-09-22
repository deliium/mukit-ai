"""Resolve critique evaluation scopes to bar/tick windows without mutating V2."""

from __future__ import annotations

import logging
from typing import Any

from app.composition_schemas import CompositionV2, bar_duration_ticks
from app.critique_schemas import (
    CritiqueError,
    CritiqueScope,
    CritiqueScopeBars,
    CritiqueScopeComposition,
    CritiqueScopeDigest,
    CritiqueScopeSection,
    CritiqueScopeTrack,
    CRITIQUE_INVALID_SCOPE,
)

logger = logging.getLogger(__name__)


def _ticks_per_bar(composition: CompositionV2) -> int:
    return max(1, bar_duration_ticks(composition.time_signature, composition.ticks_per_quarter))


def _bar_count(composition: CompositionV2) -> int:
    return max(1, int(composition.bar_count))


class ResolvedCritiqueScope:
    """Immutable resolved window for evaluation clipping."""

    __slots__ = (
        "kind",
        "start_bar",
        "end_bar",
        "start_tick",
        "end_tick",
        "track_id",
        "section_index",
        "section_id",
        "digest",
    )

    def __init__(
        self,
        *,
        kind: str,
        start_bar: int,
        end_bar: int,
        start_tick: int,
        end_tick: int,
        track_id: str | None = None,
        section_index: int | None = None,
        section_id: str | None = None,
    ) -> None:
        self.kind = kind
        self.start_bar = start_bar
        self.end_bar = end_bar
        self.start_tick = start_tick
        self.end_tick = end_tick
        self.track_id = track_id
        self.section_index = section_index
        self.section_id = section_id
        digest_kwargs: dict[str, Any] = {"kind": kind}
        if kind == "section":
            digest_kwargs["section_index"] = section_index
            digest_kwargs["section_id"] = section_id
        elif kind == "track":
            digest_kwargs["track_id"] = track_id
        elif kind == "bars":
            digest_kwargs["start_bar"] = start_bar
            digest_kwargs["end_bar"] = end_bar
        self.digest = CritiqueScopeDigest.model_validate(digest_kwargs)


def resolve_critique_scope(
    composition: CompositionV2,
    scope: CritiqueScope | dict[str, Any] | None = None,
) -> ResolvedCritiqueScope:
    """Validate and resolve scope to inclusive bars + tick window.

    Does not mutate ``composition``.
    """
    if scope is None:
        scope_model: CritiqueScope = CritiqueScopeComposition()
    elif isinstance(scope, dict):
        kind = scope.get("kind") or "composition"
        if kind == "section":
            scope_model = CritiqueScopeSection.model_validate(scope)
        elif kind == "track":
            scope_model = CritiqueScopeTrack.model_validate(scope)
        elif kind == "bars":
            scope_model = CritiqueScopeBars.model_validate(scope)
        else:
            scope_model = CritiqueScopeComposition.model_validate(scope)
    else:
        scope_model = scope

    bar_count = _bar_count(composition)
    tpb = _ticks_per_bar(composition)
    logger.info(
        "Critique scope resolve",
        extra={"scope_kind": getattr(scope_model, "kind", "composition"), "bar_count": bar_count},
    )

    if isinstance(scope_model, CritiqueScopeComposition):
        resolved = ResolvedCritiqueScope(
            kind="composition",
            start_bar=1,
            end_bar=bar_count,
            start_tick=0,
            end_tick=composition.duration_ticks,
        )
    elif isinstance(scope_model, CritiqueScopeBars):
        if scope_model.start_bar > bar_count or scope_model.end_bar > bar_count:
            raise CritiqueError(
                "bars scope exceeds composition bar_count",
                code=CRITIQUE_INVALID_SCOPE,
                details={"bar_count": bar_count},
            )
        start_tick = (scope_model.start_bar - 1) * tpb
        end_tick = scope_model.end_bar * tpb
        resolved = ResolvedCritiqueScope(
            kind="bars",
            start_bar=scope_model.start_bar,
            end_bar=scope_model.end_bar,
            start_tick=start_tick,
            end_tick=min(end_tick, composition.duration_ticks),
        )
    elif isinstance(scope_model, CritiqueScopeSection):
        sections = list(composition.sections or [])
        if scope_model.section_index >= len(sections):
            raise CritiqueError(
                "section_index out of range",
                code=CRITIQUE_INVALID_SCOPE,
                details={"section_count": len(sections)},
            )
        section = sections[scope_model.section_index]
        if scope_model.section_id and getattr(section, "id", None) != scope_model.section_id:
            raise CritiqueError(
                "section_id does not match section_index",
                code=CRITIQUE_INVALID_SCOPE,
            )
        start_bar = int(section.start_bar)
        end_bar = start_bar + int(section.bar_count) - 1
        end_bar = min(end_bar, bar_count)
        resolved = ResolvedCritiqueScope(
            kind="section",
            start_bar=start_bar,
            end_bar=max(start_bar, end_bar),
            start_tick=int(section.start_tick),
            end_tick=int(section.start_tick) + int(section.duration_ticks),
            section_index=scope_model.section_index,
            section_id=getattr(section, "id", None),
        )
    elif isinstance(scope_model, CritiqueScopeTrack):
        track_ids = {t.id for t in (composition.tracks or [])}
        if scope_model.track_id not in track_ids:
            raise CritiqueError(
                "track_id not found in composition",
                code=CRITIQUE_INVALID_SCOPE,
            )
        resolved = ResolvedCritiqueScope(
            kind="track",
            start_bar=1,
            end_bar=bar_count,
            start_tick=0,
            end_tick=composition.duration_ticks,
            track_id=scope_model.track_id,
        )
    else:
        raise CritiqueError("unsupported critique scope", code=CRITIQUE_INVALID_SCOPE)

    logger.debug(
        "Critique scope resolved",
        extra={
            "kind": resolved.kind,
            "start_bar": resolved.start_bar,
            "end_bar": resolved.end_bar,
            "start_tick": resolved.start_tick,
            "end_tick": resolved.end_tick,
            "track_id": resolved.track_id,
        },
    )
    return resolved


def event_in_resolved_scope(
    *,
    start_tick: int,
    duration_ticks: int,
    track_id: str | None,
    resolved: ResolvedCritiqueScope,
) -> bool:
    """True when a note overlaps the resolved window (and track filter)."""
    if resolved.track_id and track_id and track_id != resolved.track_id:
        return False
    end_tick = start_tick + max(0, duration_ticks)
    return end_tick > resolved.start_tick and start_tick < resolved.end_tick
