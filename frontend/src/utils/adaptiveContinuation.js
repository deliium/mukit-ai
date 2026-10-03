/** Events that may be placed on the private continuation queue. */

export function continuationEventsToSchedule(snapshot, buffer, positionTick) {
  if (!snapshot || snapshot.audible !== true) {
    return [];
  }
  const kind = snapshot.fallback_kind;
  const allowed = snapshot.source === 'model'
    || kind === 'motif_variation'
    || kind === 'accompaniment';
  if (!allowed || kind === 'reuse_loop') {
    return [];
  }
  const playhead = Number(positionTick);
  const floor = Number.isFinite(playhead) ? playhead : 0;
  const events = Array.isArray(buffer?.events) ? buffer.events : [];
  return events.filter((event) => Number(event?.start_tick) >= floor);
}

/** Body for POST …/continuation. Continuous latches only on start. */
export function buildContinuationStartBody({
  expectedDocumentRevision,
  mode = 'continuation',
  continuous = false,
} = {}) {
  return {
    expected_document_revision: expectedDocumentRevision,
    mode,
    continuous: continuous === true,
  };
}

/**
 * Compact MusicState inspect fields for the Adaptive Continuous subsection.
 * Omits digests and raw rings.
 */
export function summarizeMusicState(musicState) {
  if (!musicState || typeof musicState !== 'object') {
    return null;
  }
  if (musicState.schema_version !== 'adaptive.runtime.music_state.v1') {
    return null;
  }
  const trajectory = Array.isArray(musicState.harmony_trajectory)
    ? musicState.harmony_trajectory
    : [];
  const guards = Array.isArray(musicState.guard_flags) ? musicState.guard_flags : [];
  const themes = Array.isArray(musicState.active_theme_ids)
    ? musicState.active_theme_ids
    : [];
  return {
    virtualBar: Number.isFinite(Number(musicState.virtual_bar))
      ? Number(musicState.virtual_bar)
      : 1,
    themeCount: themes.length,
    trajectoryTail: trajectory.length ? String(trajectory[trajectory.length - 1]) : null,
    guardFlags: guards.map((flag) => String(flag)),
  };
}

/** Stable reason string when the server refuses continuous start. */
export function continuousDisabledReason(errorCode) {
  if (errorCode === 'adaptive_continuous_disabled') {
    return 'adaptive_continuous_disabled';
  }
  return '';
}
