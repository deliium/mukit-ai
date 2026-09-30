"""Pure spotting landing check. No SQLite, FastAPI, file I/O, or model client."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Literal, Sequence

from app.composition_schemas import midi_pitch_number
from app.services.composition_timeline import CompiledTimeline
from app.services.video_scoring_map import parse_timecode, video_seconds_from_score
from app.video_scoring_schemas import HitPointV1, VideoScoringError, is_closed_frame_rate

logger = logging.getLogger(__name__)

LandingStatus = Literal["landed", "missed", "empty"]
MAX_LANDING_MATCHES = 8


@dataclass(frozen=True)
class LandingMatch:
    track_index: int
    event_index: int
    start_tick: int
    pitch: int


@dataclass(frozen=True)
class CueLanding:
    id: str
    kind: str
    timecode: str | None
    tolerance_frames: int
    status: LandingStatus
    delta_frames: int | None
    match_count: int
    matches: tuple[LandingMatch, ...]


@dataclass(frozen=True)
class _Attack:
    track_index: int
    event_index: int
    start_tick: int
    pitch: int
    event_video: float
    event_frame: int
    in_picture: bool


def cue_frame_index(
    cue: HitPointV1,
    *,
    frame_rate_numerator: int,
    frame_rate_denominator: int,
    timecode_mode: str,
    start_timecode: str,
) -> int:
    """Authored ``frames_from_zero``. A null timecode floors stored ``video_seconds``."""
    if not is_closed_frame_rate(frame_rate_numerator, frame_rate_denominator):
        raise VideoScoringError("video_frame_rate_required")
    if cue.timecode is not None:
        frames = parse_timecode(
            cue.timecode,
            frame_rate_numerator=frame_rate_numerator,
            frame_rate_denominator=frame_rate_denominator,
            timecode_mode=timecode_mode,
        ) - parse_timecode(
            start_timecode,
            frame_rate_numerator=frame_rate_numerator,
            frame_rate_denominator=frame_rate_denominator,
            timecode_mode=timecode_mode,
        )
        if frames < 0:
            raise VideoScoringError("video_timecode_invalid")
        return frames
    return math.floor(
        (float(cue.video_seconds) * frame_rate_numerator / frame_rate_denominator) + 1e-9
    )


def verify_cue_landings(
    cues: Sequence[HitPointV1],
    composition: Any,
    *,
    timeline: CompiledTimeline,
    duration_seconds: float,
    frame_rate_numerator: int,
    frame_rate_denominator: int,
    timecode_mode: str,
    start_timecode: str,
    video_origin_seconds: float,
    musical_origin_tick: int,
) -> list[CueLanding]:
    """Compare note attacks with cue frames. Sustain does not widen the window."""
    logger.debug(
        "verify_cue_landings start",
        extra={"cue_count": len(cues), "duration_seconds": duration_seconds},
    )
    if not is_closed_frame_rate(frame_rate_numerator, frame_rate_denominator):
        logger.warning("video_frame_rate_required", extra={"error_code": "video_frame_rate_required"})
        raise VideoScoringError("video_frame_rate_required")
    attacks = _note_attacks(
        composition,
        timeline=timeline,
        duration_seconds=duration_seconds,
        frame_rate_numerator=frame_rate_numerator,
        frame_rate_denominator=frame_rate_denominator,
        video_origin_seconds=video_origin_seconds,
        musical_origin_tick=musical_origin_tick,
    )
    results: list[CueLanding] = []
    for cue in cues:
        landing = _land_one(
            cue,
            attacks,
            frame_rate_numerator=frame_rate_numerator,
            frame_rate_denominator=frame_rate_denominator,
            timecode_mode=timecode_mode,
            start_timecode=start_timecode,
        )
        logger.debug(
            "verify_cue_landings cue",
            extra={
                "cue_id": landing.id,
                "kind": landing.kind,
                "timecode": landing.timecode,
                "tolerance_frames": landing.tolerance_frames,
                "status": landing.status,
                "delta_frames": landing.delta_frames,
            },
        )
        results.append(landing)
    logger.debug(
        "verify_cue_landings finish",
        extra={"cue_count": len(results), "attack_count": len(attacks)},
    )
    return results


def _land_one(
    cue: HitPointV1,
    attacks: Sequence[_Attack],
    *,
    frame_rate_numerator: int,
    frame_rate_denominator: int,
    timecode_mode: str,
    start_timecode: str,
) -> CueLanding:
    cue_frame = cue_frame_index(
        cue,
        frame_rate_numerator=frame_rate_numerator,
        frame_rate_denominator=frame_rate_denominator,
        timecode_mode=timecode_mode,
        start_timecode=start_timecode,
    )
    if not attacks:
        return CueLanding(
            id=cue.id,
            kind=cue.kind,
            timecode=cue.timecode,
            tolerance_frames=cue.tolerance_frames,
            status="empty",
            delta_frames=None,
            match_count=0,
            matches=(),
        )
    closest = min(
        attacks,
        key=lambda attack: (
            abs(attack.event_frame - cue_frame),
            attack.start_tick,
            attack.track_index,
        ),
    )
    matched = [
        attack
        for attack in attacks
        if attack.in_picture and abs(attack.event_frame - cue_frame) <= cue.tolerance_frames
    ]
    matched.sort(key=lambda attack: (abs(attack.event_frame - cue_frame), attack.start_tick, attack.track_index))
    return CueLanding(
        id=cue.id,
        kind=cue.kind,
        timecode=cue.timecode,
        tolerance_frames=cue.tolerance_frames,
        status="landed" if matched else "missed",
        delta_frames=closest.event_frame - cue_frame,
        match_count=len(matched),
        matches=tuple(
            LandingMatch(
                track_index=attack.track_index,
                event_index=attack.event_index,
                start_tick=attack.start_tick,
                pitch=attack.pitch,
            )
            for attack in matched[:MAX_LANDING_MATCHES]
        ),
    )


def _note_attacks(
    composition: Any,
    *,
    timeline: CompiledTimeline,
    duration_seconds: float,
    frame_rate_numerator: int,
    frame_rate_denominator: int,
    video_origin_seconds: float,
    musical_origin_tick: int,
) -> list[_Attack]:
    attacks: list[_Attack] = []
    for track_index, track in enumerate(_child_list(composition, "tracks")):
        for event_index, event in enumerate(_child_list(track, "events")):
            if _field(event, "type") != "note":
                continue
            start_tick = int(_field(event, "start_tick"))
            if start_tick < 0 or start_tick > timeline.duration_ticks:
                continue
            event_video = video_seconds_from_score(
                timeline.tick_to_seconds(start_tick),
                timeline=timeline,
                video_origin_seconds=video_origin_seconds,
                musical_origin_tick=musical_origin_tick,
            )
            event_frame = math.floor(
                (event_video * frame_rate_numerator / frame_rate_denominator) + 1e-9
            )
            attacks.append(
                _Attack(
                    track_index=track_index,
                    event_index=event_index,
                    start_tick=start_tick,
                    pitch=_pitch_number(_field(event, "pitch")),
                    event_video=event_video,
                    event_frame=event_frame,
                    in_picture=0 <= event_video <= float(duration_seconds),
                )
            )
    return attacks


def _pitch_number(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        raise VideoScoringError("video_scoring_invalid")
    if isinstance(value, int):
        return value
    return midi_pitch_number(str(value))


def _child_list(value: Any, name: str) -> list[Any]:
    if isinstance(value, dict):
        child = value.get(name) or []
    else:
        child = getattr(value, name, None) or []
    return list(child)


def _field(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name)
