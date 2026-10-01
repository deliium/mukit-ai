/** Display helpers for film.score.adaptation.v1. They do not choose a strategy. */

export function cueSnapshotFromHit(hit) {
  return {
    id: hit.id,
    kind: hit.kind || 'hit_point',
    importance: hit.importance || 'medium',
    video_seconds: hit.video_seconds,
    tolerance_frames: hit.tolerance_frames ?? 0,
  };
}

/** Duration comes from the asset. Frame rate, origin, and hits come from scoring. */
export function baselineFromPicture(asset, scoring) {
  if (!asset || scoring == null || asset.duration_seconds == null) {
    return null;
  }
  return {
    duration_seconds: asset.duration_seconds,
    frame_rate_numerator: scoring.frame_rate_numerator,
    frame_rate_denominator: scoring.frame_rate_denominator,
    video_origin_seconds: scoring.video_origin_seconds ?? 0,
    musical_origin_tick: scoring.musical_origin_tick ?? 0,
    cues: (scoring.hit_points || []).map(cueSnapshotFromHit),
  };
}

export function operationLine(operation) {
  if (!operation) {
    return '';
  }
  return `${operation.op_id} ${operation.strategy} bars ${operation.start_bar}-${operation.end_bar} delta ${operation.bars_delta}`;
}

export function countLine(counts) {
  if (!counts) {
    return '';
  }
  return `unchanged ${counts.events_unchanged} shifted ${counts.events_shifted} removed ${counts.events_removed} added ${counts.events_added}`;
}

export function hitLine(hit) {
  if (!hit) {
    return '';
  }
  return `${hit.cue_id} ${hit.change} ${hit.status}`;
}

export function canCommitFilmAdapt(preview) {
  return Boolean(preview?.candidate_fingerprint);
}

export function editOpId(index) {
  const hex = (index + 1).toString(16).padStart(8, '0');
  return `edit_${hex}`;
}
