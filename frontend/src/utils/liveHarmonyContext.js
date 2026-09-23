/**
 * Active harmony context at playhead tick (read-only).
 *
 * Looks up composition.v2 harmony[] spans — metadata only; never invents
 * playable notes from chords. Empty / missing / unparseable → warning code.
 */

import { createAppLogger } from './appLogger.js';
import {
  HARMONY_SHAPE_CANONICAL,
  HARMONY_SHAPE_EMPTY,
  HARMONY_SHAPE_LEGACY,
  classifyHarmonyListShape,
  normalizeCompositionHarmonySpans,
} from './compositionHarmonySpans.js';
import { LIVE_HARMONY_EMPTY } from './liveSessionContracts.js';

const log = createAppLogger('liveTransport');

/** @type {number} */
let emptyWarnCount = 0;

/**
 * @typedef {{ symbol: string|null, start_tick: number|null, duration_ticks: number|null, warning?: string|null }} LiveHarmonyContext
 */

/**
 * @param {object|null|undefined} composition
 * @param {number} tick
 * @returns {LiveHarmonyContext}
 */
export function activeHarmonyAtTick(composition, tick) {
  const safeTick = Math.max(0, Math.round(Number(tick) || 0));
  const harmony = composition?.harmony;
  const shape = classifyHarmonyListShape(harmony);

  if (shape === HARMONY_SHAPE_EMPTY || harmony == null) {
    return emptyResult();
  }

  let spans = [];
  try {
    if (shape === HARMONY_SHAPE_CANONICAL) {
      spans = harmony.map((item) => ({
        start_tick: Number(item.start_tick),
        duration_ticks: Number(item.duration_ticks),
        chord: String(item.chord ?? '').trim(),
      }));
    } else if (shape === HARMONY_SHAPE_LEGACY) {
      const clone = {
        ...composition,
        harmony: Array.isArray(harmony) ? [...harmony] : [],
      };
      const normalized = normalizeCompositionHarmonySpans(clone);
      spans = Array.isArray(normalized?.harmony) ? normalized.harmony : [];
    } else {
      // Mixed / malformed — try normalize on a clone; fall back empty.
      try {
        const clone = {
          ...composition,
          harmony: Array.isArray(harmony) ? [...harmony] : [],
        };
        const normalized = normalizeCompositionHarmonySpans(clone);
        spans = Array.isArray(normalized?.harmony) ? normalized.harmony : [];
      } catch {
        return emptyResult();
      }
    }
  } catch {
    return emptyResult();
  }

  for (const span of spans) {
    const start = Number(span.start_tick);
    const dur = Number(span.duration_ticks);
    if (!Number.isFinite(start) || !Number.isFinite(dur) || dur <= 0) continue;
    if (start <= safeTick && safeTick < start + dur) {
      const symbol = String(span.chord ?? '').trim() || null;
      if (!symbol) {
        return emptyResult();
      }
      return {
        symbol,
        start_tick: Math.round(start),
        duration_ticks: Math.round(dur),
        warning: null,
      };
    }
  }
  return emptyResult();
}

function emptyResult() {
  emptyWarnCount += 1;
  if (emptyWarnCount <= 3 || emptyWarnCount % 32 === 0) {
    log.warn('live_harmony_empty', { count: emptyWarnCount });
  }
  return {
    symbol: null,
    start_tick: null,
    duration_ticks: null,
    warning: LIVE_HARMONY_EMPTY,
  };
}

/** Test helper. */
export function resetLiveHarmonyEmptyWarnCountForTests() {
  emptyWarnCount = 0;
}
