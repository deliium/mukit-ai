/**
 * Co-performance latency marks (live.latency.v1 aggregates).
 *
 * Mark pairs for midi_input, analysis, generation, scheduling.
 * DEBUG samples gated by VITE_LOG_LEVEL; INFO summary ≥ 1s interval.
 */

import { createAppLogger } from './appLogger.js';
import { LATENCY_MARK_KEYS } from './liveSessionContracts.js';

const log = createAppLogger('liveTransport');

/**
 * @param {{ now?: () => number, summaryIntervalMs?: number }} [options]
 */
export function createLiveLatencyTracker(options = {}) {
  const nowFn =
    typeof options.now === 'function'
      ? options.now
      : () => (
        typeof performance !== 'undefined' && typeof performance.now === 'function'
          ? performance.now()
          : Date.now()
      );
  const summaryIntervalMs = Math.max(
    1000,
    Math.round(Number(options.summaryIntervalMs) || 1000),
  );

  /** @type {Record<string, number|null>} */
  const lastMs = Object.fromEntries(LATENCY_MARK_KEYS.map((k) => [k, null]));
  /** @type {Record<string, number>} */
  const ewmaMs = Object.fromEntries(LATENCY_MARK_KEYS.map((k) => [k, 0]));
  /** @type {Record<string, number>} */
  const openMarks = {};
  let lastSummaryAt = 0;
  let sampleCount = 0;

  /**
   * @param {'midi_input'|'analysis'|'generation'|'scheduling'} key
   */
  function markStart(key) {
    if (!LATENCY_MARK_KEYS.includes(key)) return;
    openMarks[key] = nowFn();
    log.debug('latency mark start', { key });
  }

  /**
   * @param {'midi_input'|'analysis'|'generation'|'scheduling'} key
   * @returns {number|null} elapsed ms
   */
  function markEnd(key) {
    if (!LATENCY_MARK_KEYS.includes(key)) return null;
    const started = openMarks[key];
    delete openMarks[key];
    if (started == null) return null;
    const elapsed = Math.max(0, nowFn() - started);
    lastMs[key] = elapsed;
    const prev = ewmaMs[key] || elapsed;
    ewmaMs[key] = prev * 0.7 + elapsed * 0.3;
    sampleCount += 1;
    log.debug('latency mark end', { key, ms: Number(elapsed.toFixed(2)) });
    maybeSummary();
    return elapsed;
  }

  /**
   * Record a completed duration without start/end pairing.
   * @param {'midi_input'|'analysis'|'generation'|'scheduling'} key
   * @param {number} ms
   */
  function record(key, ms) {
    if (!LATENCY_MARK_KEYS.includes(key)) return;
    const elapsed = Math.max(0, Number(ms) || 0);
    lastMs[key] = elapsed;
    const prev = ewmaMs[key] || elapsed;
    ewmaMs[key] = prev * 0.7 + elapsed * 0.3;
    sampleCount += 1;
    log.debug('latency record', { key, ms: Number(elapsed.toFixed(2)) });
    maybeSummary();
  }

  function maybeSummary() {
    const t = nowFn();
    if (t - lastSummaryAt < summaryIntervalMs) return;
    lastSummaryAt = t;
    log.info('latency summary', {
      sampleCount,
      midi_input: round(lastMs.midi_input),
      analysis: round(lastMs.analysis),
      generation: round(lastMs.generation),
      scheduling: round(lastMs.scheduling),
      ewma_scheduling: round(ewmaMs.scheduling),
    });
  }

  function snapshot() {
    return {
      midi_input: lastMs.midi_input,
      analysis: lastMs.analysis,
      generation: lastMs.generation,
      scheduling: lastMs.scheduling,
      ewma: { ...ewmaMs },
      sampleCount,
    };
  }

  function reset() {
    for (const key of LATENCY_MARK_KEYS) {
      lastMs[key] = null;
      ewmaMs[key] = 0;
      delete openMarks[key];
    }
    sampleCount = 0;
    lastSummaryAt = 0;
  }

  return {
    markStart,
    markEnd,
    record,
    snapshot,
    reset,
  };
}

function round(value) {
  if (value == null || !Number.isFinite(Number(value))) return null;
  return Number(Number(value).toFixed(2));
}
