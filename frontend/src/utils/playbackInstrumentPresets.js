/**
 * Browser playback instrument presets and adapter contract.
 *
 * Sampled and synthesized voices share this adapter surface so the Tone
 * transport/scheduler never depends on a specific sampler library.
 */

import { createAppLogger } from './appLogger.js';
import { selectInstrumentStrategy } from './playbackTracks.js';

const logger = createAppLogger('playbackInstrumentPresets');

/**
 * @typedef {object} PlaybackInstrumentStatus
 * @property {boolean} ready
 * @property {boolean} fallback
 * @property {string} profileId
 * @property {string} [reasonCode]
 */

/**
 * @typedef {object} PlaybackInstrumentAdapter
 * @property {(context: { audioContext?: AudioContext, output?: object }) => Promise<PlaybackInstrumentStatus>} prepare
 * @property {(noteId: string, pitch: string, velocity: number, time?: number) => void} attack
 * @property {(noteId: string, time?: number) => void} release
 * @property {(time?: number) => void} releaseAll
 * @property {(destination: object) => void} connect
 * @property {() => PlaybackInstrumentStatus} getStatus
 * @property {() => void} dispose
 */

export const SYNTH_PRESET_CATALOG = Object.freeze({
  piano_keyboard: Object.freeze({
    id: 'piano_keyboard',
    label: 'Piano / electric keys',
    family: 'keys',
    strategyId: 'piano_keyboard',
  }),
  bass_synth: Object.freeze({
    id: 'bass_synth',
    label: 'Bass',
    family: 'bass',
    strategyId: 'bass',
  }),
  strings_pad: Object.freeze({
    id: 'strings_pad',
    label: 'Strings / pad',
    family: 'strings',
    strategyId: 'strings_pad',
  }),
  guitar_pluck: Object.freeze({
    id: 'guitar_pluck',
    label: 'Guitar / pluck',
    family: 'guitar',
    strategyId: 'guitar_pluck',
  }),
  brass: Object.freeze({
    id: 'brass',
    label: 'Brass',
    family: 'brass',
    strategyId: 'brass',
  }),
  woodwind_lead: Object.freeze({
    id: 'woodwind_lead',
    label: 'Woodwind / lead',
    family: 'lead',
    strategyId: 'lead_synth',
  }),
  mallet: Object.freeze({
    id: 'mallet',
    label: 'Mallet',
    family: 'mallet',
    strategyId: 'mallet',
  }),
  drums_basic: Object.freeze({
    id: 'drums_basic',
    label: 'Drums (basic GM groups)',
    family: 'drums',
    strategyId: 'drums',
  }),
});

/**
 * Resolve the deterministic synth fallback preset for a track.
 * @param {object} track
 * @returns {{ presetId: string, strategy: object, reason: string }}
 */
export function resolveSynthFallbackPreset(track) {
  const strategy = selectInstrumentStrategy(track);
  const presetId = strategy.presetId && SYNTH_PRESET_CATALOG[strategy.presetId]
    ? strategy.presetId
    : 'piano_keyboard';
  const reason = strategy.reason || 'strategy_mapping';
  logger.debug('Resolved synth fallback preset', {
    trackId: track?.id,
    presetId,
    strategy: strategy.id,
    reason,
  });
  return { presetId, strategy, reason };
}

/**
 * @param {string} presetId
 * @returns {object|null}
 */
export function getSynthPreset(presetId) {
  const id = String(presetId || '');
  return SYNTH_PRESET_CATALOG[id] || null;
}

/**
 * Create a no-op adapter shell used by tests and as a contract reference.
 * Real Tone / sampler adapters implement the same methods.
 *
 * @param {{ profileId: string, fallback?: boolean }} options
 * @returns {PlaybackInstrumentAdapter}
 */
export function createInstrumentAdapterStub(options = {}) {
  const profileId = String(options.profileId || 'piano_keyboard');
  let ready = false;
  let disposed = false;
  let destination = null;
  const activeNotes = new Set();

  const getStatus = () => ({
    ready: ready && !disposed,
    fallback: Boolean(options.fallback),
    profileId,
    reasonCode: disposed ? 'disposed' : (ready ? 'ready' : 'not_prepared'),
  });

  return {
    async prepare() {
      if (disposed) {
        logger.warn('Prepare called on disposed adapter', { profileId });
        return getStatus();
      }
      ready = true;
      logger.debug('Adapter prepared', { profileId, fallback: Boolean(options.fallback) });
      return getStatus();
    },
    attack(noteId, pitch, velocity) {
      if (!ready || disposed) {
        return;
      }
      const id = String(noteId || '');
      if (!id) {
        return;
      }
      activeNotes.add(id);
      logger.debug('Adapter attack', {
        profileId,
        noteId: id,
        hasPitch: Boolean(pitch),
        velocity: Number.isFinite(Number(velocity)) ? Number(velocity) : null,
      });
    },
    release(noteId) {
      const id = String(noteId || '');
      activeNotes.delete(id);
    },
    releaseAll() {
      activeNotes.clear();
    },
    connect(nextDestination) {
      destination = nextDestination || null;
      logger.debug('Adapter connected', { profileId, hasDestination: Boolean(destination) });
    },
    getStatus,
    dispose() {
      activeNotes.clear();
      ready = false;
      disposed = true;
      destination = null;
      logger.debug('Adapter disposed', { profileId });
    },
  };
}

/**
 * Assert an object implements the adapter contract (structural check).
 * @param {unknown} adapter
 * @returns {{ ok: boolean, missing: string[] }}
 */
export function validateInstrumentAdapter(adapter) {
  const required = ['prepare', 'attack', 'release', 'releaseAll', 'connect', 'getStatus', 'dispose'];
  const missing = required.filter((name) => typeof adapter?.[name] !== 'function');
  if (missing.length) {
    logger.warn('Instrument adapter missing methods', { missing });
  }
  return { ok: missing.length === 0, missing };
}
