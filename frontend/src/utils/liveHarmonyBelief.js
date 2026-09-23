/**
 * Harmony belief with confidence + hysteresis (warm path).
 *
 * Prefer V2 span → else features.probable_harmony → dwell/hysteresis gates.
 * Ambiguous single-note guesses hold the last belief.
 *
 * Session snapshot / musicStore warm-path pump wiring belongs in later jam tasks.
 * Do not nest belief inside live.performance.features.v1.
 */

import { createAppLogger } from './appLogger.js';
import { parseLiveChordSymbol } from './liveChordTones.js';
import {
  JAM_HARMONY_CHANGED,
  JAM_HARMONY_HOLD,
  createIdleHarmonyBelief,
  readLiveJamSettings,
  scaleHysteresisForResponsiveness,
} from './liveJamContracts.js';

const log = createAppLogger('liveJam');

/** @type {number} */
let holdCount = 0;

/**
 * @typedef {{
 *   symbol: string|null,
 *   confidence: number,
 *   held: boolean,
 *   reason_code: string|null,
 *   pending_symbol?: string|null,
 *   pending_confidence?: number,
 *   pending_since_ms?: number|null,
 *   belief_since_ms?: number|null,
 * }} HarmonyBeliefState
 */

/**
 * Update smoothed harmony belief from features + optional V2 span.
 *
 * Extra pending_* / belief_since_ms fields persist dwell state across calls —
 * pass the previous return value as `prev`.
 *
 * @param {HarmonyBeliefState|null|undefined} prev
 * @param {object|null|undefined} features live.performance.features.v1
 * @param {{ symbol?: string|null }|string|null|undefined} v2HarmonyAtTick
 * @param {'low'|'medium'|'high'|string} [responsiveness]
 * @param {ReturnType<typeof readLiveJamSettings>} [settings]
 * @param {{ nowMs?: number }} [options] inject clock for tests
 * @returns {HarmonyBeliefState}
 */
export function updateHarmonyBelief(
  prev,
  features,
  v2HarmonyAtTick,
  responsiveness = 'medium',
  settings = readLiveJamSettings(),
  options = {},
) {
  const nowMs = Number.isFinite(Number(options.nowMs))
    ? Number(options.nowMs)
    : (
      typeof performance !== 'undefined' && typeof performance.now === 'function'
        ? performance.now()
        : Date.now()
    );
  const scaled = scaleHysteresisForResponsiveness(
    /** @type {'low'|'medium'|'high'} */ (responsiveness),
    settings,
  );
  const previous = normalizePrev(prev);
  const v2Symbol = extractV2Symbol(v2HarmonyAtTick);

  // 1. Prefer parseable V2 span (high confidence).
  if (v2Symbol) {
    const parsed = parseLiveChordSymbol(v2Symbol);
    if (parsed.parseable) {
      return adoptOrHoldV2(previous, v2Symbol, nowMs);
    }
  }

  const probable = features?.probable_harmony && typeof features.probable_harmony === 'object'
    ? features.probable_harmony
    : {};
  const candidateSymbol = typeof probable.symbol === 'string' && probable.symbol.trim()
    ? String(probable.symbol).trim().slice(0, 64)
    : null;
  const candidateConf = clamp01(Number(probable.confidence));
  const candidateParsed = candidateSymbol
    ? parseLiveChordSymbol(candidateSymbol)
    : { parseable: false };

  // 4. Ambiguous / unparseable → hold last belief.
  if (!candidateSymbol || !candidateParsed.parseable || candidateConf < 0.35) {
    return holdBelief(previous, nowMs, 'ambiguous');
  }

  // Same as current → clear pending, keep belief.
  if (symbolsEqual(previous.symbol, candidateSymbol)) {
    return {
      ...previous,
      confidence: Math.max(previous.confidence, candidateConf),
      held: false,
      reason_code: null,
      pending_symbol: null,
      pending_confidence: 0,
      pending_since_ms: null,
      belief_since_ms: previous.belief_since_ms ?? nowMs,
    };
  }

  // No prior belief → adopt if above confidence min (first fill).
  if (!previous.symbol) {
    if (candidateConf >= scaled.confidenceMin) {
      return changeBelief(previous, candidateSymbol, candidateConf, nowMs);
    }
    return holdBelief(previous, nowMs, 'below_threshold');
  }

  // 3. Change only when confidence ≥ threshold AND dwell ≥ scaled dwell
  //    AND new conf ≥ current + hysteresis.
  const meetsConf = candidateConf + 1e-9 >= scaled.confidenceMin;
  const margin = candidateConf - previous.confidence;
  const meetsHysteresis = margin + 1e-9 >= scaled.hysteresis;
  if (!meetsConf || !meetsHysteresis) {
    return holdBelief(previous, nowMs, 'below_threshold');
  }

  let pendingSince = previous.pending_since_ms;
  let pendingSymbol = previous.pending_symbol;
  let pendingConf = previous.pending_confidence ?? 0;

  if (!symbolsEqual(pendingSymbol, candidateSymbol)) {
    pendingSymbol = candidateSymbol;
    pendingConf = candidateConf;
    pendingSince = nowMs;
    log.debug('harmony candidate pending', {
      code: JAM_HARMONY_HOLD,
      pending: candidateSymbol.slice(0, 16),
      conf: Number(candidateConf.toFixed(3)),
    });
  } else {
    pendingConf = Math.max(pendingConf, candidateConf);
  }

  const dwellElapsed = pendingSince != null
    ? nowMs - pendingSince
    : 0;

  if (dwellElapsed < scaled.dwellMs) {
    holdCount += 1;
    if (holdCount <= 3 || holdCount % 32 === 0) {
      log.debug('harmony hold dwell', {
        code: JAM_HARMONY_HOLD,
        count: holdCount,
        dwell_ms: Math.round(dwellElapsed),
        need_ms: scaled.dwellMs,
      });
    }
    return {
      ...previous,
      held: true,
      reason_code: JAM_HARMONY_HOLD,
      pending_symbol: pendingSymbol,
      pending_confidence: pendingConf,
      pending_since_ms: pendingSince,
    };
  }

  return changeBelief(previous, candidateSymbol, candidateConf, nowMs);
}

/**
 * @param {HarmonyBeliefState} previous
 * @param {string} symbol
 * @param {number} nowMs
 */
function adoptOrHoldV2(previous, symbol, nowMs) {
  if (symbolsEqual(previous.symbol, symbol)) {
    return {
      ...previous,
      confidence: Math.max(previous.confidence, 0.95),
      held: false,
      reason_code: null,
      pending_symbol: null,
      pending_confidence: 0,
      pending_since_ms: null,
      belief_since_ms: previous.belief_since_ms ?? nowMs,
    };
  }
  log.info('harmony belief changed', {
    code: JAM_HARMONY_CHANGED,
    from: previous.symbol ? String(previous.symbol).slice(0, 16) : null,
    to: symbol.slice(0, 16),
    source: 'v2',
  });
  return {
    symbol,
    confidence: 0.95,
    held: false,
    reason_code: JAM_HARMONY_CHANGED,
    pending_symbol: null,
    pending_confidence: 0,
    pending_since_ms: null,
    belief_since_ms: nowMs,
  };
}

/**
 * @param {HarmonyBeliefState} previous
 * @param {string} symbol
 * @param {number} confidence
 * @param {number} nowMs
 */
function changeBelief(previous, symbol, confidence, nowMs) {
  log.info('harmony belief changed', {
    code: JAM_HARMONY_CHANGED,
    from: previous.symbol ? String(previous.symbol).slice(0, 16) : null,
    to: symbol.slice(0, 16),
  });
  return {
    symbol,
    confidence: clamp01(confidence),
    held: false,
    reason_code: JAM_HARMONY_CHANGED,
    pending_symbol: null,
    pending_confidence: 0,
    pending_since_ms: null,
    belief_since_ms: nowMs,
  };
}

/**
 * @param {HarmonyBeliefState} previous
 * @param {number} nowMs
 * @param {string} why
 */
function holdBelief(previous, nowMs, why) {
  holdCount += 1;
  if (holdCount <= 3 || holdCount % 32 === 0) {
    log.debug('harmony hold', {
      code: JAM_HARMONY_HOLD,
      count: holdCount,
      why,
      symbol: previous.symbol ? String(previous.symbol).slice(0, 16) : null,
    });
  }
  return {
    ...previous,
    held: true,
    reason_code: JAM_HARMONY_HOLD,
    belief_since_ms: previous.belief_since_ms ?? nowMs,
  };
}

function normalizePrev(prev) {
  if (!prev || typeof prev !== 'object') {
    return {
      ...createIdleHarmonyBelief(),
      pending_symbol: null,
      pending_confidence: 0,
      pending_since_ms: null,
      belief_since_ms: null,
    };
  }
  const base = createIdleHarmonyBelief({
    symbol: typeof prev.symbol === 'string' ? prev.symbol : null,
    confidence: prev.confidence,
    held: prev.held,
    reason_code: typeof prev.reason_code === 'string' ? prev.reason_code : null,
  });
  return {
    ...base,
    pending_symbol: typeof prev.pending_symbol === 'string' ? prev.pending_symbol : null,
    pending_confidence: clamp01(Number(prev.pending_confidence) || 0),
    pending_since_ms: Number.isFinite(Number(prev.pending_since_ms))
      ? Number(prev.pending_since_ms)
      : null,
    belief_since_ms: Number.isFinite(Number(prev.belief_since_ms))
      ? Number(prev.belief_since_ms)
      : null,
  };
}

function extractV2Symbol(v2HarmonyAtTick) {
  if (typeof v2HarmonyAtTick === 'string') {
    const s = v2HarmonyAtTick.trim();
    return s || null;
  }
  if (v2HarmonyAtTick && typeof v2HarmonyAtTick === 'object') {
    const s = String(v2HarmonyAtTick.symbol || '').trim();
    return s || null;
  }
  return null;
}

function symbolsEqual(a, b) {
  if (a == null && b == null) return true;
  if (a == null || b == null) return false;
  return String(a).trim().toLowerCase() === String(b).trim().toLowerCase();
}

function clamp01(n) {
  if (!Number.isFinite(n)) return 0;
  return Math.max(0, Math.min(1, n));
}

/** Aggregate hold count for tests / badges. */
export function getHarmonyHoldCount() {
  return holdCount;
}

/** Test helper. */
export function resetHarmonyBeliefCountersForTests() {
  holdCount = 0;
}
