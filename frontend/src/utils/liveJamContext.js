/**
 * Session-only AI Jam musical context cache (planned window).
 *
 * Warm-path `refresh` builds planned_window + phrase/key belief from
 * hysteresis belief + optional V2 spans + features. Hot-path generators
 * read `getSnapshot()` only (O(1)).
 *
 * musicStore pump wiring belongs in later jam tasks.
 */

import { createAppLogger } from './appLogger.js';
import { parseLiveChordSymbol } from './liveChordTones.js';

const log = createAppLogger('liveJam');

const DEFAULT_WINDOW_BARS = 4;
const DEFAULT_BAR_TICKS = 1920;
const MAX_PHRASE_TICKS = 32;

/**
 * @typedef {{
 *   start_tick: number,
 *   end_tick: number,
 *   symbol: string|null,
 *   source: 'v2'|'inferred'|'held',
 * }} PlannedWindowEntry
 *
 * @typedef {{
 *   belief_symbol: string|null,
 *   belief_confidence: number,
 *   planned_window: PlannedWindowEntry[],
 *   phrase_boundary_ticks: number[],
 *   key_belief: { tonic_pc: number, mode: string, confidence: number },
 *   now_tick: number,
 *   frozen: boolean,
 * }} LiveJamContextSnapshot
 */

/**
 * @param {{
 *   barTicks?: number,
 *   defaultWindowBars?: number,
 * }} [options]
 */
export function createLiveJamContext(options = {}) {
  const barTicks = Math.max(
    1,
    Math.round(Number(options.barTicks) || DEFAULT_BAR_TICKS),
  );
  const defaultWindowBars = Math.max(
    1,
    Math.min(16, Math.round(Number(options.defaultWindowBars) || DEFAULT_WINDOW_BARS)),
  );

  /** @type {LiveJamContextSnapshot} */
  let snapshot = emptySnapshot();
  let frozen = false;
  /** @type {number} */
  let lastLoggedBar = -1;
  /** @type {number[]} */
  let phraseBoundaryTicks = [];

  /**
   * Warm-path refresh from belief + features + optional V2 spans.
   * @param {{
   *   belief?: { symbol?: string|null, confidence?: number, held?: boolean }|null,
   *   features?: object|null,
   *   v2HarmonySpans?: Array<{ start_tick?: number, duration_ticks?: number, chord?: string, symbol?: string }>|null,
   *   nowTick?: number,
   *   windowBars?: number,
   *   barTicks?: number,
   * }} args
   */
  function refresh(args = {}) {
    if (frozen) {
      log.debug('jam context refresh skipped — frozen');
      return getSnapshot();
    }

    const nowTick = Math.max(0, Math.round(Number(args.nowTick) || 0));
    const windowBars = Math.max(
      1,
      Math.min(16, Math.round(Number(args.windowBars) || defaultWindowBars)),
    );
    const ticksPerBar = Math.max(
      1,
      Math.round(Number(args.barTicks) || barTicks),
    );
    const belief = args.belief && typeof args.belief === 'object' ? args.belief : {};
    const beliefSymbol = typeof belief.symbol === 'string' && belief.symbol.trim()
      ? belief.symbol.trim().slice(0, 64)
      : null;
    const beliefConfidence = clamp01(Number(belief.confidence));
    const held = Boolean(belief.held);

    const features = args.features && typeof args.features === 'object'
      ? args.features
      : {};
    const keySrc = features.probable_key && typeof features.probable_key === 'object'
      ? features.probable_key
      : {};
    const keyBelief = {
      tonic_pc: clampInt(keySrc.tonic_pc, 0, 11, 0),
      mode: typeof keySrc.mode === 'string' ? String(keySrc.mode).slice(0, 16) : 'major',
      confidence: clamp01(Number(keySrc.confidence)),
    };

    const phrase = features.phrase && typeof features.phrase === 'object'
      ? features.phrase
      : {};
    if (phrase.boundary_likely) {
      appendPhraseBoundary(nowTick);
    }

    const spans = normalizeSpans(args.v2HarmonySpans);
    const windowEnd = nowTick + windowBars * ticksPerBar;
    const planned = buildPlannedWindow({
      nowTick,
      windowEnd,
      ticksPerBar,
      windowBars,
      spans,
      beliefSymbol,
      held,
    });

    snapshot = Object.freeze({
      belief_symbol: beliefSymbol,
      belief_confidence: beliefConfidence,
      planned_window: Object.freeze(planned.map((e) => Object.freeze({ ...e }))),
      phrase_boundary_ticks: Object.freeze(phraseBoundaryTicks.slice()),
      key_belief: Object.freeze({ ...keyBelief }),
      now_tick: nowTick,
      frozen: false,
    });

    const bar = Math.floor(nowTick / ticksPerBar) + 1;
    if (bar !== lastLoggedBar) {
      lastLoggedBar = bar;
      log.debug('jam context refresh', {
        bar,
        window_len: planned.length,
        belief: beliefSymbol ? beliefSymbol.slice(0, 16) : null,
        held,
      });
    }

    return getSnapshot();
  }

  /** O(1) last snapshot for hot-path generators. */
  function getSnapshot() {
    return snapshot;
  }

  /**
   * @param {{ reason?: string }} [opts]
   */
  function clear(opts = {}) {
    const reason = String(opts.reason || 'clear').slice(0, 64);
    frozen = false;
    phraseBoundaryTicks = [];
    lastLoggedBar = -1;
    snapshot = emptySnapshot();
    log.info('jam context clear', { reason });
    return getSnapshot();
  }

  /** Freeze after Stop for Commit harmony spans (no further refresh). */
  function freeze() {
    frozen = true;
    snapshot = Object.freeze({
      ...snapshot,
      planned_window: snapshot.planned_window,
      phrase_boundary_ticks: snapshot.phrase_boundary_ticks,
      key_belief: snapshot.key_belief,
      frozen: true,
    });
    log.info('jam context freeze', {
      window_len: snapshot.planned_window.length,
      belief: snapshot.belief_symbol
        ? String(snapshot.belief_symbol).slice(0, 16)
        : null,
    });
    return getSnapshot();
  }

  function isFrozen() {
    return frozen;
  }

  function appendPhraseBoundary(tick) {
    const t = Math.max(0, Math.round(Number(tick) || 0));
    if (phraseBoundaryTicks.length > 0
      && phraseBoundaryTicks[phraseBoundaryTicks.length - 1] === t) {
      return;
    }
    phraseBoundaryTicks.push(t);
    if (phraseBoundaryTicks.length > MAX_PHRASE_TICKS) {
      phraseBoundaryTicks = phraseBoundaryTicks.slice(-MAX_PHRASE_TICKS);
    }
  }

  return {
    refresh,
    getSnapshot,
    clear,
    freeze,
    isFrozen,
  };
}

/**
 * @param {{
 *   nowTick: number,
 *   windowEnd: number,
 *   ticksPerBar: number,
 *   windowBars: number,
 *   spans: Array<{ start_tick: number, end_tick: number, symbol: string }>,
 *   beliefSymbol: string|null,
 *   held: boolean,
 * }} args
 * @returns {PlannedWindowEntry[]}
 */
function buildPlannedWindow(args) {
  /** @type {PlannedWindowEntry[]} */
  const entries = [];
  const {
    nowTick,
    windowEnd,
    ticksPerBar,
    windowBars,
    spans,
    beliefSymbol,
    held,
  } = args;

  // Prefer V2 spans overlapping the window; fill gaps with belief.
  const overlapping = spans.filter(
    (s) => s.end_tick > nowTick && s.start_tick < windowEnd,
  );

  if (overlapping.length > 0) {
    let cursor = nowTick;
    const ordered = overlapping
      .slice()
      .sort((a, b) => a.start_tick - b.start_tick || a.end_tick - b.end_tick);

    for (const span of ordered) {
      const start = Math.max(nowTick, span.start_tick);
      const end = Math.min(windowEnd, span.end_tick);
      if (start > cursor && beliefSymbol) {
        entries.push({
          start_tick: cursor,
          end_tick: start,
          symbol: beliefSymbol,
          source: held ? 'held' : 'inferred',
        });
      }
      if (end > start) {
        entries.push({
          start_tick: start,
          end_tick: end,
          symbol: span.symbol,
          source: 'v2',
        });
        cursor = Math.max(cursor, end);
      }
    }
    if (cursor < windowEnd && beliefSymbol) {
      entries.push({
        start_tick: cursor,
        end_tick: windowEnd,
        symbol: beliefSymbol,
        source: held ? 'held' : 'inferred',
      });
    }
    return mergeAdjacent(entries);
  }

  // No V2 coverage — one entry per bar tagged inferred|held.
  if (!beliefSymbol) {
    return entries;
  }
  for (let i = 0; i < windowBars; i += 1) {
    const start = nowTick + i * ticksPerBar;
    const end = start + ticksPerBar;
    entries.push({
      start_tick: start,
      end_tick: Math.min(end, windowEnd),
      symbol: beliefSymbol,
      source: held ? 'held' : 'inferred',
    });
  }
  return entries;
}

/**
 * @param {PlannedWindowEntry[]} entries
 */
function mergeAdjacent(entries) {
  if (entries.length === 0) return entries;
  /** @type {PlannedWindowEntry[]} */
  const out = [];
  for (const entry of entries) {
    const last = out[out.length - 1];
    if (
      last
      && last.end_tick === entry.start_tick
      && last.symbol === entry.symbol
      && last.source === entry.source
    ) {
      last.end_tick = entry.end_tick;
    } else {
      out.push({ ...entry });
    }
  }
  return out;
}

/**
 * @param {unknown} raw
 */
function normalizeSpans(raw) {
  if (!Array.isArray(raw)) return [];
  /** @type {Array<{ start_tick: number, end_tick: number, symbol: string }>} */
  const out = [];
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue;
    const start = Math.round(Number(item.start_tick));
    const dur = Math.round(Number(item.duration_ticks));
    const symbol = String(item.chord ?? item.symbol ?? '').trim();
    if (!Number.isFinite(start) || !Number.isFinite(dur) || dur <= 0 || !symbol) {
      continue;
    }
    if (!parseLiveChordSymbol(symbol).parseable) continue;
    out.push({
      start_tick: Math.max(0, start),
      end_tick: Math.max(0, start) + dur,
      symbol: symbol.slice(0, 64),
    });
  }
  return out;
}

function emptySnapshot() {
  return Object.freeze({
    belief_symbol: null,
    belief_confidence: 0,
    planned_window: Object.freeze(/** @type {PlannedWindowEntry[]} */ ([])),
    phrase_boundary_ticks: Object.freeze(/** @type {number[]} */ ([])),
    key_belief: Object.freeze({ tonic_pc: 0, mode: 'major', confidence: 0 }),
    now_tick: 0,
    frozen: false,
  });
}

function clamp01(n) {
  if (!Number.isFinite(n)) return 0;
  return Math.max(0, Math.min(1, n));
}

function clampInt(value, min, max, fallback) {
  const n = Math.round(Number(value));
  if (!Number.isFinite(n)) return fallback;
  return Math.max(min, Math.min(max, n));
}
