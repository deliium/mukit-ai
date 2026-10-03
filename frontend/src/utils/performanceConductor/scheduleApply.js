/**
 * Pure schedule-apply helper: merge realization deltas into a playback clone.
 * Never mutates the source composition. Not a conductor twin.
 */

import { createAppLogger } from '../appLogger.js';

const logger = createAppLogger('performanceScheduleApply');

/**
 * @param {object|null|undefined} composition
 * @param {object|null|undefined} realization
 * @returns {object|null}
 */
export function applyRealizationToScheduleComposition(composition, realization) {
  if (!composition || typeof composition !== 'object') {
    return composition ?? null;
  }
  if (!realization || !Array.isArray(realization.notes)) {
    return composition;
  }

  const byEvent = new Map();
  for (const note of realization.notes) {
    if (!note || typeof note.event_id !== 'string') continue;
    byEvent.set(`${note.track_id || ''}::${note.event_id}`, note);
  }

  const gainByTrack = new Map();
  for (const row of realization.track_gains || []) {
    if (row && typeof row.track_id === 'string' && typeof row.gain === 'number') {
      gainByTrack.set(row.track_id, row.gain);
    }
  }

  const spansByTrack = new Map();
  for (const span of realization.sustain_spans || []) {
    if (!span || typeof span.track_id !== 'string') continue;
    const list = spansByTrack.get(span.track_id) || [];
    list.push({
      start_tick: Math.max(0, Math.floor(span.start_tick)),
      duration_ticks: Math.max(1, Math.floor(span.end_tick - span.start_tick)),
    });
    spansByTrack.set(span.track_id, list);
  }

  let applied = 0;
  const tracks = (composition.tracks || []).map((track) => {
    const events = (track.events || []).map((event) => {
      const delta = byEvent.get(`${track.id}::${event.id}`);
      if (!delta) return event;
      applied += 1;
      const start = Math.max(0, Math.floor((event.start_tick || 0) + (delta.tick_delta || 0)));
      const duration = Math.max(
        1,
        Math.floor((event.duration_ticks || 1) + (delta.duration_delta || 0)),
      );
      const velocity = Math.max(1, Math.min(127, Math.floor(delta.velocity ?? event.velocity)));
      return {
        ...event,
        start_tick: start,
        duration_ticks: duration,
        velocity,
        // Pitch / harmony / id unchanged.
      };
    });

    const next = { ...track, events };
    if (gainByTrack.has(track.id)) {
      // Map session gain 0..1 → approximate MIDI volume 0..127 for audition.
      next.volume = Math.max(0, Math.min(127, Math.round(gainByTrack.get(track.id) * 127)));
    }
    if (spansByTrack.has(track.id)) {
      next.sustain_pedals = spansByTrack.get(track.id);
    }
    return next;
  });

  logger.debug('Applied realization to schedule composition', {
    appliedNoteCount: applied,
    trackGainCount: gainByTrack.size,
    sustainTrackCount: spansByTrack.size,
  });

  return {
    ...composition,
    tracks,
  };
}
