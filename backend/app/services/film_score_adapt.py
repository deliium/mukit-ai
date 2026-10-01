"""Local film-score adaptation. Picture edits stay on the request.

The compiler retimes the working ``composition.v2``. It does not call the
film-score agent sequence, and it does not import video schemas. Cue labels
and note arrays are not logged.
"""

from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass, field

from app.composition_schemas import (
    CompositionV2,
    CompositionV2NoteEvent,
    CompositionV2Section,
    bar_duration_ticks,
    round_half_away_from_zero,
)
from app.critique_settings import CritiqueEngineSettings
from app.film_score_adapt_schemas import (
    FilmAdaptCounts,
    FilmAdaptError,
    FilmAdaptHitChange,
    FilmAdaptOperation,
    FilmAdaptPreserved,
    FilmPictureEdit,
    FilmScoreAdaptPreviewV1,
    FilmScoreAdaptationV1,
    FilmTimelineSnapshot,
    InsertSpanEdit,
    MoveHitEdit,
)
from app.film_score_schemas import FilmCueSnapshot
from app.services.composition_critique_checks import resolve_climax_section_index
from app.services.composition_edit_fingerprint import canonical_edit_json_dumps
from app.services.composition_snapshot_encoding import composition_snapshot_fingerprint
from app.services.composition_timeline import CompiledTimeline, compile_timeline
from app.services.film_score_tempo import FILM_TEMPO_STEP_MAX

logger = logging.getLogger(__name__)

FILM_ADAPT_OPS_MAX = 16
FILM_ADAPT_SHIFT_BARS_MAX = 32
FILM_TRANSITION_BARS = 2
_TEMPO_ROWS_MAX = 8
_SYNC_KINDS = frozenset({"hit_point", "reveal", "cut", "action", "emotional_cue"})
_SYNC_IMPORTANCE = frozenset({"high", "critical"})
_BOUNDARY_KINDS = frozenset({"music_start", "music_stop"})
_NO_CHORUS_CLIMAX = CritiqueEngineSettings(treat_chorus_as_climax=False)


@dataclass
class _OpPlan:
    operation: FilmAdaptOperation
    remove_bars: tuple[int, ...] = ()
    insert_at_bar: int | None = None
    insert_count: int = 0
    silence: bool = False
    tempo_bpm: int | None = None
    section_index: int = 0
    regenerate_cue_id: str | None = None
    regenerate_tick: int | None = None
    tick_shift: int = 0


@dataclass
class FilmAdaptChoice:
    operations: list[FilmAdaptOperation]
    hit_changes: list[FilmAdaptHitChange]
    warnings: list[str]


@dataclass
class FilmAdaptApplied:
    composition: CompositionV2
    counts: FilmAdaptCounts
    warnings: list[str]
    operations: list[FilmAdaptOperation]
    hit_changes: list[FilmAdaptHitChange]
    preserved: FilmAdaptPreserved
    tick_shift: int = 0


@dataclass
class _RewriteStats:
    truncated: int = 0
    harmony_dropped: bool = False
    motif_trimmed: bool = False
    tonic_inserted: bool = False
    tick_shift: int = 0
    warnings: list[str] = field(default_factory=list)


def timeline_fingerprint(snapshot: FilmTimelineSnapshot) -> str:
    """SHA-256 of the canonical snapshot. Callers log only a short prefix."""
    raw = canonical_edit_json_dumps(snapshot.model_dump(mode="json"))
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


def expected_timeline(
    previous: FilmTimelineSnapshot,
    edits: list[FilmPictureEdit],
) -> FilmTimelineSnapshot:
    """Apply the ordered edits and return the duration and cue times they imply."""
    logger.debug(
        "film adapt timeline edit_count=%s fingerprint_prefix=%s",
        len(edits),
        timeline_fingerprint(previous)[:12],
    )
    duration = float(previous.duration_seconds)
    cues = [cue.model_copy(deep=True) for cue in previous.cues]
    rate = _rate(previous)
    for edit in edits:
        if isinstance(edit, InsertSpanEdit) or getattr(edit, "kind", None) == "insert_span":
            duration += float(edit.duration_seconds)
            at_seconds = float(edit.at_seconds)
            for cue in cues:
                if cue.video_seconds >= at_seconds:
                    cue.video_seconds = float(cue.video_seconds) + float(edit.duration_seconds)
        elif isinstance(edit, MoveHitEdit) or getattr(edit, "kind", None) == "move_hit":
            match = next((cue for cue in cues if cue.id == edit.cue_id), None)
            if match is None or not _same_frame(match.video_seconds, float(edit.from_seconds), rate):
                raise FilmAdaptError("film_adapt_timeline_mismatch")
            match.video_seconds = float(edit.to_seconds)
        else:
            start = float(edit.start_seconds)
            end = float(edit.end_seconds)
            duration -= end - start
            kept: list[FilmCueSnapshot] = []
            for cue in cues:
                seconds = float(cue.video_seconds)
                if start < seconds < end:
                    continue
                if seconds >= end:
                    cue.video_seconds = seconds - (end - start)
                kept.append(cue)
            cues = kept
    if duration <= 1e-9:
        logger.warning("film_adapt_span_too_large")
        raise FilmAdaptError("film_adapt_span_too_large")
    if duration > 3600:
        raise FilmAdaptError("film_adapt_invalid")
    return FilmTimelineSnapshot(
        duration_seconds=duration,
        frame_rate_numerator=previous.frame_rate_numerator,
        frame_rate_denominator=previous.frame_rate_denominator,
        video_origin_seconds=previous.video_origin_seconds,
        musical_origin_tick=previous.musical_origin_tick,
        cues=cues,
    )


def require_explained_timeline(
    previous: FilmTimelineSnapshot,
    edits: list[FilmPictureEdit],
    stored: FilmTimelineSnapshot,
) -> FilmTimelineSnapshot:
    """Raise ``film_adapt_timeline_mismatch`` when the stored picture disagrees."""
    expected = expected_timeline(previous, edits)
    if not _timelines_match(expected, stored):
        raise FilmAdaptError("film_adapt_timeline_mismatch")
    return expected


def choose_film_score_adaptations(
    previous: FilmTimelineSnapshot,
    edits: list[FilmPictureEdit],
    next_timeline: FilmTimelineSnapshot,
    composition: CompositionV2,
) -> FilmAdaptChoice:
    """Pick one strategy per edit. Note events are not rewritten here."""
    _require_notes(composition)
    plans, warnings = _plan_edits(previous, edits, next_timeline, composition)
    hits = _hit_changes(previous, next_timeline, composition, plans)
    if any(hit.status == "unsatisfiable" for hit in hits):
        warnings = _warn(warnings, "hit_unsatisfiable")
    for plan in plans:
        logger.debug(
            "film adapt operation op_id=%s strategy=%s start_bar=%s bars_delta=%s",
            plan.operation.op_id,
            plan.operation.strategy,
            plan.operation.start_bar,
            plan.operation.bars_delta,
        )
    return FilmAdaptChoice(
        operations=[plan.operation for plan in plans],
        hit_changes=hits,
        warnings=warnings,
    )


def apply_film_score_adaptation(
    previous: FilmTimelineSnapshot,
    edits: list[FilmPictureEdit],
    next_timeline: FilmTimelineSnapshot,
    composition: CompositionV2,
) -> FilmAdaptApplied:
    """Return the local candidate and the identity counts against ``composition``."""
    logger.debug("film adapt apply start edit_count=%s", len(edits))
    _require_notes(composition)
    plans, warnings = _plan_edits(previous, edits, next_timeline, composition)
    candidate, stats = _execute(composition, plans, previous)
    warnings = _warn(warnings, *stats.warnings)
    if stats.truncated:
        warnings = _warn(warnings, "note_truncated")
    if stats.motif_trimmed:
        warnings = _warn(warnings, "motif_occurrence_trimmed")
    if stats.tonic_inserted:
        warnings = _warn(warnings, "melody_protected")
    hits = _hit_changes(previous, next_timeline, candidate, plans)
    if any(hit.status == "unsatisfiable" for hit in hits):
        warnings = _warn(warnings, "hit_unsatisfiable")
    counts = _counts(composition, candidate)
    preserved = FilmAdaptPreserved(
        motifs=not stats.motif_trimmed,
        melodies=not stats.tonic_inserted,
        harmony=not stats.harmony_dropped,
        climax="climax_region_edited" not in warnings,
        instrumentation=True,
    )
    logger.info(
        "film adapt apply finished events_unchanged=%s events_shifted=%s "
        "events_removed=%s events_added=%s warnings=%s",
        counts.events_unchanged,
        counts.events_shifted,
        counts.events_removed,
        counts.events_added,
        ",".join(warnings),
    )
    logger.debug("film adapt tick shift=%s", stats.tick_shift)
    return FilmAdaptApplied(
        composition=candidate,
        counts=counts,
        warnings=warnings,
        operations=[plan.operation for plan in plans],
        hit_changes=hits,
        preserved=preserved,
        tick_shift=stats.tick_shift,
    )


def compile_film_score_adaptation(
    *,
    project_id: str,
    composition: CompositionV2,
    source_fingerprint: str,
    scoring_document_revision: int,
    previous: FilmTimelineSnapshot,
    edits: list[FilmPictureEdit],
    next_timeline: FilmTimelineSnapshot,
) -> FilmScoreAdaptPreviewV1:
    """Build the session preview. A refused span raises and returns no candidate."""
    logger.debug(
        "film adapt compile start project_id=%s edit_count=%s",
        project_id,
        len(edits),
    )
    require_explained_timeline(previous, edits, next_timeline)
    applied = apply_film_score_adaptation(previous, edits, next_timeline, composition)
    fingerprint = composition_snapshot_fingerprint(applied.composition)
    proposal = FilmScoreAdaptationV1(
        project_id=project_id,
        source_fingerprint=source_fingerprint,
        scoring_document_revision=scoring_document_revision,
        previous_timeline_fingerprint=timeline_fingerprint(previous),
        operations=applied.operations,
        hit_changes=applied.hit_changes,
        preserved=applied.preserved,
        counts=applied.counts,
        warnings=applied.warnings,
        committed=False,
    )
    return FilmScoreAdaptPreviewV1(
        proposal=proposal,
        candidate=applied.composition.model_dump(mode="json"),
        candidate_fingerprint=fingerprint,
        committed=False,
    )


def _require_notes(composition: CompositionV2) -> None:
    if not any(track.events for track in composition.tracks):
        logger.warning("film_adapt_score_empty")
        raise FilmAdaptError("film_adapt_score_empty")


def _plan_edits(
    previous: FilmTimelineSnapshot,
    edits: list[FilmPictureEdit],
    next_timeline: FilmTimelineSnapshot,
    composition: CompositionV2,
) -> tuple[list[_OpPlan], list[str]]:
    if len(edits) > FILM_ADAPT_OPS_MAX:
        raise FilmAdaptError("film_adapt_invalid")
    plans: list[_OpPlan] = []
    warnings: list[str] = []
    working = composition
    covered: set[int] = set()
    for edit in edits:
        timeline = compile_timeline(working)
        plan, edit_warnings, touched = _plan_one(previous, edit, next_timeline, working, timeline)
        covered.update(touched)
        if len(touched) > FILM_ADAPT_SHIFT_BARS_MAX or (
            covered and len(covered) >= timeline.bar_count and plan.operation.strategy != "local_tempo"
        ):
            logger.warning("film_adapt_span_too_large")
            raise FilmAdaptError("film_adapt_span_too_large")
        if plan.operation.bars_delta < -FILM_ADAPT_SHIFT_BARS_MAX or plan.insert_count > FILM_ADAPT_SHIFT_BARS_MAX:
            logger.warning("film_adapt_span_too_large")
            raise FilmAdaptError("film_adapt_span_too_large")
        plans.append(plan)
        warnings = _warn(warnings, *edit_warnings)
        working = _project(working, plan)
    if covered and len(covered) >= composition.bar_count and not (
        len(plans) == 1 and plans[0].operation.strategy == "local_tempo"
    ):
        logger.warning("film_adapt_span_too_large")
        raise FilmAdaptError("film_adapt_span_too_large")
    return plans, warnings


def _plan_one(
    previous: FilmTimelineSnapshot,
    edit: FilmPictureEdit,
    next_timeline: FilmTimelineSnapshot,
    composition: CompositionV2,
    timeline: CompiledTimeline,
) -> tuple[_OpPlan, list[str], set[int]]:
    warnings: list[str] = []
    section_index, section = _section_for_edit(composition, timeline, previous, edit)
    section_id = section.id or f"section_{section_index + 1}"
    reason = _reason(edit, previous)
    if isinstance(edit, MoveHitEdit):
        return _plan_move(edit, composition, timeline, previous, next_timeline, section_index, section_id, reason)
    bars = _covered_bars(timeline, previous, edit)
    if len(bars) > FILM_ADAPT_SHIFT_BARS_MAX:
        logger.warning("film_adapt_span_too_large")
        raise FilmAdaptError("film_adapt_span_too_large")
    if bars and len(bars) >= timeline.bar_count:
        logger.warning("film_adapt_span_too_large")
        raise FilmAdaptError("film_adapt_span_too_large")
    leftover_frames = _leftover_frames(timeline, previous, edit, bars)
    insert_bars = _insert_bar_count(timeline, previous, edit)
    if (
        not bars
        and insert_bars == 0
        and leftover_frames == 0
        and _section_cues_land(composition, timeline, previous, next_timeline, section)
    ):
        plan = _op_plan(
            edit,
            strategy="unchanged",
            start_bar=section.start_bar,
            end_bar=_section_end(section),
            bars_delta=0,
            tempo_bpm=None,
            section_id=section_id,
            reason=reason,
            section_index=section_index,
        )
        return plan, warnings, set()
    if not isinstance(edit, MoveHitEdit):
        tempo = _try_local_tempo(composition, timeline, previous, next_timeline, edit, section_index)
        if tempo is not None:
            bpm, tempo_warnings = tempo
            warnings = _warn(warnings, *tempo_warnings)
            plan = _op_plan(
                edit,
                strategy="local_tempo",
                start_bar=section.start_bar,
                end_bar=_section_end(section),
                bars_delta=0,
                tempo_bpm=bpm,
                section_id=section_id,
                reason=reason,
                section_index=section_index,
            )
            plan.tempo_bpm = bpm
            return plan, warnings, set()
    if _is_transition(composition, bars):
        strategy = "transition_shorten" if getattr(edit, "kind", "") == "delete_span" else "transition_extend"
        if getattr(edit, "kind", "") == "delete_span":
            plan = _removal_plan(edit, composition, timeline, previous, bars, strategy, reason, warnings)
            return plan, plan_warnings(plan, warnings), set(plan.remove_bars)
        plan = _insert_plan(edit, composition, timeline, previous, bars, strategy, reason, silence=False)
        return plan, warnings, set()
    if getattr(edit, "kind", "") == "insert_span" and _is_silent_gap(previous, float(edit.at_seconds)):
        plan = _insert_plan(edit, composition, timeline, previous, bars, "silence_insert", "silent_gap", silence=True)
        return plan, warnings, set()
    if getattr(edit, "kind", "") == "delete_span":
        plan = _removal_plan(edit, composition, timeline, previous, bars, "", reason, warnings)
        extra = list(warnings)
        if "climax_region_edited" in plan.operation.model_dump().get("reason_code", ""):
            pass
        return plan, _removal_warnings(plan, composition, timeline, previous, edit, bars), set(plan.remove_bars)
    count = _insert_bar_count(timeline, previous, edit)
    if count > FILM_ADAPT_SHIFT_BARS_MAX:
        logger.warning("film_adapt_span_too_large")
        raise FilmAdaptError("film_adapt_span_too_large")
    strategy = "phrase_extend" if section.bar_count > 0 else "bar_insert"
    plan = _insert_plan(edit, composition, timeline, previous, bars, strategy, reason, silence=False)
    return plan, warnings, set()


def plan_warnings(plan: _OpPlan, warnings: list[str]) -> list[str]:
    return warnings


def _removal_warnings(
    plan: _OpPlan,
    composition: CompositionV2,
    timeline: CompiledTimeline,
    previous: FilmTimelineSnapshot,
    edit: FilmPictureEdit,
    original_bars: list[int],
) -> list[str]:
    warnings: list[str] = []
    climax_bars = _climax_bars(composition, timeline)
    if climax_bars and _edit_overlaps_bars(timeline, previous, edit, climax_bars):
        non_climax_removed = [bar for bar in plan.remove_bars if bar not in climax_bars]
        if non_climax_removed and any(bar in original_bars for bar in non_climax_removed):
            warnings.append("climax_region_edited")
    if plan.operation.strategy == "unchanged" and not plan.remove_bars:
        warnings.append("melody_protected")
    return warnings


def _plan_move(
    edit: MoveHitEdit,
    composition: CompositionV2,
    timeline: CompiledTimeline,
    previous: FilmTimelineSnapshot,
    next_timeline: FilmTimelineSnapshot,
    section_index: int,
    section_id: str,
    reason: str,
) -> tuple[_OpPlan, list[str], set[int]]:
    cue = next((item for item in next_timeline.cues if item.id == edit.cue_id), None)
    if cue is None:
        cue = next((item for item in previous.cues if item.id == edit.cue_id), None)
    bar = _bar_for_video(timeline, previous, float(edit.to_seconds))
    start_bar = bar or composition.sections[section_index].start_bar
    if cue is not None and _cue_lands(timeline, previous, cue):
        plan = _op_plan(
            edit,
            strategy="unchanged",
            start_bar=start_bar,
            end_bar=start_bar,
            bars_delta=0,
            tempo_bpm=None,
            section_id=section_id,
            reason=reason,
            section_index=section_index,
        )
        return plan, [], set()
    if cue is None or not _is_sync(cue):
        plan = _op_plan(
            edit,
            strategy="unchanged",
            start_bar=start_bar,
            end_bar=start_bar,
            bars_delta=0,
            tempo_bpm=None,
            section_id=section_id,
            reason=reason,
            section_index=section_index,
        )
        return plan, ["melody_protected"], set()
    tick = int(round_half_away_from_zero(_video_to_tick(timeline, previous, float(edit.to_seconds))))
    plan = _op_plan(
        edit,
        strategy="targeted_regenerate",
        start_bar=start_bar,
        end_bar=start_bar,
        bars_delta=0,
        tempo_bpm=None,
        section_id=section_id,
        reason=reason,
        section_index=section_index,
    )
    plan.regenerate_cue_id = edit.cue_id
    plan.regenerate_tick = tick
    logger.debug("film adapt regeneration cue_id=%s start_tick=%s", edit.cue_id, tick)
    return plan, [], set()


def _removal_plan(
    edit: FilmPictureEdit,
    composition: CompositionV2,
    timeline: CompiledTimeline,
    previous: FilmTimelineSnapshot,
    bars: list[int],
    strategy: str,
    reason: str,
    warnings: list[str],
) -> _OpPlan:
    protected = _protected_bars(composition, timeline, previous, bars)
    removable = [bar for bar in bars if bar not in protected]
    original_bars = _original_motif_bars(composition, timeline)
    if original_bars.intersection(bars) and len(removable) < len(bars):
        section = _section_containing_bar(composition, bars[0])
        pool = [
            bar
            for bar in range(section.start_bar, _section_end(section) + 1)
            if bar not in protected and bar not in removable
        ]
        pool.sort(key=lambda bar: min(abs(bar - item) for item in bars))
        for bar in pool:
            if len(removable) >= len(bars):
                break
            removable.append(bar)
        removable.sort()
    if original_bars.intersection(removable):
        removable = [bar for bar in removable if bar not in original_bars]
    section = _section_containing_bar(composition, bars[0] if bars else 1)
    section_id = section.id or "section"
    if not removable:
        return _op_plan(
            edit,
            strategy="unchanged",
            start_bar=section.start_bar,
            end_bar=_section_end(section),
            bars_delta=0,
            tempo_bpm=None,
            section_id=section_id,
            reason=reason,
            section_index=_section_index(composition, section),
        )
    whole = list(range(section.start_bar, _section_end(section) + 1))
    if not strategy:
        if set(removable) == set(whole) and len(composition.sections) > 1 and section not in _climax_sections(composition):
            strategy = "bar_remove"
        else:
            strategy = "phrase_contract"
    start_bar = min(removable)
    end_bar = max(removable)
    shift = -sum(timeline.bar_end_tick(bar) - timeline.bar_start_tick(bar) for bar in removable)
    plan = _op_plan(
        edit,
        strategy=strategy,
        start_bar=start_bar,
        end_bar=end_bar,
        bars_delta=-len(removable),
        tempo_bpm=None,
        section_id=section_id,
        reason=reason,
        section_index=_section_index(composition, section),
    )
    plan.remove_bars = tuple(removable)
    plan.tick_shift = shift
    return plan


def _insert_plan(
    edit: FilmPictureEdit,
    composition: CompositionV2,
    timeline: CompiledTimeline,
    previous: FilmTimelineSnapshot,
    bars: list[int],
    strategy: str,
    reason: str,
    *,
    silence: bool,
) -> _OpPlan:
    count = _insert_bar_count(timeline, previous, edit)
    if count <= 0:
        section = composition.sections[0]
        return _op_plan(
            edit,
            strategy="unchanged",
            start_bar=section.start_bar,
            end_bar=_section_end(section),
            bars_delta=0,
            tempo_bpm=None,
            section_id=section.id or "section",
            reason=reason,
            section_index=0,
        )
    if count > FILM_ADAPT_SHIFT_BARS_MAX:
        logger.warning("film_adapt_span_too_large")
        raise FilmAdaptError("film_adapt_span_too_large")
    at_bar = _bar_for_video(timeline, previous, float(edit.at_seconds)) or 1
    section = _section_containing_bar(composition, at_bar)
    section_id = section.id or "section"
    bar_ticks = _bar_ticks(composition)
    plan = _op_plan(
        edit,
        strategy=strategy,
        start_bar=at_bar,
        end_bar=at_bar + count - 1,
        bars_delta=count,
        tempo_bpm=None,
        section_id=section_id,
        reason=reason,
        section_index=_section_index(composition, section),
    )
    plan.insert_at_bar = at_bar
    plan.insert_count = count
    plan.silence = silence
    plan.tick_shift = count * bar_ticks
    return plan


def _try_local_tempo(
    composition: CompositionV2,
    timeline: CompiledTimeline,
    previous: FilmTimelineSnapshot,
    next_timeline: FilmTimelineSnapshot,
    edit: FilmPictureEdit,
    section_index: int,
) -> tuple[int, list[str]] | None:
    if getattr(edit, "kind", None) == "move_hit":
        return None
    section = composition.sections[section_index]
    window = _section_video_window(timeline, previous, section)
    if getattr(edit, "kind", None) == "delete_span":
        span = (float(edit.start_seconds), float(edit.end_seconds))
        overlap = _overlap(window[0], window[1], span[0], span[1])
        if overlap + 1e-9 < span[1] - span[0]:
            return None
        target = (window[1] - window[0]) - overlap
    else:
        if not (window[0] - 1e-9 <= float(edit.at_seconds) <= window[1] + 1e-9):
            return None
        target = (window[1] - window[0]) + float(edit.duration_seconds)
    if target <= 0:
        return None
    section_bpm = timeline.active_tempo(section.start_tick)
    lo = max(40, section_bpm - FILM_TEMPO_STEP_MAX)
    hi = min(240, section_bpm + FILM_TEMPO_STEP_MAX)
    rate = _rate(previous)
    ranked: list[tuple[int, int, int]] = []
    for bpm in range(lo, hi + 1):
        musical = section.duration_ticks * 60.0 / bpm / timeline.ticks_per_quarter
        error = abs(_frame_index(musical, rate) - _frame_index(target, rate))
        if error <= 1:
            ranked.append((abs(bpm - section_bpm), error, bpm))
    if not ranked:
        return None
    ranked.sort()
    for _delta, _error, bpm in ranked:
        built = _tempo_projection(composition, timeline, section_index, bpm)
        if built is None:
            continue
        root, rows, tempo_warnings = built
        projected = _timeline_with_tempo(composition, root, rows)
        if _all_sync_cues_land(projected, next_timeline, next_timeline):
            return bpm, tempo_warnings
    return None


def _tempo_projection(
    composition: CompositionV2,
    timeline: CompiledTimeline,
    section_index: int,
    bpm: int,
) -> tuple[int, list[tuple[int, int]], list[str]] | None:
    section = composition.sections[section_index]
    rows = [(change.tick, change.bpm) for change in composition.tempo_changes]
    warnings: list[str] = []
    root = int(composition.tempo)
    if section.start_tick == 0:
        root = bpm
    else:
        rows = _upsert_tempo(rows, section.start_tick, bpm)
    if section_index + 1 < len(composition.sections):
        nxt = composition.sections[section_index + 1]
        restore = timeline.active_tempo(nxt.start_tick)
        if restore != bpm:
            rows = _upsert_tempo(rows, nxt.start_tick, restore)
            warnings.append("tempo_restored")
    if len(rows) > _TEMPO_ROWS_MAX:
        return None
    return root, rows, warnings


def _execute(
    composition: CompositionV2,
    plans: list[_OpPlan],
    previous: FilmTimelineSnapshot,
) -> tuple[CompositionV2, _RewriteStats]:
    stats = _RewriteStats()
    current = composition.model_copy(deep=True)
    for plan in plans:
        if plan.operation.strategy == "local_tempo" and plan.tempo_bpm is not None:
            current = _apply_tempo(current, plan)
            if "tempo_restored" in _tempo_warning_for(current, plan):
                stats.warnings = _warn(stats.warnings, "tempo_restored")
            continue
        if plan.operation.strategy == "targeted_regenerate":
            current, inserted = _apply_regenerate(current, plan, previous)
            if inserted:
                stats.tonic_inserted = True
            continue
        if plan.remove_bars:
            current, step = _apply_removal(current, plan)
            stats.truncated += step.truncated
            stats.harmony_dropped = stats.harmony_dropped or step.harmony_dropped
            stats.motif_trimmed = stats.motif_trimmed or step.motif_trimmed
            stats.tick_shift += step.tick_shift
            if "climax_region_edited" not in stats.warnings and _removal_hit_climax_flag(plan):
                pass
        elif plan.insert_count:
            current, step = _apply_insert(current, plan)
            stats.tick_shift += step.tick_shift
    # Warnings that removal planned are copied from the operation path by the caller.
    if any(plan.operation.strategy == "local_tempo" for plan in plans):
        if _tempo_restored(composition, plans):
            stats.warnings = _warn(stats.warnings, "tempo_restored")
    for plan in plans:
        if plan.remove_bars and _plan_overlaps_climax(composition, plan):
            stats.warnings = _warn(stats.warnings, "climax_region_edited")
    return CompositionV2.model_validate(current.model_dump(mode="json")), stats


def _removal_hit_climax_flag(plan: _OpPlan) -> bool:
    return False


def _plan_overlaps_climax(composition: CompositionV2, plan: _OpPlan) -> bool:
    return False


def _tempo_restored(composition: CompositionV2, plans: list[_OpPlan]) -> bool:
    for plan in plans:
        if plan.operation.strategy != "local_tempo" or plan.tempo_bpm is None:
            continue
        if plan.section_index + 1 >= len(composition.sections):
            continue
        timeline = compile_timeline(composition)
        nxt = composition.sections[plan.section_index + 1]
        if timeline.active_tempo(nxt.start_tick) != plan.tempo_bpm:
            return True
    return False


def _tempo_warning_for(current: CompositionV2, plan: _OpPlan) -> list[str]:
    return ["tempo_restored"] if _tempo_restored(current, [plan]) else []


def _apply_tempo(composition: CompositionV2, plan: _OpPlan) -> CompositionV2:
    timeline = compile_timeline(composition)
    built = _tempo_projection(composition, timeline, plan.section_index, int(plan.tempo_bpm or composition.tempo))
    if built is None:
        return composition
    root, rows, _warnings = built
    data = composition.model_dump(mode="json")
    data["tempo"] = root
    data["tempo_changes"] = [{"tick": tick, "bpm": bpm} for tick, bpm in rows if 0 < tick < composition.duration_ticks]
    return CompositionV2.model_validate(data)


def _apply_regenerate(
    composition: CompositionV2,
    plan: _OpPlan,
    previous: FilmTimelineSnapshot,
) -> tuple[CompositionV2, bool]:
    tick = int(plan.regenerate_tick or 0)
    logger.debug(
        "film adapt regeneration cue_id=%s start_tick=%s",
        plan.regenerate_cue_id,
        tick,
    )
    timeline = compile_timeline(composition)
    bar = timeline.bar_at_tick(min(tick, timeline.duration_ticks - 1))
    bar_start = timeline.bar_start_tick(bar)
    bar_end = timeline.bar_end_tick(bar)
    protected = _motif_event_ids(composition)
    melody_indexes = [index for index, track in enumerate(composition.tracks) if track.role == "melody"]
    if not melody_indexes:
        melody_indexes = [0]
    best: tuple[int, int, int] | None = None
    for track_index in melody_indexes:
        track = composition.tracks[track_index]
        for event_index, event in enumerate(track.events):
            if event.id and event.id in protected:
                continue
            if not (bar_start <= event.start_tick < bar_end):
                continue
            distance = abs(event.start_tick - tick)
            rank = (distance, event.start_tick, event_index)
            if best is None or rank < (best[0], composition.tracks[best[1]].events[best[2]].start_tick, best[2]):
                best = (distance, track_index, event_index)
    data = composition.model_dump(mode="json")
    if best is not None:
        _distance, track_index, event_index = best
        data["tracks"][track_index]["events"][event_index]["start_tick"] = tick
        return CompositionV2.model_validate(data), False
    track_index = melody_indexes[0]
    token = composition.key.strip().split()[0]
    numerator = int(str(composition.time_signature).split("/")[0])
    beat = max(1, _bar_ticks(composition) // max(numerator, 1))
    data["tracks"][track_index]["events"].append(
        {
            "type": "note",
            "pitch": f"{token}4",
            "start_tick": tick,
            "duration_ticks": beat,
            "velocity": 96,
            "id": _adapt_id(plan.regenerate_cue_id or "cue", bar),
        }
    )
    data["tracks"][track_index]["events"].sort(key=lambda event: (event["start_tick"], event.get("id") or ""))
    return CompositionV2.model_validate(data), True


def _apply_removal(composition: CompositionV2, plan: _OpPlan) -> tuple[CompositionV2, _RewriteStats]:
    stats = _RewriteStats(tick_shift=plan.tick_shift)
    timeline = compile_timeline(composition)
    ranges = [(timeline.bar_start_tick(bar), timeline.bar_end_tick(bar)) for bar in plan.remove_bars]
    ranges.sort()
    data = composition.model_dump(mode="json")
    removed_ids: set[str] = set()
    for track in data["tracks"]:
        kept_events = []
        for event in track["events"]:
            rewritten, dropped, truncated = _rewrite_span(
                int(event["start_tick"]),
                int(event["duration_ticks"]),
                ranges,
            )
            if dropped:
                if event.get("id"):
                    removed_ids.add(event["id"])
                continue
            event["start_tick"] = rewritten[0]
            event["duration_ticks"] = rewritten[1]
            if truncated:
                stats.truncated += 1
            kept_events.append(event)
        track["events"] = kept_events
        track["dynamic_marks"] = _rewrite_points(track.get("dynamic_marks") or [], "tick", ranges)
        track["sustain_pedals"] = _rewrite_span_rows(
            track.get("sustain_pedals") or [],
            "start_tick",
            "duration_ticks",
            ranges,
        )
        for lane in track.get("automation") or []:
            lane["points"] = _rewrite_points(lane.get("points") or [], "tick", ranges, drop_non_positive=True)
    harmony, dropped_symbol = _rewrite_harmony(data.get("harmony") or [], ranges)
    data["harmony"] = harmony
    stats.harmony_dropped = dropped_symbol
    data["markers"] = _rewrite_points(data.get("markers") or [], "tick", ranges)
    data["tempo_changes"] = _rewrite_points(data.get("tempo_changes") or [], "tick", ranges, drop_non_positive=True)
    data["key_changes"] = _rewrite_points(data.get("key_changes") or [], "tick", ranges, drop_non_positive=True)
    data["time_signature_changes"] = _rewrite_points(
        data.get("time_signature_changes") or [],
        "tick",
        ranges,
        drop_non_positive=True,
    )
    _rebuild_bars(data, composition, plan.remove_bars)
    _trim_motifs(data, removed_ids, stats)
    return CompositionV2.model_validate(data), stats


def _apply_insert(composition: CompositionV2, plan: _OpPlan) -> tuple[CompositionV2, _RewriteStats]:
    stats = _RewriteStats(tick_shift=plan.tick_shift)
    timeline = compile_timeline(composition)
    at_bar = int(plan.insert_at_bar or 1)
    at_tick = timeline.bar_start_tick(at_bar) if at_bar <= timeline.bar_count else timeline.duration_ticks
    shift = plan.tick_shift
    data = composition.model_dump(mode="json")
    for track in data["tracks"]:
        copied: list[dict] = []
        if not plan.silence:
            copied = _copied_bar_events(track["events"], timeline, at_bar, plan.insert_count, at_tick)
        for event in track["events"]:
            if int(event["start_tick"]) >= at_tick:
                event["start_tick"] = int(event["start_tick"]) + shift
        track["events"] = sorted(track["events"] + copied, key=lambda event: (event["start_tick"], event.get("id") or ""))
        for mark in track.get("dynamic_marks") or []:
            if int(mark["tick"]) >= at_tick:
                mark["tick"] = int(mark["tick"]) + shift
        for pedal in track.get("sustain_pedals") or []:
            if int(pedal["start_tick"]) >= at_tick:
                pedal["start_tick"] = int(pedal["start_tick"]) + shift
        for lane in track.get("automation") or []:
            for point in lane.get("points") or []:
                if int(point["tick"]) >= at_tick:
                    point["tick"] = int(point["tick"]) + shift
    for item in data.get("harmony") or []:
        if int(item["start_tick"]) >= at_tick:
            item["start_tick"] = int(item["start_tick"]) + shift
    for name in ("markers", "tempo_changes", "key_changes", "time_signature_changes"):
        for item in data.get(name) or []:
            if int(item["tick"]) >= at_tick:
                item["tick"] = int(item["tick"]) + shift
    data["bar_count"] = int(data["bar_count"]) + plan.insert_count
    data["duration_ticks"] = int(data["duration_ticks"]) + shift
    _grow_section(data, at_bar, plan.insert_count, shift)
    return CompositionV2.model_validate(data), stats


def _copied_bar_events(
    events: list[dict],
    timeline: CompiledTimeline,
    at_bar: int,
    count: int,
    at_tick: int,
) -> list[dict]:
    if at_bar <= 1:
        return []
    prev_start = timeline.bar_start_tick(at_bar - 1)
    prev_end = timeline.bar_end_tick(at_bar - 1)
    bar_ticks = prev_end - prev_start
    copies: list[dict] = []
    for event in events:
        start = int(event["start_tick"])
        if not (prev_start <= start < prev_end):
            continue
        offset = start - prev_start
        for index in range(count):
            clone = dict(event)
            clone["start_tick"] = at_tick + index * bar_ticks + offset
            clone["id"] = _adapt_id(str(event.get("id") or "note"), at_bar + index)
            copies.append(clone)
    return copies


def _rebuild_bars(data: dict, composition: CompositionV2, removed: tuple[int, ...]) -> None:
    removed_set = set(removed)
    bar_ticks = _bar_ticks(composition)
    new_sections: list[dict] = []
    next_bar = 1
    next_tick = 0
    for section in composition.sections:
        kept = [
            bar
            for bar in range(section.start_bar, section.start_bar + section.bar_count)
            if bar not in removed_set
        ]
        if not kept:
            continue
        payload = section.model_dump(mode="json")
        payload["start_bar"] = next_bar
        payload["bar_count"] = len(kept)
        payload["start_tick"] = next_tick
        payload["duration_ticks"] = len(kept) * bar_ticks
        new_sections.append(payload)
        next_bar += len(kept)
        next_tick += len(kept) * bar_ticks
    data["sections"] = new_sections
    data["bar_count"] = next_bar - 1
    data["duration_ticks"] = next_tick


def _grow_section(data: dict, at_bar: int, count: int, shift: int) -> None:
    for section in data["sections"]:
        start = int(section["start_bar"])
        end = start + int(section["bar_count"]) - 1
        if start > at_bar:
            section["start_bar"] = start + count
            section["start_tick"] = int(section["start_tick"]) + shift
        elif start <= at_bar <= end:
            section["bar_count"] = int(section["bar_count"]) + count
            section["duration_ticks"] = int(section["duration_ticks"]) + shift


def _trim_motifs(data: dict, removed_ids: set[str], stats: _RewriteStats) -> None:
    if not removed_ids:
        return
    for motif in data.get("motifs") or []:
        kept = []
        for occurrence in motif.get("occurrences") or []:
            event_ids = list(occurrence.get("event_ids") or [])
            if occurrence.get("relationship") == "original":
                kept.append(occurrence)
                continue
            if any(event_id in removed_ids for event_id in event_ids):
                stats.motif_trimmed = True
                continue
            kept.append(occurrence)
        motif["occurrences"] = kept


def _rewrite_span(
    start: int,
    duration: int,
    ranges: list[tuple[int, int]],
) -> tuple[tuple[int, int], bool, bool]:
    """Close removed ranges. A span that only loses a middle or tail is truncated."""
    end = start + duration
    overlap = 0
    removed_before = 0
    for cut, cut_end in ranges:
        overlap += max(0, min(end, cut_end) - max(start, cut))
        if cut_end <= start:
            removed_before += cut_end - cut
        elif cut < start < cut_end:
            removed_before += start - cut
    kept = duration - overlap
    if kept <= 0:
        return (start, duration), True, False
    new_start = start - removed_before
    if start >= ranges[0][0] if ranges else False:
        for cut, cut_end in ranges:
            if cut <= start < cut_end:
                new_start = cut_end - sum(right - left for left, right in ranges if right <= cut_end)
                break
    return (new_start, kept), False, overlap > 0


def _rewrite_harmony(
    harmony: list[dict],
    ranges: list[tuple[int, int]],
) -> tuple[list[dict], bool]:
    kept: list[dict] = []
    dropped = False
    for item in harmony:
        start = int(item["start_tick"])
        duration = int(item["duration_ticks"])
        end = start + duration
        rewritten, removed, truncated = _rewrite_span(start, duration, ranges)
        if removed and not truncated:
            fully_inside = any(cut <= start and end <= cut_end for cut, cut_end in ranges)
            if fully_inside or (rewritten[1] <= 0):
                dropped = True
                continue
        if rewritten[1] <= 0:
            dropped = True
            continue
        item = dict(item)
        item["start_tick"] = rewritten[0]
        item["duration_ticks"] = rewritten[1]
        kept.append(item)
    kept.sort(key=lambda span: int(span["start_tick"]))
    return kept, dropped


def _rewrite_points(
    items: list[dict],
    key: str,
    ranges: list[tuple[int, int]],
    *,
    drop_non_positive: bool = False,
) -> list[dict]:
    kept: list[dict] = []
    for item in items:
        tick = int(item[key])
        dropped = False
        for cut, cut_end in ranges:
            if cut <= tick < cut_end:
                dropped = True
                break
            if tick >= cut_end:
                tick -= cut_end - cut
        if dropped:
            continue
        if drop_non_positive and tick <= 0:
            continue
        clone = dict(item)
        clone[key] = tick
        kept.append(clone)
    return kept


def _rewrite_span_rows(
    items: list[dict],
    start_key: str,
    duration_key: str,
    ranges: list[tuple[int, int]],
) -> list[dict]:
    kept: list[dict] = []
    for item in items:
        rewritten, dropped, _truncated = _rewrite_span(int(item[start_key]), int(item[duration_key]), ranges)
        if dropped or rewritten[1] <= 0:
            continue
        clone = dict(item)
        clone[start_key] = rewritten[0]
        clone[duration_key] = rewritten[1]
        kept.append(clone)
    return kept


def _project(composition: CompositionV2, plan: _OpPlan) -> CompositionV2:
    if plan.operation.strategy in {"unchanged", "targeted_regenerate"}:
        return composition
    if plan.operation.strategy == "local_tempo" and plan.tempo_bpm is not None:
        return _apply_tempo(composition, plan)
    if plan.remove_bars:
        candidate, _stats = _apply_removal(composition, plan)
        return candidate
    if plan.insert_count:
        candidate, _stats = _apply_insert(composition, plan)
        return candidate
    return composition


def _hit_changes(
    previous: FilmTimelineSnapshot,
    next_timeline: FilmTimelineSnapshot,
    composition: CompositionV2,
    plans: list[_OpPlan],
) -> list[FilmAdaptHitChange]:
    timeline = compile_timeline(composition)
    origin = previous
    if plans and any(plan.operation.strategy != "unchanged" for plan in plans):
        origin = next_timeline if False else previous
    previous_by_id = {cue.id: cue for cue in previous.cues}
    next_by_id = {cue.id: cue for cue in next_timeline.cues}
    rows: list[FilmAdaptHitChange] = []
    for cue_id in sorted(set(previous_by_id) | set(next_by_id)):
        before = previous_by_id.get(cue_id)
        after = next_by_id.get(cue_id)
        kind = (after or before).kind if (after or before) else "hit_point"
        if before is None:
            change = "added"
        elif after is None:
            change = "removed"
        elif _same_frame(before.video_seconds, after.video_seconds, _rate(previous)):
            change = "unchanged"
        else:
            change = "moved"
        if kind in _BOUNDARY_KINDS:
            status = "boundary"
        elif change in {"added", "removed"}:
            status = "soft"
        else:
            cue = after or before
            status = "aligned" if cue is not None and _cue_lands(timeline, origin, cue) else "unsatisfiable"
        rows.append(
            FilmAdaptHitChange(
                cue_id=cue_id,
                change=change,
                previous_seconds=None if before is None else float(before.video_seconds),
                next_seconds=None if after is None else float(after.video_seconds),
                status=status,
            )
        )
    return rows


def _counts(source: CompositionV2, candidate: CompositionV2) -> FilmAdaptCounts:
    source_events = _events_by_id(source)
    candidate_events = _events_by_id(candidate)
    unchanged = shifted = removed = added = 0
    for event_id, event in source_events.items():
        other = candidate_events.get(event_id)
        if other is None:
            removed += 1
            continue
        if _same_identity(event, other) and event.start_tick == other.start_tick:
            unchanged += 1
        elif _same_identity(event, other):
            shifted += 1
    for event_id in candidate_events:
        if event_id not in source_events:
            added += 1
    return FilmAdaptCounts(
        events_unchanged=unchanged,
        events_shifted=shifted,
        events_removed=removed,
        events_added=added,
    )


def _events_by_id(composition: CompositionV2) -> dict[str, CompositionV2NoteEvent]:
    indexed: dict[str, CompositionV2NoteEvent] = {}
    for track in composition.tracks:
        for event in track.events:
            if event.id:
                indexed[event.id] = event
    return indexed


def _same_identity(left: CompositionV2NoteEvent, right: CompositionV2NoteEvent) -> bool:
    return (
        left.pitch == right.pitch
        and left.duration_ticks == right.duration_ticks
        and left.velocity == right.velocity
    )


def _motif_event_ids(composition: CompositionV2) -> set[str]:
    ids: set[str] = set()
    for motif in composition.motifs:
        for occurrence in motif.occurrences:
            ids.update(occurrence.event_ids)
    return ids


def _original_motif_bars(composition: CompositionV2, timeline: CompiledTimeline) -> set[int]:
    bars: set[int] = set()
    indexed = _events_by_id(composition)
    for motif in composition.motifs:
        for occurrence in motif.occurrences:
            if occurrence.relationship != "original":
                continue
            for event_id in occurrence.event_ids:
                event = indexed.get(event_id)
                if event is None:
                    continue
                bars.add(timeline.bar_at_tick(min(event.start_tick, timeline.duration_ticks - 1)))
    return bars


def _climax_bars(composition: CompositionV2, timeline: CompiledTimeline) -> set[int]:
    index = resolve_climax_section_index(
        composition,
        requested_climax_section_index=None,
        brief_text=None,
        settings=_NO_CHORUS_CLIMAX,
    )
    if index is None:
        return set()
    section = composition.sections[index]
    return set(range(section.start_bar, section.start_bar + section.bar_count))


def _climax_sections(composition: CompositionV2) -> list[CompositionV2Section]:
    index = resolve_climax_section_index(
        composition,
        requested_climax_section_index=None,
        brief_text=None,
        settings=_NO_CHORUS_CLIMAX,
    )
    if index is None:
        return []
    return [composition.sections[index]]


def _protected_bars(
    composition: CompositionV2,
    timeline: CompiledTimeline,
    previous: FilmTimelineSnapshot,
    desired: list[int],
) -> set[int]:
    protected = set(_original_motif_bars(composition, timeline))
    protected.update(_climax_bars(composition, timeline))
    for cue in previous.cues:
        if not _is_sync(cue):
            continue
        bar = _bar_for_video(timeline, previous, float(cue.video_seconds))
        if bar is not None:
            protected.add(bar)
    return protected


def _covered_bars(
    timeline: CompiledTimeline,
    previous: FilmTimelineSnapshot,
    edit: FilmPictureEdit,
) -> list[int]:
    if getattr(edit, "kind", None) != "delete_span":
        return []
    start = _video_to_tick(timeline, previous, float(edit.start_seconds))
    end = _video_to_tick(timeline, previous, float(edit.end_seconds))
    bars: list[int] = []
    for bar in range(1, timeline.bar_count + 1):
        bar_start = timeline.bar_start_tick(bar)
        bar_end = timeline.bar_end_tick(bar)
        overlap = max(0.0, min(end, bar_end) - max(start, bar_start))
        length = bar_end - bar_start
        if length <= 0:
            continue
        if round_half_away_from_zero(overlap / length) >= 1:
            bars.append(bar)
    return bars


def _insert_bar_count(
    timeline: CompiledTimeline,
    previous: FilmTimelineSnapshot,
    edit: FilmPictureEdit,
) -> int:
    if getattr(edit, "kind", None) != "insert_span":
        return 0
    start = _video_to_tick(timeline, previous, float(edit.at_seconds))
    end = _video_to_tick(timeline, previous, float(edit.at_seconds) + float(edit.duration_seconds))
    length = max(0.0, end - start)
    bar_ticks = _bar_ticks_timeline(timeline)
    if bar_ticks <= 0:
        return 0
    return max(0, round_half_away_from_zero(length / bar_ticks))


def _leftover_frames(
    timeline: CompiledTimeline,
    previous: FilmTimelineSnapshot,
    edit: FilmPictureEdit,
    bars: list[int],
) -> int:
    if getattr(edit, "kind", None) != "delete_span":
        return 0
    span_ticks = _video_to_tick(timeline, previous, float(edit.end_seconds)) - _video_to_tick(
        timeline, previous, float(edit.start_seconds)
    )
    covered = 0
    for bar in bars:
        covered += timeline.bar_end_tick(bar) - timeline.bar_start_tick(bar)
    leftover_ticks = max(0.0, span_ticks - covered)
    seconds = leftover_ticks * 60.0 / max(timeline.root_tempo, 1) / timeline.ticks_per_quarter
    return _frame_index(seconds, _rate(previous))


def _section_for_edit(
    composition: CompositionV2,
    timeline: CompiledTimeline,
    previous: FilmTimelineSnapshot,
    edit: FilmPictureEdit,
):
    if getattr(edit, "kind", None) == "delete_span":
        seconds = float(edit.start_seconds)
    elif getattr(edit, "kind", None) == "insert_span":
        seconds = float(edit.at_seconds)
    else:
        seconds = float(edit.to_seconds)
    bar = _bar_for_video(timeline, previous, seconds) or 1
    section = _section_containing_bar(composition, bar)
    return _section_index(composition, section), section


def _section_containing_bar(composition: CompositionV2, bar: int) -> CompositionV2Section:
    for section in composition.sections:
        if section.start_bar <= bar <= section.start_bar + section.bar_count - 1:
            return section
    return composition.sections[-1]


def _section_index(composition: CompositionV2, section: CompositionV2Section) -> int:
    for index, item in enumerate(composition.sections):
        if item.id == section.id and item.start_bar == section.start_bar:
            return index
    return 0


def _section_end(section: CompositionV2Section) -> int:
    return section.start_bar + section.bar_count - 1


def _is_transition(composition: CompositionV2, bars: list[int]) -> bool:
    if not bars:
        return False
    for section in composition.sections:
        last = set(range(max(section.start_bar, _section_end(section) - FILM_TRANSITION_BARS + 1), _section_end(section) + 1))
        first = set(range(section.start_bar, min(_section_end(section), section.start_bar + FILM_TRANSITION_BARS - 1) + 1))
        edges = last | first
        if set(bars).issubset(edges) and _section_is_transition(section):
            return True
        if set(bars).issubset(last) and _section_is_transition(section):
            return True
    return False


def _section_is_transition(section: CompositionV2Section) -> bool:
    label = (section.label or "").lower()
    identifier = (section.id or "").lower()
    return section.type == "bridge" or "transition" in label or "transition" in identifier


def _is_silent_gap(previous: FilmTimelineSnapshot, at_seconds: float) -> bool:
    stopped = any(cue.kind == "music_stop" and float(cue.video_seconds) <= at_seconds for cue in previous.cues)
    started = any(cue.kind == "music_start" and float(cue.video_seconds) >= at_seconds for cue in previous.cues)
    return stopped and started


def _is_sync(cue: FilmCueSnapshot) -> bool:
    return cue.kind in _SYNC_KINDS and cue.importance in _SYNC_IMPORTANCE


def _section_cues_land(
    composition: CompositionV2,
    timeline: CompiledTimeline,
    previous: FilmTimelineSnapshot,
    next_timeline: FilmTimelineSnapshot,
    section: CompositionV2Section,
) -> bool:
    return _all_sync_cues_land(timeline, previous, next_timeline)


def _all_sync_cues_land(
    timeline: CompiledTimeline,
    origin: FilmTimelineSnapshot,
    picture: FilmTimelineSnapshot,
) -> bool:
    for cue in picture.cues:
        if _is_sync(cue) and not _cue_lands(timeline, origin, cue):
            return False
    return True


def _cue_lands(timeline: CompiledTimeline, origin: FilmTimelineSnapshot, cue: FilmCueSnapshot) -> bool:
    if cue.kind in _BOUNDARY_KINDS:
        return True
    delta = _frame_delta(timeline, origin, float(cue.video_seconds))
    return delta <= int(cue.tolerance_frames)


def _frame_delta(timeline: CompiledTimeline, origin: FilmTimelineSnapshot, video_seconds: float) -> int:
    tick = _video_to_tick(timeline, origin, video_seconds)
    beat = _nearest_beat(timeline, tick)
    beat_video = _tick_to_video(timeline, origin, beat)
    return abs(_frame_index(video_seconds, _rate(origin)) - _frame_index(beat_video, _rate(origin)))


def _nearest_beat(timeline: CompiledTimeline, tick: float) -> int:
    meter = timeline.active_time_signature(int(min(max(tick, 0), timeline.duration_ticks)))
    numerator = int(meter.split("/")[0])
    beat = max(1, bar_duration_ticks(meter, timeline.ticks_per_quarter) // numerator)
    nearest = round_half_away_from_zero(tick / beat) * beat
    return max(0, min(int(nearest), timeline.duration_ticks))


def _video_to_tick(timeline: CompiledTimeline, origin: FilmTimelineSnapshot, video_seconds: float) -> float:
    origin_seconds = timeline.tick_to_seconds(min(int(origin.musical_origin_tick), timeline.duration_ticks))
    score_seconds = origin_seconds + (float(video_seconds) - float(origin.video_origin_seconds))
    if score_seconds <= 0:
        return 0.0
    total = timeline.total_duration_seconds()
    if score_seconds >= total:
        return float(timeline.duration_ticks)
    raw = float(timeline.seconds_to_tick(score_seconds))
    return float(round_half_away_from_zero(raw))


def _tick_to_video(timeline: CompiledTimeline, origin: FilmTimelineSnapshot, tick: int) -> float:
    origin_seconds = timeline.tick_to_seconds(min(int(origin.musical_origin_tick), timeline.duration_ticks))
    bounded = max(0, min(int(tick), timeline.duration_ticks))
    return float(origin.video_origin_seconds) + timeline.tick_to_seconds(bounded) - origin_seconds


def _bar_for_video(timeline: CompiledTimeline, origin: FilmTimelineSnapshot, video_seconds: float) -> int | None:
    tick = _video_to_tick(timeline, origin, video_seconds)
    if tick >= timeline.duration_ticks:
        return timeline.bar_count
    return timeline.bar_at_tick(int(tick))


def _section_video_window(
    timeline: CompiledTimeline,
    origin: FilmTimelineSnapshot,
    section: CompositionV2Section,
) -> tuple[float, float]:
    end_tick = section.start_tick + section.duration_ticks
    return (
        _tick_to_video(timeline, origin, section.start_tick),
        _tick_to_video(timeline, origin, end_tick),
    )


def _timeline_with_tempo(
    composition: CompositionV2,
    root: int,
    rows: list[tuple[int, int]],
) -> CompiledTimeline:
    data = composition.model_dump(mode="json")
    data["tempo"] = root
    data["tempo_changes"] = [
        {"tick": tick, "bpm": bpm}
        for tick, bpm in rows
        if 0 < tick < int(data["duration_ticks"])
    ]
    return compile_timeline(CompositionV2.model_validate(data))


def _upsert_tempo(rows: list[tuple[int, int]], tick: int, bpm: int) -> list[tuple[int, int]]:
    updated = [(item_tick, item_bpm) for item_tick, item_bpm in rows if item_tick != tick]
    if tick > 0:
        updated.append((tick, bpm))
    updated.sort(key=lambda item: item[0])
    return updated


def _edit_overlaps_bars(
    timeline: CompiledTimeline,
    previous: FilmTimelineSnapshot,
    edit: FilmPictureEdit,
    bars: set[int],
) -> bool:
    return bool(set(_covered_bars(timeline, previous, edit)).intersection(bars))


def _op_plan(
    edit: FilmPictureEdit,
    *,
    strategy: str,
    start_bar: int,
    end_bar: int,
    bars_delta: int,
    tempo_bpm: int | None,
    section_id: str,
    reason: str,
    section_index: int,
) -> _OpPlan:
    operation = FilmAdaptOperation(
        op_id=edit.op_id,
        kind=edit.kind,
        strategy=strategy,
        start_bar=start_bar,
        end_bar=max(end_bar, start_bar),
        bars_delta=bars_delta,
        tempo_bpm=tempo_bpm,
        section_id=section_id,
        reason_code=reason,
    )
    return _OpPlan(operation=operation, tempo_bpm=tempo_bpm, section_index=section_index)


def _reason(edit: FilmPictureEdit, previous: FilmTimelineSnapshot) -> str:
    if getattr(edit, "kind", None) == "move_hit":
        return "hit_move"
    if getattr(edit, "kind", None) == "insert_span" and _is_silent_gap(previous, float(edit.at_seconds)):
        return "silent_gap"
    if getattr(edit, "kind", None) == "insert_span":
        return "picture_extend"
    return "picture_shorten"


def _timelines_match(expected: FilmTimelineSnapshot, stored: FilmTimelineSnapshot) -> bool:
    if (
        expected.frame_rate_numerator != stored.frame_rate_numerator
        or expected.frame_rate_denominator != stored.frame_rate_denominator
        or expected.musical_origin_tick != stored.musical_origin_tick
    ):
        return False
    rate = _rate(expected)
    if not _same_frame(expected.duration_seconds, stored.duration_seconds, rate):
        return False
    if not _same_frame(expected.video_origin_seconds, stored.video_origin_seconds, rate):
        return False
    if {cue.id for cue in expected.cues} != {cue.id for cue in stored.cues}:
        return False
    stored_by_id = {cue.id: cue for cue in stored.cues}
    for cue in expected.cues:
        if not _same_frame(cue.video_seconds, stored_by_id[cue.id].video_seconds, rate):
            return False
    return True


def _rate(snapshot: FilmTimelineSnapshot) -> float:
    return snapshot.frame_rate_numerator / snapshot.frame_rate_denominator


def _same_frame(left: float, right: float, rate: float) -> bool:
    return _frame_index(left, rate) == _frame_index(right, rate)


def _frame_index(seconds: float, rate: float) -> int:
    return math.floor(float(seconds) * rate + 1e-9)


def _overlap(start: float, end: float, other_start: float, other_end: float) -> float:
    return max(0.0, min(end, other_end) - max(start, other_start))


def _bar_ticks(composition: CompositionV2) -> int:
    return bar_duration_ticks(composition.time_signature, composition.ticks_per_quarter)


def _bar_ticks_timeline(timeline: CompiledTimeline) -> int:
    return bar_duration_ticks(timeline.root_time_signature, timeline.ticks_per_quarter)


def _adapt_id(source: str, bar_index: int) -> str:
    digest = hashlib.sha256(f"{source}:{bar_index}".encode("utf-8")).hexdigest()[:8]
    return f"adapt_{digest}"


def _warn(warnings: list[str], *codes: str) -> list[str]:
    merged = list(warnings)
    for code in codes:
        if code and code not in merged:
            merged.append(code)
    return merged[:32]


def _has_structural_shift(plans: list[_OpPlan]) -> int:
    return sum(plan.tick_shift for plan in plans)
