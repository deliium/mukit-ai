"""Pure deterministic spatial compiler: scene → spatial.preview.v1.

No FastAPI, SQLite, LLM, or PCM imports. FOA ACN/SN3D + equal-power stereo
from Task 1 formulas. Motion samples SpatialMix position only — never musical time.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Sequence

from app.spatial_constants import (
    ENGINE_VERSION,
    foa_acn_sn3d,
    stereo_equal_power_gains,
)
from app.spatial_schemas import (
    SpatialFoaCoeffsV1,
    SpatialMixSourceV1,
    SpatialMotionKeyframeV1,
    SpatialPreviewMetricsV1,
    SpatialPreviewSourceV1,
    SpatialPreviewStaleV1,
    SpatialPreviewV1,
    SpatialSceneV1,
    SpatialStereoCoeffsV1,
)

logger = logging.getLogger(__name__)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _sample_motion(
    source: SpatialMixSourceV1,
    *,
    at_tick: int,
    at_seconds: float | None,
) -> tuple[float, float, float, float, bool]:
    """Return (azimuth, elevation, distance, spread, motion_used).

    Primary sample key is tick. ``at_seconds`` only when tick sampling not used
    for keyframes that lack tick (time_seconds-only keyframes).
    """
    motion = list(source.motion or [])
    if not motion:
        return (
            float(source.azimuth_deg),
            float(source.elevation_deg),
            float(source.distance),
            float(source.spread),
            False,
        )

    # Prefer tick-keyed frames when any frame has tick.
    tick_frames = [kf for kf in motion if kf.tick is not None]
    if tick_frames:
        ordered = sorted(tick_frames, key=lambda kf: int(kf.tick or 0))
        t = int(at_tick)
        return _interp_keyframes(ordered, t, key="tick") + (True,)

    # Fallback: time_seconds-only frames when at_seconds provided (or 0).
    sec_frames = [kf for kf in motion if kf.time_seconds is not None]
    if not sec_frames:
        return (
            float(source.azimuth_deg),
            float(source.elevation_deg),
            float(source.distance),
            float(source.spread),
            False,
        )
    ordered = sorted(sec_frames, key=lambda kf: float(kf.time_seconds or 0.0))
    t = float(at_seconds if at_seconds is not None else 0.0)
    return _interp_keyframes(ordered, t, key="time_seconds") + (True,)


def _interp_keyframes(
    ordered: Sequence[SpatialMotionKeyframeV1],
    t: float | int,
    *,
    key: str,
) -> tuple[float, float, float, float]:
    def key_of(kf: SpatialMotionKeyframeV1) -> float:
        if key == "tick":
            return float(kf.tick or 0)
        return float(kf.time_seconds or 0.0)

    if t <= key_of(ordered[0]):
        kf = ordered[0]
        return (
            float(kf.azimuth_deg),
            float(kf.elevation_deg),
            float(kf.distance),
            float(kf.spread),
        )
    if t >= key_of(ordered[-1]):
        kf = ordered[-1]
        return (
            float(kf.azimuth_deg),
            float(kf.elevation_deg),
            float(kf.distance),
            float(kf.spread),
        )
    for i in range(len(ordered) - 1):
        a = ordered[i]
        b = ordered[i + 1]
        ka = key_of(a)
        kb = key_of(b)
        if ka <= t <= kb:
            span = kb - ka
            frac = 0.0 if span <= 0 else (float(t) - ka) / span
            return (
                _lerp(float(a.azimuth_deg), float(b.azimuth_deg), frac),
                _lerp(float(a.elevation_deg), float(b.elevation_deg), frac),
                _lerp(float(a.distance), float(b.distance), frac),
                _lerp(float(a.spread), float(b.spread), frac),
            )
    kf = ordered[-1]
    return (
        float(kf.azimuth_deg),
        float(kf.elevation_deg),
        float(kf.distance),
        float(kf.spread),
    )


def _compile_source(
    source: SpatialMixSourceV1,
    *,
    at_tick: int,
    at_seconds: float | None,
    track_ids: set[str] | None,
    stem_ids: set[str] | None,
) -> tuple[SpatialPreviewSourceV1, float, bool]:
    """Compile one source. Returns (preview_source, distance_used, motion_used)."""
    az, el, dist, spread, motion_used = _sample_motion(
        source, at_tick=at_tick, at_seconds=at_seconds
    )
    skipped = False
    skip_reason: str | None = None

    if source.source_kind == "track" and track_ids is not None:
        if not source.track_id or source.track_id not in track_ids:
            skipped = True
            skip_reason = "source_unresolved"
    if source.source_kind == "stem" and stem_ids is not None:
        if not source.stem_id or source.stem_id not in stem_ids:
            skipped = True
            skip_reason = "source_unresolved"

    if source.muted:
        left, right, d_gain = 0.0, 0.0, 0.0
        w = y = z = x = 0.0
    else:
        left, right, d_gain = stereo_equal_power_gains(
            az, el, distance=dist, spread=spread, gain=float(source.gain)
        )
        w, y, z, x, _ = foa_acn_sn3d(
            az, el, distance=dist, spread=spread, gain=float(source.gain)
        )

    preview = SpatialPreviewSourceV1(
        source_id=source.id,
        source_kind=source.source_kind,
        track_id=source.track_id,
        stem_id=source.stem_id,
        stereo=SpatialStereoCoeffsV1(left_gain=left, right_gain=right),
        foa=SpatialFoaCoeffsV1(w=w, y=y, z=z, x=x),
        distance_gain=d_gain,
        spread=spread,
        muted=bool(source.muted),
        skipped=skipped,
        skip_reason=skip_reason,
    )
    return preview, dist, motion_used


def compile_spatial_preview(
    scene: SpatialSceneV1,
    *,
    scene_revision: int = 1,
    at_tick: int = 0,
    at_seconds: float | None = None,
    track_ids: set[str] | None = None,
    stem_ids: set[str] | None = None,
    stale_composition: bool = False,
    stale_stem_set: bool = False,
    request_composition_fingerprint: str | None = None,
    request_stem_set_fingerprint: str | None = None,
) -> SpatialPreviewV1:
    """Deterministic compile. Seed-free. Motion primary key = at_tick."""
    sources_out: list[SpatialPreviewSourceV1] = []
    distances: list[float] = []
    left_sum = 0.0
    right_sum = 0.0
    foa_energy = 0.0
    track_count = 0
    stem_count = 0
    skipped_count = 0
    any_motion = False
    warnings: list[str] = []

    for source in scene.sources:
        if source.source_kind == "track":
            track_count += 1
        else:
            stem_count += 1
        preview_src, dist, motion_used = _compile_source(
            source,
            at_tick=at_tick,
            at_seconds=at_seconds,
            track_ids=track_ids,
            stem_ids=stem_ids,
        )
        if motion_used:
            any_motion = True
        if preview_src.skipped:
            skipped_count += 1
            warnings.append(
                f"{preview_src.skip_reason}:{preview_src.source_id}"
            )
        else:
            distances.append(dist)
            left_sum += abs(preview_src.stereo.left_gain)
            right_sum += abs(preview_src.stereo.right_gain)
            foa = preview_src.foa
            foa_energy += math.sqrt(
                foa.w * foa.w + foa.y * foa.y + foa.z * foa.z + foa.x * foa.x
            )
        sources_out.append(preview_src)

    total_lr = left_sum + right_sum
    imbalance = 0.0 if total_lr <= 0 else abs(left_sum - right_sum) / total_lr
    mean_distance = (
        sum(distances) / float(len(distances)) if distances else 0.0
    )

    scene_id = scene.id or "sscene_unpersisted"
    metrics = SpatialPreviewMetricsV1(
        source_count=len(scene.sources),
        track_count=track_count,
        stem_count=stem_count,
        skipped_count=skipped_count,
        mean_distance=mean_distance,
        stereo_imbalance=imbalance,
        foa_energy=foa_energy,
        motion_sampled=any_motion,
        sample_tick=int(at_tick),
    )

    logger.debug(
        "Compiled spatial preview",
        extra={
            "scene_id": scene_id,
            "source_count": metrics.source_count,
            "track_count": track_count,
            "stem_count": stem_count,
            "skipped_count": skipped_count,
            "sample_tick": int(at_tick),
            "engine_version": ENGINE_VERSION,
        },
    )

    return SpatialPreviewV1(
        schema_version="spatial.preview.v1",
        scene_id=scene_id,
        scene_revision=int(scene_revision),
        engine_version=ENGINE_VERSION,
        source_composition_fingerprint=(
            request_composition_fingerprint
            or scene.source_composition_fingerprint
        ),
        source_stem_set_fingerprint=(
            request_stem_set_fingerprint or scene.source_stem_set_fingerprint
        ),
        stale=SpatialPreviewStaleV1(
            composition=bool(stale_composition),
            stem_set=bool(stale_stem_set),
        ),
        listener=scene.listener,
        sources=sources_out,
        metrics=metrics,
        warnings=warnings,
    )


def preview_metric_digest(preview: SpatialPreviewV1) -> str:
    """Stable digest for distinguishability asserts (metrics only)."""
    from app.spatial_schemas import metrics_digest

    return metrics_digest(preview.metrics)


def azimuth_xyz_unit(azimuth_deg: float, elevation_deg: float = 0.0) -> tuple[float, float, float]:
    """Cartesian unit vector for tests (right-handed, +Z front, +Y up, +X right).

    With +azimuth = left (CCW): x = -sin(az)·cos(el), y = sin(el), z = cos(az)·cos(el).
    """
    az = math.radians(float(azimuth_deg))
    el = math.radians(float(elevation_deg))
    cos_el = math.cos(el)
    return (-math.sin(az) * cos_el, math.sin(el), math.cos(az) * cos_el)
