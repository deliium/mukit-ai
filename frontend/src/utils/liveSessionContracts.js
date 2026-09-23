/**
 * Co-performance live session contracts (FE).
 *
 * Terminology lock (see plan v4-co-performance-realtime-engine):
 * - live.session.v1 — session snapshot; never autosaved / never revision snapshot
 * - live.accompaniment.predict.request.v1 — bounded cold-path HTTP body
 * - live.accompaniment.chunk.v1 — ephemeral events until explicit Commit
 * - Active harmony — metadata only; never invents playable score notes
 * - Incoming MIDI stream — Transport-synced; exclusive with midiPhase capture
 *
 * Never put composition / tracks / events / raw MIDI dumps into predict bodies.
 * Logging: codes + counts only (createAppLogger namespaces liveTransport / liveMidi / liveAccompaniment).
 */

import { createAppLogger } from './appLogger.js';

const log = createAppLogger('liveSession');

export const LIVE_SESSION_SCHEMA = 'live.session.v1';
export const LIVE_PREDICT_REQUEST_SCHEMA = 'live.accompaniment.predict.request.v1';
export const LIVE_ACCOMPANIMENT_CHUNK_SCHEMA = 'live.accompaniment.chunk.v1';

/** @typedef {'idle'|'arming'|'running'|'degraded'|'stopping'|'cancelled'} LiveSessionPhase */
/** @typedef {'local_pattern'|'fake'|'symbolic'|'llm'} LiveChunkSource */

export const LIVE_SESSION_PHASES = Object.freeze([
  'idle',
  'arming',
  'running',
  'degraded',
  'stopping',
  'cancelled',
]);

export const LIVE_CHUNK_SOURCES = Object.freeze([
  'local_pattern',
  'fake',
  'symbolic',
  'llm',
]);

export const LIVE_DEGRADED_PATTERN_CONTINUE = 'live_degraded_pattern_continue';
export const LIVE_HARMONY_EMPTY = 'live_harmony_empty';
export const LIVE_ENGINE_UNAVAILABLE = 'live_engine_unavailable';
export const LIVE_MIDI_PHASE_EXCLUSION = 'live_midi_phase_exclusion';

export const LIVE_HORIZON_INVALID = 'live_horizon_invalid';
export const LIVE_FEATURES_TOO_LARGE = 'live_features_too_large';
export const LIVE_SESSION_CONTEXT_INVALID = 'live_session_context_invalid';
export const LIVE_EVENTS_FORBIDDEN_IN_REQUEST = 'live_events_forbidden_in_request';

export const LATENCY_MARK_KEYS = Object.freeze([
  'midi_input',
  'analysis',
  'generation',
  'scheduling',
]);

export const FORBIDDEN_LIVE_PREDICT_KEYS = Object.freeze(
  new Set([
    'tracks',
    'musicxml',
    'midi',
    'wav',
    'analysis',
    'analysis_report',
    'composition',
    'music',
    'events',
    'note_events',
    'embedding',
    'vector',
    'motifs',
    'raw_midi',
    'midi_dump',
    'edited_music_json',
    'music_json',
  ]),
);

/**
 * Default / clamped horizon bounds (aligned with backend LIVE_* defaults).
 * Override via VITE_LIVE_* when present.
 */
export function readLiveHorizonBounds(envReader = readViteEnv) {
  const barsMin = intEnv(envReader, 'VITE_LIVE_HORIZON_BARS_MIN', 1, 1, 4);
  const barsMax = intEnv(envReader, 'VITE_LIVE_HORIZON_BARS_MAX', 2, barsMin, 8);
  const barsDefault = intEnv(envReader, 'VITE_LIVE_HORIZON_BARS', 1, barsMin, barsMax);
  const msMin = intEnv(envReader, 'VITE_LIVE_HORIZON_MS_MIN', 250, 50, 60_000);
  const msMax = intEnv(envReader, 'VITE_LIVE_HORIZON_MS_MAX', 8_000, msMin, 60_000);
  const msDefault = intEnv(envReader, 'VITE_LIVE_HORIZON_MS', 2_000, msMin, msMax);
  const maxEvents = intEnv(envReader, 'VITE_LIVE_ACCOMP_MAX_EVENTS_PER_CHUNK', 64, 1, 256);
  const maxFeaturesBytes = intEnv(
    envReader,
    'VITE_LIVE_PREDICT_MAX_FEATURES_BYTES',
    4_096,
    256,
    65_536,
  );
  const predictTimeoutMs = intEnv(envReader, 'VITE_LIVE_PREDICT_TIMEOUT_MS', 1_500, 100, 30_000);
  const maxInFlight = 1; // v1 lock
  const scheduleSlackMs = intEnv(envReader, 'VITE_LIVE_SCHEDULE_SLACK_MS', 80, 0, 2_000);
  return Object.freeze({
    barsMin,
    barsMax,
    barsDefault,
    msMin,
    msMax,
    msDefault,
    maxEventsPerChunk: maxEvents,
    maxFeaturesBytes,
    predictTimeoutMs,
    maxInFlight,
    scheduleSlackMs,
  });
}

function readViteEnv(name) {
  try {
    const viteEnv = typeof import.meta !== 'undefined' ? import.meta.env : undefined;
    if (viteEnv && viteEnv[name] != null && String(viteEnv[name]).length > 0) {
      return String(viteEnv[name]);
    }
  } catch {
    // ignore
  }
  if (typeof process !== 'undefined' && process.env && process.env[name] != null) {
    const value = String(process.env[name]);
    if (value.length > 0) return value;
  }
  return undefined;
}

function intEnv(reader, key, defaultValue, minimum, maximum) {
  const raw = reader(key);
  if (raw == null || String(raw).trim() === '') return defaultValue;
  const parsed = Number.parseInt(String(raw).trim(), 10);
  if (!Number.isFinite(parsed)) return defaultValue;
  return Math.max(minimum, Math.min(maximum, parsed));
}

/**
 * @param {unknown} payload
 * @param {string} [path]
 * @returns {{ ok: true } | { ok: false, code: string, path: string }}
 */
export function findForbiddenPredictKey(payload, path = '') {
  if (payload && typeof payload === 'object' && !Array.isArray(payload)) {
    for (const [key, value] of Object.entries(payload)) {
      const child = path ? `${path}.${key}` : key;
      if (FORBIDDEN_LIVE_PREDICT_KEYS.has(String(key).toLowerCase())) {
        return { ok: false, code: LIVE_EVENTS_FORBIDDEN_IN_REQUEST, path: child.slice(0, 120) };
      }
      const nested = findForbiddenPredictKey(value, child);
      if (!nested.ok) return nested;
    }
  } else if (Array.isArray(payload)) {
    for (let i = 0; i < payload.length; i += 1) {
      const nested = findForbiddenPredictKey(payload[i], `${path}[${i}]`);
      if (!nested.ok) return nested;
    }
  }
  return { ok: true };
}

/**
 * @param {{ bars: number, ms: number }} horizon
 * @param {ReturnType<typeof readLiveHorizonBounds>} [bounds]
 */
export function validateLiveHorizon(horizon, bounds = readLiveHorizonBounds()) {
  const bars = Number(horizon?.bars);
  const ms = Number(horizon?.ms);
  if (!Number.isFinite(bars) || bars < bounds.barsMin || bars > bounds.barsMax) {
    log.debug('horizon validate fail', { code: LIVE_HORIZON_INVALID, field: 'bars' });
    return {
      ok: false,
      code: LIVE_HORIZON_INVALID,
      details: { bars, min: bounds.barsMin, max: bounds.barsMax },
    };
  }
  if (!Number.isFinite(ms) || ms < bounds.msMin || ms > bounds.msMax) {
    log.debug('horizon validate fail', { code: LIVE_HORIZON_INVALID, field: 'ms' });
    return {
      ok: false,
      code: LIVE_HORIZON_INVALID,
      details: { ms, min: bounds.msMin, max: bounds.msMax },
    };
  }
  log.debug('horizon validate pass', { bars, ms });
  return { ok: true, horizon: { bars, ms } };
}

/**
 * @param {Record<string, unknown>} features
 * @param {ReturnType<typeof readLiveHorizonBounds>} [bounds]
 */
export function validateLivePredictFeatures(features, bounds = readLiveHorizonBounds()) {
  const forbidden = findForbiddenPredictKey(features);
  if (!forbidden.ok) {
    log.debug('features validate fail', { code: forbidden.code, path: forbidden.path });
    return forbidden;
  }
  let size = 0;
  try {
    size = new TextEncoder().encode(JSON.stringify(features ?? {})).length;
  } catch {
    return { ok: false, code: LIVE_FEATURES_TOO_LARGE, details: { bytes: -1 } };
  }
  if (size > bounds.maxFeaturesBytes) {
    log.debug('features validate fail', { code: LIVE_FEATURES_TOO_LARGE, bytes: size });
    return {
      ok: false,
      code: LIVE_FEATURES_TOO_LARGE,
      details: { bytes: size, max_bytes: bounds.maxFeaturesBytes },
    };
  }
  log.debug('features validate pass', { bytes: size });
  return { ok: true, bytes: size };
}

/**
 * Build a minimal idle live.session.v1 snapshot.
 * @param {string} sessionId
 * @param {Partial<{
 *   horizon: { bars: number, ms: number },
 *   jam_mode: import('./liveJamContracts.js').JamMode | null,
 * }>} [opts]
 */
export function createIdleLiveSession(sessionId, opts = {}) {
  const bounds = readLiveHorizonBounds();
  const horizon = opts.horizon ?? { bars: bounds.barsDefault, ms: bounds.msDefault };
  return {
    schema: LIVE_SESSION_SCHEMA,
    session_id: String(sessionId || ''),
    phase: /** @type {LiveSessionPhase} */ ('idle'),
    transport: { playing: false, tick: 0, bar: 1, beat: 1 },
    horizon,
    active_harmony: { symbol: null, start_tick: null, duration_ticks: null },
    belief: { symbol: null, confidence: 0, held: false, reason_code: null },
    jam_mode: opts.jam_mode ?? null,
    jam_controls: null,
    latency_ms: {
      midi_input: null,
      analysis: null,
      generation: null,
      scheduling: null,
    },
    degradation: { active: false, code: null, count: 0 },
    jam_flags: {
      predict_unavailable: false,
      harmony_hold_count: 0,
    },
  };
}

/**
 * Validate a predict request body before HTTP send.
 * @param {unknown} body
 * @param {ReturnType<typeof readLiveHorizonBounds>} [bounds]
 */
export function validateLivePredictRequest(body, bounds = readLiveHorizonBounds()) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return { ok: false, code: LIVE_SESSION_CONTEXT_INVALID };
  }
  /** @type {Record<string, unknown>} */
  const req = /** @type {Record<string, unknown>} */ (body);
  const forbidden = findForbiddenPredictKey(req);
  if (!forbidden.ok) {
    log.debug('predict request validate fail', { code: forbidden.code, path: forbidden.path });
    return forbidden;
  }
  if (req.schema !== LIVE_PREDICT_REQUEST_SCHEMA && req.schema_version !== LIVE_PREDICT_REQUEST_SCHEMA) {
    // Accept either key for FE convenience; backend uses schema_version.
  }
  const sessionId = String(req.session_id ?? '').trim();
  const requestId = String(req.request_id ?? '').trim();
  if (!sessionId || !requestId) {
    return { ok: false, code: LIVE_SESSION_CONTEXT_INVALID };
  }
  const horizonResult = validateLiveHorizon(
    /** @type {{ bars: number, ms: number }} */ (req.horizon ?? {}),
    bounds,
  );
  if (!horizonResult.ok) return horizonResult;
  const featuresResult = validateLivePredictFeatures(
    /** @type {Record<string, unknown>} */ (req.features ?? {}),
    bounds,
  );
  if (!featuresResult.ok) return featuresResult;
  log.debug('predict request validate pass', {
    session_id_len: sessionId.length,
    request_id_len: requestId.length,
  });
  return { ok: true };
}

/**
 * Normalize an accompaniment chunk for buffer insert.
 * @param {unknown} chunk
 * @param {ReturnType<typeof readLiveHorizonBounds>} [bounds]
 */
export function normalizeLiveAccompanimentChunk(chunk, bounds = readLiveHorizonBounds()) {
  if (!chunk || typeof chunk !== 'object' || Array.isArray(chunk)) {
    return { ok: false, code: LIVE_SESSION_CONTEXT_INVALID };
  }
  /** @type {Record<string, unknown>} */
  const raw = /** @type {Record<string, unknown>} */ (chunk);
  const events = Array.isArray(raw.events) ? raw.events : [];
  if (events.length > bounds.maxEventsPerChunk) {
    return {
      ok: false,
      code: LIVE_SESSION_CONTEXT_INVALID,
      details: { count: events.length, max: bounds.maxEventsPerChunk },
    };
  }
  const source = LIVE_CHUNK_SOURCES.includes(/** @type {string} */ (raw.source))
    ? raw.source
    : 'local_pattern';
  return {
    ok: true,
    chunk: {
      schema: LIVE_ACCOMPANIMENT_CHUNK_SCHEMA,
      request_id: String(raw.request_id ?? ''),
      session_id: String(raw.session_id ?? ''),
      start_tick: Math.max(0, Number(raw.start_tick) || 0),
      events: events.map(normalizeEphemeralEvent).filter(Boolean),
      source,
      generated_at_ms: Number(raw.generated_at_ms) || 0,
      latency_ms: raw.latency_ms && typeof raw.latency_ms === 'object' ? raw.latency_ms : undefined,
    },
  };
}

function normalizeEphemeralEvent(event) {
  if (!event || typeof event !== 'object') return null;
  const pitch = Number(event.pitch);
  const startTick = Number(event.start_tick);
  const durationTicks = Number(event.duration_ticks);
  if (
    !Number.isFinite(pitch)
    || pitch < 0
    || pitch > 127
    || !Number.isFinite(startTick)
    || startTick < 0
    || !Number.isFinite(durationTicks)
    || durationTicks < 1
  ) {
    return null;
  }
  const velocity = Number(event.velocity);
  return {
    pitch: Math.round(pitch),
    start_tick: Math.round(startTick),
    duration_ticks: Math.round(durationTicks),
    velocity: Number.isFinite(velocity)
      ? Math.max(1, Math.min(127, Math.round(velocity)))
      : 80,
    track_role: typeof event.track_role === 'string' ? event.track_role.slice(0, 32) : undefined,
  };
}
