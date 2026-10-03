/**
 * Decode neural stem WAVs and schedule on shared Tone.Transport origin.
 * Does not rewrite stem bytes or composition.v2. Teardown on clear.
 */

import { createAppLogger } from '../appLogger.js';

const logger = createAppLogger('spatialMusic.stems');

/**
 * @typedef {{
 *   stemId: string,
 *   buffer: AudioBuffer,
 *   player: object|null,
 *   extraNodes: object[],
 * }} StemPlayerEntry
 */

/** @type {Map<string, StemPlayerEntry>} */
const activePlayers = new Map();

/**
 * Fetch stem audio, decode, and prepare a Tone.Player connected to a destination.
 * Schedules at Transport origin (start 0); does not mutate Transport.bpm or note ticks.
 * @param {object} opts
 * @param {typeof import('tone')} opts.Tone
 * @param {string} [opts.url] fetch URL when using default fetcher
 * @param {string} opts.stemId
 * @param {object} opts.destination Tone node
 * @param {object[]} [opts.extraNodes] spatial chain nodes disposed on teardown
 * @param {(url: string) => Promise<ArrayBuffer>} [opts.fetchArrayBuffer]
 */
export async function prepareStemSpatialPlayer({
  Tone,
  url,
  stemId,
  destination,
  extraNodes = [],
  fetchArrayBuffer,
}) {
  if (!Tone || !stemId || !destination) {
    return { ok: false, reason: 'missing_args' };
  }
  if (!fetchArrayBuffer && !url) {
    return { ok: false, reason: 'missing_args' };
  }
  teardownStemSpatialPlayer(stemId);
  try {
    const fetcher = fetchArrayBuffer || (async (u) => {
      const res = await fetch(u);
      if (!res.ok) throw new Error(`stem_fetch_${res.status}`);
      return res.arrayBuffer();
    });
    const bytes = await fetcher(url || `stem:${stemId}`);
    const audioCtx = Tone.getContext?.()?.rawContext || Tone.context?.rawContext;
    if (!audioCtx || typeof audioCtx.decodeAudioData !== 'function') {
      return { ok: false, reason: 'decode_unavailable' };
    }
    const buffer = await audioCtx.decodeAudioData(bytes.slice(0));
    const player = new Tone.Player(buffer).connect(destination);
    player.sync().start(0);
    activePlayers.set(String(stemId), {
      stemId: String(stemId),
      buffer,
      player,
      extraNodes: Array.isArray(extraNodes) ? [...extraNodes] : [],
    });
    logger.info('Prepared stem spatial player', {
      stemId,
      durationSec: buffer.duration,
      extraNodeCount: Array.isArray(extraNodes) ? extraNodes.length : 0,
    });
    return { ok: true, reason: 'scheduled', stemId, durationSec: buffer.duration };
  } catch (error) {
    logger.warn('Stem spatial prepare failed', {
      stemId,
      message: error?.message || String(error),
    });
    return { ok: false, reason: 'stem_prepare_failed', message: error?.message };
  }
}

/**
 * @param {string} stemId
 */
export function teardownStemSpatialPlayer(stemId) {
  const entry = activePlayers.get(String(stemId));
  if (!entry) return;
  try {
    entry.player?.unsync?.();
    entry.player?.stop?.();
    entry.player?.dispose?.();
  } catch {
    // ignore dispose races
  }
  for (const node of entry.extraNodes || []) {
    try {
      node?.disconnect?.();
      node?.dispose?.();
    } catch {
      // ignore dispose races
    }
  }
  activePlayers.delete(String(stemId));
  logger.debug('Teardown stem spatial player', { stemId });
}

export function teardownAllStemSpatialPlayers() {
  for (const stemId of [...activePlayers.keys()]) {
    teardownStemSpatialPlayer(stemId);
  }
  logger.debug('Teardown all stem spatial players', { cleared: true });
}

export function listActiveStemSpatialPlayers() {
  return [...activePlayers.keys()];
}
