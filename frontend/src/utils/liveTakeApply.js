/**
 * Commit co-performance ephemeral stream/accompaniment into composition.v2.
 * One transaction; never invents notes from harmony alone.
 */

import { createAppLogger } from './appLogger.js';
import { applyMidiTakeToComposition } from './midiTakeApply.js';
import { midiToPitch } from './pianoRollEvents.js';

const log = createAppLogger('liveAccompaniment');

/**
 * @param {Array<{ midi?: number, pitch?: number|string, start_tick: number, duration_ticks: number, velocity?: number, channel?: number }>} notes
 */
export function normalizeLiveNotesForCommit(notes) {
  const out = [];
  for (const note of notes || []) {
    if (typeof note.pitch === 'string' && note.pitch.length > 0 && Number.isNaN(Number(note.pitch))) {
      out.push({
        pitch: note.pitch,
        midi: Number.isFinite(Number(note.midi)) ? Math.round(Number(note.midi)) : undefined,
        channel: Number(note.channel) || 0,
        start_tick: Math.max(0, Math.round(Number(note.start_tick) || 0)),
        duration_ticks: Math.max(1, Math.round(Number(note.duration_ticks) || 1)),
        velocity: Math.max(1, Math.min(127, Math.round(Number(note.velocity) || 80))),
      });
      continue;
    }
    const midi = Number(note.midi ?? note.pitch);
    if (!Number.isFinite(midi) || midi < 0 || midi > 127) continue;
    const { pitch } = midiToPitch(Math.round(midi));
    if (!pitch) continue;
    out.push({
      pitch,
      midi: Math.round(midi),
      channel: Number(note.channel) || 0,
      start_tick: Math.max(0, Math.round(Number(note.start_tick) || 0)),
      duration_ticks: Math.max(1, Math.round(Number(note.duration_ticks) || 1)),
      velocity: Math.max(1, Math.min(127, Math.round(Number(note.velocity) || 80))),
    });
  }
  return out;
}

/**
 * @param {object|null} composition
 * @param {{
 *   trackId: string,
 *   streamNotes?: array,
 *   accompanimentEvents?: array,
 *   lockedTrackIds?: iterable,
 * }} opts
 */
export function applyCoPerformanceTakeToComposition(composition, opts = {}) {
  const streamNotes = normalizeLiveNotesForCommit(opts.streamNotes || []);
  const accomp = normalizeLiveNotesForCommit(
    (opts.accompanimentEvents || []).map((ev) => ({
      midi: ev.pitch,
      start_tick: ev.start_tick,
      duration_ticks: ev.duration_ticks,
      velocity: ev.velocity,
    })),
  );
  const notes = [...streamNotes, ...accomp];
  log.info('co-performance commit prepare', {
    streamCount: streamNotes.length,
    accompCount: accomp.length,
    trackId: opts.trackId ? String(opts.trackId).slice(0, 24) : null,
  });
  return applyMidiTakeToComposition(composition, {
    trackId: opts.trackId,
    notes,
    sustainPedals: [],
    lockedTrackIds: opts.lockedTrackIds,
  });
}
