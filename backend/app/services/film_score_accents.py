"""Place one tonic attack on aligned sync cues that the landing check would miss.

Dialogue-sparse bars and unsatisfiable cues stay untouched. Tempo and
``tempo_changes`` are not rewritten here.
"""

from __future__ import annotations

import logging

from app.composition_schemas import CompositionV2, CompositionV2NoteEvent, bar_duration_ticks
from app.film_score_schemas import FilmCueSnapshot, FilmScorePlanV1
from app.services.composition_timeline import compile_timeline
from app.services.film_score_tempo import SYNC_IMPORTANCE, SYNC_KINDS, alignment_beat_tick
from app.services.video_spotting import verify_cue_landings
from app.video_scoring_schemas import HitPointV1

logger = logging.getLogger(__name__)


def apply_film_score_accents(
    composition: CompositionV2,
    plan: FilmScorePlanV1,
    cues: list[FilmCueSnapshot],
    *,
    frame_rate_numerator: int,
    frame_rate_denominator: int,
    duration_seconds: float,
    timecode_mode: str = "non_drop",
    start_timecode: str = "00:00:00:00",
) -> CompositionV2:
    """Insert missing attacks. Returns a new composition when a note is added."""
    logger.debug(
        "apply_film_score_accents start cue_count=%s",
        len(cues),
    )
    tempo = composition.tempo
    tempo_changes = list(composition.tempo_changes)
    timeline = compile_timeline(composition)
    landings = {
        row.id: row.status
        for row in verify_cue_landings(
            [_hit_point(cue) for cue in cues],
            composition,
            timeline=timeline,
            duration_seconds=duration_seconds,
            frame_rate_numerator=frame_rate_numerator,
            frame_rate_denominator=frame_rate_denominator,
            timecode_mode=timecode_mode,
            start_timecode=start_timecode,
            video_origin_seconds=plan.music_start_seconds,
            musical_origin_tick=0,
        )
    }
    by_id = {cue.id: cue for cue in cues}
    beat_ticks = bar_duration_ticks(plan.time_signature, composition.ticks_per_quarter) // _beats_per_bar(
        plan.time_signature
    )
    if beat_ticks < 1:
        beat_ticks = composition.ticks_per_quarter
    tracks = [track.model_copy(deep=True) for track in composition.tracks]
    track_index = _melody_track_index(tracks)
    inserted = 0
    for row in plan.hit_alignments:
        cue = by_id.get(row.cue_id)
        if cue is None or not _should_insert(row, cue, plan):
            continue
        if landings.get(cue.id) == "landed":
            continue
        start_tick = alignment_beat_tick(
            plan,
            cue,
            ticks_per_quarter=composition.ticks_per_quarter,
        )
        if start_tick is None or start_tick < 0 or start_tick >= composition.duration_ticks:
            continue
        duration = min(beat_ticks, composition.duration_ticks - start_tick)
        if duration < 1:
            continue
        tracks[track_index].events.append(
            CompositionV2NoteEvent(
                pitch=_tonic_pitch(plan.key),
                start_tick=start_tick,
                duration_ticks=duration,
                velocity=96,
            )
        )
        inserted += 1
        logger.debug(
            "film score accent cue_id=%s start_tick=%s status=%s",
            cue.id,
            start_tick,
            row.status,
        )
    updated = composition.model_copy(update={"tracks": tracks, "tempo": tempo, "tempo_changes": tempo_changes})
    logger.debug("apply_film_score_accents finish inserted=%s", inserted)
    return updated


def _should_insert(row, cue: FilmCueSnapshot, plan: FilmScorePlanV1) -> bool:
    if row.status != "aligned":
        return False
    if cue.importance not in SYNC_IMPORTANCE or cue.kind not in SYNC_KINDS:
        return False
    if cue.kind == "dialogue" or row.kind == "dialogue":
        return False
    if row.target_bar is None:
        return False
    for region in plan.density_regions:
        if region.density == "sparse" and region.start_bar <= row.target_bar <= region.end_bar:
            return False
    return True


def _hit_point(cue: FilmCueSnapshot) -> HitPointV1:
    return HitPointV1(
        id=cue.id,
        kind=cue.kind,
        label="cue",
        video_seconds=cue.video_seconds,
        musical_tick=0,
        tolerance_frames=cue.tolerance_frames,
        importance=cue.importance,
    )


def _melody_track_index(tracks) -> int:
    for index, track in enumerate(tracks):
        if track.role == "melody":
            return index
    return 0


def _beats_per_bar(time_signature: str) -> int:
    numerator, _denominator = (int(part) for part in time_signature.split("/"))
    return max(1, numerator)


def _tonic_pitch(key: str) -> str:
    token = key.strip().split()[0]
    return f"{token}4"
