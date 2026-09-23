/**
 * Session-scoped shared playback engine handle for co-performance.
 *
 * PlaybackControls registers the single createPlaybackEngine instance.
 * Live scheduler must attach here — never spawn a second Transport owner.
 */

import { createAppLogger } from './appLogger.js';
import { LIVE_ENGINE_UNAVAILABLE } from './liveSessionContracts.js';

const log = createAppLogger('liveTransport');

/** @type {ReturnType<import('./tonePlaybackEngine.js').createPlaybackEngine> | null} */
let sharedEngine = null;

/** @type {Set<(event: { type: string, reason?: string }) => void>} */
const listeners = new Set();

/**
 * @param {object|null|undefined} engine
 */
export function registerLivePlaybackEngine(engine) {
  sharedEngine = engine || null;
  log.info('engine attach', {
    hasEngine: Boolean(sharedEngine),
    sessionId: sharedEngine?.getSessionId?.() ?? null,
  });
  notify({ type: 'attach' });
}

/**
 * @param {{ reason?: string, clearLive?: boolean }} [opts]
 */
export function clearLivePlaybackEngine({ reason = 'dispose', clearLive = true } = {}) {
  if (sharedEngine && clearLive && typeof sharedEngine.clearLiveScheduledEvents === 'function') {
    const cleared = sharedEngine.clearLiveScheduledEvents();
    log.debug('owned live ID clear', { reason, clearedCount: cleared });
  }
  sharedEngine = null;
  log.info('engine detach', { reason });
  notify({ type: 'detach', reason });
}

export function getLivePlaybackEngine() {
  return sharedEngine;
}

export function requireLivePlaybackEngine() {
  if (!sharedEngine) {
    log.warn('start without engine', { code: LIVE_ENGINE_UNAVAILABLE });
    return { ok: false, code: LIVE_ENGINE_UNAVAILABLE, engine: null };
  }
  return { ok: true, engine: sharedEngine };
}

/**
 * @param {(event: { type: string, reason?: string }) => void} listener
 * @returns {() => void}
 */
export function subscribeLivePlaybackEngine(listener) {
  if (typeof listener !== 'function') {
    return () => {};
  }
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function notify(event) {
  for (const listener of listeners) {
    try {
      listener(event);
    } catch (error) {
      log.warn('engine listener error', {
        message: error instanceof Error ? error.message : 'unknown',
      });
    }
  }
}
