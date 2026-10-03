/**
 * Drop note_performances rows whose event_id was removed (motif-reconcile twin).
 */

import { createAppLogger } from '../appLogger.js';

const log = createAppLogger('midiExpressive');

/**
 * @param {object} composition
 * @param {Iterable<string>|ArrayLike<string>} removedEventIds
 * @returns {{ composition: object, removedPerformanceCount: number }}
 */
export function pruneNotePerformancesForRemovedEventIds(composition, removedEventIds) {
  const removed = new Set(
    [...(removedEventIds || [])].map(String).filter(Boolean),
  );
  if (!composition || !removed.size || !Array.isArray(composition.tracks)) {
    return { composition, removedPerformanceCount: 0 };
  }

  let removedPerformanceCount = 0;
  const tracks = composition.tracks.map((track) => {
    const rows = track.note_performances;
    if (!Array.isArray(rows) || rows.length === 0) {
      return track;
    }
    const next = rows.filter((row) => {
      const id = row && typeof row.event_id === 'string' ? row.event_id : '';
      if (id && removed.has(id)) {
        removedPerformanceCount += 1;
        return false;
      }
      return true;
    });
    if (next.length === rows.length) {
      return track;
    }
    return { ...track, note_performances: next };
  });

  if (removedPerformanceCount > 0) {
    log.debug('Pruned note_performances after event removal', {
      removedPerformanceCount,
      removedEventIdCount: removed.size,
    });
  }

  return {
    composition: removedPerformanceCount ? { ...composition, tracks } : composition,
    removedPerformanceCount,
  };
}
