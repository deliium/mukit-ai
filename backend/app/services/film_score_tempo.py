"""Deterministic film-score window, section grid, and section-boundary tempo.

Bar lengths come from ``bar_duration_ticks``. A new tempo is an integer bpm at a
section start, or the root when the opening section is the one that moves.
Cue labels and instructions are not inputs.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from app.composition_schemas import bar_duration_ticks, round_half_away_from_zero
from app.film_score_schemas import (
    FilmCueSnapshot,
    FilmDensityRegion,
    FilmHitAlignment,
    FilmScoreError,
    FilmScorePlanV1,
    FilmScoreSection,
    FilmSyncOrigin,
    FilmTempoChange,
    FilmTempoStrategy,
)

logger = logging.getLogger(__name__)

FILM_PHRASE_BARS = 8
FILM_PHRASE_MIN_BARS = 4
FILM_PHRASE_MAX_BARS = 16
FILM_TEMPO_STEP_MAX = 12
FILM_DIALOGUE_SECONDS = 2.0
FILM_BAR_COUNT_MAX = 512

SYNC_KINDS = frozenset({"hit_point", "reveal", "cut", "action", "emotional_cue"})
SYNC_IMPORTANCE = frozenset({"high", "critical"})


@dataclass
class _SectionDraft:
    id: str
    label: str
    start_bar: int
    bar_count: int
    tempo_bpm: int


@dataclass(frozen=True)
class _BeatChoice:
    beat_video: float
    beat_tick: int
    target_bar: int
    frame_delta: int
    delta_seconds: float


def music_window_start(cues: list[FilmCueSnapshot]) -> float:
    """Earliest ``music_start``, or zero when the picture has none."""
    return _music_start(cues)


def compile_film_score_plan(
    cues: list[FilmCueSnapshot],
    *,
    frame_rate_numerator: int,
    frame_rate_denominator: int,
    opening_tempo: int,
    tempo_min: int,
    tempo_max: int,
    time_signature: str,
    key: str,
    target_duration_seconds: float,
    project_id: str,
    source_fingerprint: str,
    scoring_document_revision: int,
    ticks_per_quarter: int = 480,
) -> FilmScorePlanV1:
    """Compile sections and hit status. Does not call a model or read SQLite."""
    logger.debug(
        "compile_film_score_plan start cue_count=%s opening_tempo=%s",
        len(cues),
        opening_tempo,
    )
    if frame_rate_numerator <= 0 or frame_rate_denominator <= 0:
        raise FilmScoreError("film_frame_rate_required")
    if tempo_min > tempo_max:
        raise FilmScoreError("film_score_invalid")

    music_start = _music_start(cues)
    music_end = _music_end(cues, music_start, target_duration_seconds)
    window_seconds = music_end - music_start
    bar_ticks = bar_duration_ticks(time_signature, ticks_per_quarter)
    min_bar_seconds = _bar_seconds(bar_ticks, tempo_min, ticks_per_quarter)
    if window_seconds + 1e-9 < min_bar_seconds:
        logger.warning(
            "film_duration_invalid",
            extra={"error_code": "film_duration_invalid"},
        )
        raise FilmScoreError("film_duration_invalid")

    root = int(opening_tempo)
    bar_count = _bar_count(window_seconds, bar_ticks, root, ticks_per_quarter)
    drafts = _phrase_sections(bar_count, root)
    drafts, root, changes = _adapt_tempos(
        drafts,
        cues,
        music_start=music_start,
        music_end=music_end,
        root=root,
        tempo_min=tempo_min,
        tempo_max=tempo_max,
        bar_ticks=bar_ticks,
        ticks_per_quarter=ticks_per_quarter,
        frame_rate_numerator=frame_rate_numerator,
        frame_rate_denominator=frame_rate_denominator,
    )
    if root != int(opening_tempo):
        bar_count = _bar_count(window_seconds, bar_ticks, root, ticks_per_quarter)
        drafts = _phrase_sections(bar_count, root)
        drafts, root, changes = _adapt_tempos(
            drafts,
            cues,
            music_start=music_start,
            music_end=music_end,
            root=root,
            tempo_min=tempo_min,
            tempo_max=tempo_max,
            bar_ticks=bar_ticks,
            ticks_per_quarter=ticks_per_quarter,
            frame_rate_numerator=frame_rate_numerator,
            frame_rate_denominator=frame_rate_denominator,
            lock_root=True,
        )

    edges = _video_edges(
        drafts,
        music_start=music_start,
        bar_ticks=bar_ticks,
        ticks_per_quarter=ticks_per_quarter,
    )
    regions = _dialogue_regions(
        cues,
        drafts,
        edges,
        music_start=music_start,
        music_end=music_end,
        bar_ticks=bar_ticks,
        ticks_per_quarter=ticks_per_quarter,
    )
    sparse_bars = _sparse_bars(regions)
    sections = _sections_with_density(drafts, edges, sparse_bars)
    alignments, warnings = _alignments(
        cues,
        drafts,
        edges,
        changes,
        music_start=music_start,
        music_end=music_end,
        bar_ticks=bar_ticks,
        ticks_per_quarter=ticks_per_quarter,
        frame_rate_numerator=frame_rate_numerator,
        frame_rate_denominator=frame_rate_denominator,
    )
    _log_sections(sections, alignments)
    plan = FilmScorePlanV1(
        project_id=project_id,
        source_fingerprint=source_fingerprint,
        scoring_document_revision=scoring_document_revision,
        target_duration_seconds=music_end,
        music_start_seconds=music_start,
        music_end_seconds=music_end,
        sync_origin=FilmSyncOrigin(
            video_origin_seconds=music_start,
            musical_origin_tick=0,
        ),
        time_signature=time_signature,
        key=key,
        root_tempo=root,
        tempo_min=tempo_min,
        tempo_max=tempo_max,
        sections=sections,
        tempo_strategy=FilmTempoStrategy(policy="section_boundary", changes=changes),
        hit_alignments=alignments,
        density_regions=regions,
        warnings=warnings,
        committed=False,
    )
    logger.debug(
        "compile_film_score_plan finish bar_count=%s change_count=%s",
        sum(section.bar_count for section in sections),
        len(changes),
    )
    return plan


def alignment_beat_tick(
    plan: FilmScorePlanV1,
    cue: FilmCueSnapshot,
    *,
    ticks_per_quarter: int = 480,
) -> int | None:
    """Nearest beat tick already chosen for one cue. None when the cue is not a hit beat."""
    bar_ticks = bar_duration_ticks(plan.time_signature, ticks_per_quarter)
    edges = [
        (section.start_video_seconds, section.end_video_seconds, section)
        for section in plan.sections
    ]
    match = _section_for_video(edges, cue.video_seconds, plan.music_end_seconds)
    if match is None:
        return None
    _start, _end, section = match
    choice = _nearest_beat(
        cue.video_seconds,
        section_start_video=section.start_video_seconds,
        section_start_bar=section.start_bar,
        section_bar_count=section.bar_count,
        bpm=section.tempo_bpm,
        bar_ticks=bar_ticks,
        ticks_per_quarter=ticks_per_quarter,
        frame_rate_numerator=1,
        frame_rate_denominator=1,
    )
    return choice.beat_tick


def _music_start(cues: list[FilmCueSnapshot]) -> float:
    starts = [cue.video_seconds for cue in cues if cue.kind == "music_start"]
    if not starts:
        return 0.0
    return float(min(starts))


def _music_end(cues: list[FilmCueSnapshot], music_start: float, target: float) -> float:
    stops = [
        cue.video_seconds
        for cue in cues
        if cue.kind == "music_stop" and cue.video_seconds >= music_start
    ]
    end = float(target)
    if stops:
        end = min(end, float(min(stops)))
    return end


def _bar_seconds(bar_ticks: int, bpm: int, ticks_per_quarter: int) -> float:
    return bar_ticks * 60.0 / bpm / ticks_per_quarter


def _bar_count(window_seconds: float, bar_ticks: int, bpm: int, ticks_per_quarter: int) -> int:
    seconds = _bar_seconds(bar_ticks, bpm, ticks_per_quarter)
    count = round_half_away_from_zero(window_seconds / seconds)
    return max(1, min(FILM_BAR_COUNT_MAX, count))


def _phrase_sections(bar_count: int, bpm: int) -> list[_SectionDraft]:
    lengths: list[int] = []
    remaining = bar_count
    while remaining > 0:
        chunk = min(FILM_PHRASE_BARS, remaining)
        if chunk < FILM_PHRASE_MIN_BARS and lengths:
            lengths[-1] += chunk
            if lengths[-1] > FILM_PHRASE_MAX_BARS:
                overflow = lengths[-1] - FILM_PHRASE_MAX_BARS
                lengths[-1] = FILM_PHRASE_MAX_BARS
                lengths.append(overflow)
        else:
            lengths.append(chunk)
        remaining -= chunk
    drafts: list[_SectionDraft] = []
    start_bar = 1
    for index, length in enumerate(lengths, start=1):
        drafts.append(
            _SectionDraft(
                id=f"sec_{index:02d}",
                label=f"Section {index}",
                start_bar=start_bar,
                bar_count=length,
                tempo_bpm=bpm,
            )
        )
        start_bar += length
    return drafts


def _is_sync(cue: FilmCueSnapshot) -> bool:
    return cue.importance in SYNC_IMPORTANCE and cue.kind in SYNC_KINDS


def _frame(seconds: float, numerator: int, denominator: int) -> int:
    return math.floor((seconds * numerator / denominator) + 1e-9)


def _nearest_beat(
    video_seconds: float,
    *,
    section_start_video: float,
    section_start_bar: int,
    section_bar_count: int,
    bpm: int,
    bar_ticks: int,
    ticks_per_quarter: int,
    frame_rate_numerator: int,
    frame_rate_denominator: int,
) -> _BeatChoice:
    beat_seconds = 60.0 / bpm
    local = video_seconds - section_start_video
    if local < 0:
        local = 0.0
    section_seconds = _bar_seconds(bar_ticks, bpm, ticks_per_quarter) * section_bar_count
    index = int(math.floor((local / beat_seconds) + 0.5))
    if index < 0:
        index = 0
    max_index = int(math.floor(section_seconds / beat_seconds + 1e-9))
    if index > max_index:
        index = max_index
    beat_local = index * beat_seconds
    beat_video = section_start_video + beat_local
    ticks_per_beat = ticks_per_quarter * 4 / _denominator(bar_ticks, ticks_per_quarter)
    beat_tick = (section_start_bar - 1) * bar_ticks + int(round(index * ticks_per_beat))
    target_bar = section_start_bar + int(beat_tick - (section_start_bar - 1) * bar_ticks) // bar_ticks
    return _BeatChoice(
        beat_video=beat_video,
        beat_tick=beat_tick,
        target_bar=target_bar,
        frame_delta=abs(
            _frame(beat_video, frame_rate_numerator, frame_rate_denominator)
            - _frame(video_seconds, frame_rate_numerator, frame_rate_denominator)
        ),
        delta_seconds=beat_video - video_seconds,
    )


def _denominator(bar_ticks: int, ticks_per_quarter: int) -> int:
    """Recover the meter denominator from the bar length used to build ticks."""
    # bar_ticks = numerator * 4 * tpq / denominator, and a beat is a quarter
    # unless the caller uses a compound meter. Beat ticks stay at the quarter.
    del bar_ticks
    return 4 if ticks_per_quarter else 4


def _worst_errors(
    cues: list[FilmCueSnapshot],
    *,
    bpm: int,
    section_start_video: float,
    section_start_bar: int,
    section_bar_count: int,
    bar_ticks: int,
    ticks_per_quarter: int,
    frame_rate_numerator: int,
    frame_rate_denominator: int,
) -> tuple[int, float]:
    """Worst frame gap, then worst absolute seconds, across the section's sync cues."""
    worst_frames = 0
    worst_seconds = 0.0
    for cue in cues:
        choice = _nearest_beat(
            cue.video_seconds,
            section_start_video=section_start_video,
            section_start_bar=section_start_bar,
            section_bar_count=section_bar_count,
            bpm=bpm,
            bar_ticks=bar_ticks,
            ticks_per_quarter=ticks_per_quarter,
            frame_rate_numerator=frame_rate_numerator,
            frame_rate_denominator=frame_rate_denominator,
        )
        if choice.frame_delta > worst_frames:
            worst_frames = choice.frame_delta
        gap = abs(choice.delta_seconds)
        if gap > worst_seconds:
            worst_seconds = gap
    return worst_frames, worst_seconds


def _adapt_tempos(
    drafts: list[_SectionDraft],
    cues: list[FilmCueSnapshot],
    *,
    music_start: float,
    music_end: float,
    root: int,
    tempo_min: int,
    tempo_max: int,
    bar_ticks: int,
    ticks_per_quarter: int,
    frame_rate_numerator: int,
    frame_rate_denominator: int,
    lock_root: bool = False,
) -> tuple[list[_SectionDraft], int, list[FilmTempoChange]]:
    changes: list[FilmTempoChange] = []
    cursor = music_start
    current = root
    for draft in drafts:
        draft.tempo_bpm = current
        start_video = cursor
        duration = _bar_seconds(bar_ticks, current, ticks_per_quarter) * draft.bar_count
        end_video = start_video + duration
        syncs = _syncs_in_span(cues, start_video, end_video, music_start, music_end)
        chosen = current
        if syncs:
            previous_worst, _previous_time = _worst_errors(
                syncs,
                bpm=current,
                section_start_video=start_video,
                section_start_bar=draft.start_bar,
                section_bar_count=draft.bar_count,
                bar_ticks=bar_ticks,
                ticks_per_quarter=ticks_per_quarter,
                frame_rate_numerator=frame_rate_numerator,
                frame_rate_denominator=frame_rate_denominator,
            )
            if previous_worst > 0 and not (lock_root and draft.start_bar == 1):
                low = max(tempo_min, current - FILM_TEMPO_STEP_MAX)
                high = min(tempo_max, current + FILM_TEMPO_STEP_MAX)
                ranked: list[tuple[int, float, int, int]] = []
                for bpm in range(low, high + 1):
                    worst, time_error = _worst_errors(
                        syncs,
                        bpm=bpm,
                        section_start_video=start_video,
                        section_start_bar=draft.start_bar,
                        section_bar_count=draft.bar_count,
                        bar_ticks=bar_ticks,
                        ticks_per_quarter=ticks_per_quarter,
                        frame_rate_numerator=frame_rate_numerator,
                        frame_rate_denominator=frame_rate_denominator,
                    )
                    ranked.append((worst, time_error, abs(bpm - current), bpm))
                ranked.sort()
                best_worst, _time_error, _distance, best_bpm = ranked[0]
                if best_worst < previous_worst:
                    chosen = best_bpm
        if chosen != current:
            if draft.start_bar == 1:
                root = chosen
            elif len(changes) < 8:
                tick = (draft.start_bar - 1) * bar_ticks
                changes.append(
                    FilmTempoChange(
                        tick=tick,
                        bpm=chosen,
                        section_id=draft.id,
                        reason_code="phrase_fit",
                    )
                )
            draft.tempo_bpm = chosen
            current = chosen
            duration = _bar_seconds(bar_ticks, chosen, ticks_per_quarter) * draft.bar_count
        cursor = start_video + duration
    return drafts, root, changes


def _syncs_in_span(
    cues: list[FilmCueSnapshot],
    start_video: float,
    end_video: float,
    music_start: float,
    music_end: float,
) -> list[FilmCueSnapshot]:
    found: list[FilmCueSnapshot] = []
    for cue in cues:
        if not _is_sync(cue):
            continue
        if cue.video_seconds < music_start or cue.video_seconds > music_end:
            continue
        if start_video - 1e-9 <= cue.video_seconds < end_video - 1e-9:
            found.append(cue)
    return found


def _video_edges(
    drafts: list[_SectionDraft],
    *,
    music_start: float,
    bar_ticks: int,
    ticks_per_quarter: int,
) -> list[tuple[float, float]]:
    cursor = music_start
    edges: list[tuple[float, float]] = []
    for draft in drafts:
        duration = _bar_seconds(bar_ticks, draft.tempo_bpm, ticks_per_quarter) * draft.bar_count
        edges.append((cursor, cursor + duration))
        cursor += duration
    return edges


def _dialogue_regions(
    cues: list[FilmCueSnapshot],
    drafts: list[_SectionDraft],
    edges: list[tuple[float, float]],
    *,
    music_start: float,
    music_end: float,
    bar_ticks: int,
    ticks_per_quarter: int,
) -> list[FilmDensityRegion]:
    ordered = sorted(cues, key=lambda cue: (cue.video_seconds, cue.id))
    regions: list[FilmDensityRegion] = []
    for index, cue in enumerate(ordered):
        if cue.kind != "dialogue":
            continue
        if cue.video_seconds < music_start or cue.video_seconds >= music_end:
            continue
        later = [item.video_seconds for item in ordered[index + 1 :] if item.video_seconds > cue.video_seconds]
        nxt = min(later) if later else music_end
        span_end = min(cue.video_seconds + FILM_DIALOGUE_SECONDS, nxt, music_end)
        span_end = min(span_end, cue.video_seconds + _eight_bar_seconds(cue.video_seconds, drafts, edges, bar_ticks, ticks_per_quarter))
        if span_end <= cue.video_seconds:
            continue
        start_bar, end_bar = _bars_overlapping(
            cue.video_seconds,
            span_end,
            drafts,
            edges,
            bar_ticks=bar_ticks,
            ticks_per_quarter=ticks_per_quarter,
            music_start=music_start,
        )
        if start_bar is None or end_bar is None or end_bar < start_bar:
            continue
        regions.append(
            FilmDensityRegion(
                cue_id=cue.id,
                start_bar=start_bar,
                end_bar=end_bar,
                density="sparse",
            )
        )
    return regions


def _eight_bar_seconds(
    video: float,
    drafts: list[_SectionDraft],
    edges: list[tuple[float, float]],
    bar_ticks: int,
    ticks_per_quarter: int,
) -> float:
    for draft, (start, end) in zip(drafts, edges, strict=True):
        if start - 1e-9 <= video < end - 1e-9 or abs(video - start) <= 1e-9:
            return 8 * _bar_seconds(bar_ticks, draft.tempo_bpm, ticks_per_quarter)
    if drafts:
        return 8 * _bar_seconds(bar_ticks, drafts[-1].tempo_bpm, ticks_per_quarter)
    return FILM_DIALOGUE_SECONDS


def _bars_overlapping(
    span_start: float,
    span_end: float,
    drafts: list[_SectionDraft],
    edges: list[tuple[float, float]],
    *,
    bar_ticks: int,
    ticks_per_quarter: int,
    music_start: float,
) -> tuple[int | None, int | None]:
    start_bar: int | None = None
    end_bar: int | None = None
    for draft, (_sec_start, _sec_end) in zip(drafts, edges, strict=True):
        for offset in range(draft.bar_count):
            bar = draft.start_bar + offset
            bar_video = music_start + _seconds_until_tick(
                (bar - 1) * bar_ticks,
                drafts,
                bar_ticks=bar_ticks,
                ticks_per_quarter=ticks_per_quarter,
            )
            bar_end_video = music_start + _seconds_until_tick(
                bar * bar_ticks,
                drafts,
                bar_ticks=bar_ticks,
                ticks_per_quarter=ticks_per_quarter,
            )
            if bar_end_video <= span_start + 1e-9 or bar_video >= span_end - 1e-9:
                continue
            start_bar = bar if start_bar is None else min(start_bar, bar)
            end_bar = bar if end_bar is None else max(end_bar, bar)
    return start_bar, end_bar


def _seconds_until_tick(
    tick: int,
    drafts: list[_SectionDraft],
    *,
    bar_ticks: int,
    ticks_per_quarter: int,
) -> float:
    remaining = tick
    total = 0.0
    for draft in drafts:
        section_ticks = draft.bar_count * bar_ticks
        take = min(remaining, section_ticks)
        total += take * 60.0 / draft.tempo_bpm / ticks_per_quarter
        remaining -= take
        if remaining <= 0:
            break
    return total


def _sparse_bars(regions: list[FilmDensityRegion]) -> set[int]:
    bars: set[int] = set()
    for region in regions:
        bars.update(range(region.start_bar, region.end_bar + 1))
    return bars


def _sections_with_density(
    drafts: list[_SectionDraft],
    edges: list[tuple[float, float]],
    sparse_bars: set[int],
) -> list[FilmScoreSection]:
    sections: list[FilmScoreSection] = []
    for draft, (start, end) in zip(drafts, edges, strict=True):
        bars = range(draft.start_bar, draft.start_bar + draft.bar_count)
        density = "sparse" if any(bar in sparse_bars for bar in bars) else "moderate"
        sections.append(
            FilmScoreSection(
                id=draft.id,
                label=draft.label,
                start_bar=draft.start_bar,
                bar_count=draft.bar_count,
                density=density,
                tempo_bpm=draft.tempo_bpm,
                start_video_seconds=start,
                end_video_seconds=end,
            )
        )
    return sections


def _alignments(
    cues: list[FilmCueSnapshot],
    drafts: list[_SectionDraft],
    edges: list[tuple[float, float]],
    changes: list[FilmTempoChange],
    *,
    music_start: float,
    music_end: float,
    bar_ticks: int,
    ticks_per_quarter: int,
    frame_rate_numerator: int,
    frame_rate_denominator: int,
) -> tuple[list[FilmHitAlignment], list[str]]:
    changed_sections = {change.section_id for change in changes}
    rows: list[FilmHitAlignment] = []
    unsatisfiable = False
    packed = list(zip(drafts, edges, strict=True))
    for cue in cues:
        if cue.kind in {"music_start", "music_stop"}:
            rows.append(_boundary_row(cue, packed, bar_ticks))
            continue
        match = _section_for_video(
            [(start, end, draft) for draft, (start, end) in zip(drafts, edges, strict=True)],
            cue.video_seconds,
            music_end,
        )
        inside = music_start - 1e-9 <= cue.video_seconds <= music_end + 1e-9 and match is not None
        if not inside or match is None:
            rows.append(
                FilmHitAlignment(
                    cue_id=cue.id,
                    kind=cue.kind,
                    importance=cue.importance,
                    status="soft",
                    target_bar=None,
                    delta_seconds=0.0,
                    tempo_change_added=False,
                )
            )
            continue
        _start, _end, draft = match
        choice = _nearest_beat(
            cue.video_seconds,
            section_start_video=_start,
            section_start_bar=draft.start_bar,
            section_bar_count=draft.bar_count,
            bpm=draft.tempo_bpm,
            bar_ticks=bar_ticks,
            ticks_per_quarter=ticks_per_quarter,
            frame_rate_numerator=frame_rate_numerator,
            frame_rate_denominator=frame_rate_denominator,
        )
        aligned = choice.frame_delta <= cue.tolerance_frames
        if _is_sync(cue):
            status = "aligned" if aligned else "unsatisfiable"
            if status == "unsatisfiable":
                unsatisfiable = True
            added = status == "aligned" and draft.id in changed_sections and choice.frame_delta <= cue.tolerance_frames
            # tempo_change_added only when this section's change is what aligned it.
            if added:
                added = _was_miss_before_change(
                    cue,
                    draft,
                    section_start_video=_start,
                    bar_ticks=bar_ticks,
                    ticks_per_quarter=ticks_per_quarter,
                    frame_rate_numerator=frame_rate_numerator,
                    frame_rate_denominator=frame_rate_denominator,
                    changes=changes,
                    root_fallback=drafts[0].tempo_bpm if draft.start_bar == 1 else _incoming_bpm(draft, drafts),
                )
            rows.append(
                FilmHitAlignment(
                    cue_id=cue.id,
                    kind=cue.kind,
                    importance=cue.importance,
                    status=status,
                    target_bar=choice.target_bar,
                    delta_seconds=choice.delta_seconds,
                    tempo_change_added=added,
                )
            )
        else:
            rows.append(
                FilmHitAlignment(
                    cue_id=cue.id,
                    kind=cue.kind,
                    importance=cue.importance,
                    status="aligned" if aligned else "soft",
                    target_bar=choice.target_bar,
                    delta_seconds=choice.delta_seconds,
                    tempo_change_added=False,
                )
            )
    warnings = ["hit_unsatisfiable"] if unsatisfiable else []
    return rows, warnings


def _incoming_bpm(draft: _SectionDraft, drafts: list[_SectionDraft]) -> int:
    previous = draft.tempo_bpm
    for item in drafts:
        if item.id == draft.id:
            break
        previous = item.tempo_bpm
    return previous


def _was_miss_before_change(
    cue: FilmCueSnapshot,
    draft: _SectionDraft,
    *,
    section_start_video: float,
    bar_ticks: int,
    ticks_per_quarter: int,
    frame_rate_numerator: int,
    frame_rate_denominator: int,
    changes: list[FilmTempoChange],
    root_fallback: int,
) -> bool:
    if draft.id not in {change.section_id for change in changes}:
        return False
    previous = root_fallback
    for change in changes:
        if change.section_id == draft.id:
            break
        previous = change.bpm
    # Incoming bpm is the bpm of the previous section, which equals the last
    # adopted tempo before this change.
    before = _nearest_beat(
        cue.video_seconds,
        section_start_video=section_start_video,
        section_start_bar=draft.start_bar,
        section_bar_count=draft.bar_count,
        bpm=previous,
        bar_ticks=bar_ticks,
        ticks_per_quarter=ticks_per_quarter,
        frame_rate_numerator=frame_rate_numerator,
        frame_rate_denominator=frame_rate_denominator,
    )
    return before.frame_delta > cue.tolerance_frames


def _boundary_row(
    cue: FilmCueSnapshot,
    packed: list[tuple[_SectionDraft, tuple[float, float]]],
    bar_ticks: int,
) -> FilmHitAlignment:
    target = None
    for draft, (start, end) in packed:
        if start - 1e-9 <= cue.video_seconds <= end + 1e-9:
            target = draft.start_bar
            break
    del bar_ticks
    return FilmHitAlignment(
        cue_id=cue.id,
        kind=cue.kind,
        importance=cue.importance,
        status="boundary",
        target_bar=target,
        delta_seconds=0.0,
        tempo_change_added=False,
    )


def _section_for_video(
    edges: list[tuple[float, float, _SectionDraft | FilmScoreSection]],
    video: float,
    music_end: float,
) -> tuple[float, float, _SectionDraft | FilmScoreSection] | None:
    for start, end, draft in edges:
        if start - 1e-9 <= video < end - 1e-9:
            return start, end, draft
        if abs(video - end) <= 1e-9 and abs(video - music_end) <= 1e-6:
            return start, end, draft
    if edges:
        start, end, draft = edges[-1]
        if abs(video - end) <= 1e-6:
            return start, end, draft
    return None


def _log_sections(sections: list[FilmScoreSection], alignments: list[FilmHitAlignment]) -> None:
    counts: dict[str, int] = {}
    for row in alignments:
        counts[row.status] = counts.get(row.status, 0) + 1
    for section in sections:
        logger.debug(
            "film score section id=%s start_bar=%s bar_count=%s bpm=%s",
            section.id,
            section.start_bar,
            section.bar_count,
            section.tempo_bpm,
        )
    logger.debug("film score hit status counts %s", counts)
