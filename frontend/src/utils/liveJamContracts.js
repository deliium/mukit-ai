/**
 * AI Jam product contracts (FE) — extends co-performance live session.
 *
 * Terminology (plan v4-ai-jam-realtime-co-composition):
 * - jam_mode — user_melody | user_chords role partition
 * - live.performance.features.v1 — raw analysis only (NO nested belief)
 * - belief — smoothed harmony after hysteresis; session snapshot / predict active_harmony
 * - jam controls — session-only; never autosaved / never revision state
 *
 * Mode → role matrix (locked):
 *   user_melody:  user=[melody]; ai=[bass, accompaniment]; optional_ai=[texture] if complexity≥medium
 *   user_chords:  user=[harmony]; ai=[melody, bass, texture]
 *
 * Logging: createAppLogger('liveJam') — enums/counts only; never histograms at INFO.
 */

import { createAppLogger } from './appLogger.js';
import {
  LIVE_EVENTS_FORBIDDEN_IN_REQUEST,
  LIVE_FEATURES_TOO_LARGE,
  findForbiddenPredictKey,
  readLiveHorizonBounds,
} from './liveSessionContracts.js';

const log = createAppLogger('liveJam');

export const LIVE_PERFORMANCE_FEATURES_SCHEMA = 'live.performance.features.v1';

/** @typedef {'user_melody'|'user_chords'} JamMode */
/** @typedef {'melody'|'bass'|'harmony'|'accompaniment'|'texture'} JamTrackRole */
/** @typedef {'low'|'medium'|'high'} JamComplexity */
/** @typedef {'low'|'medium'|'high'} JamDensity */
/** @typedef {'block'|'arp'|'alberti'|'pad'} JamAccompanimentStyle */
/** @typedef {'low'|'medium'|'high'} JamResponsiveness */

export const JAM_MODES = Object.freeze(['user_melody', 'user_chords']);

export const JAM_TRACK_ROLES = Object.freeze([
  'melody',
  'bass',
  'harmony',
  'accompaniment',
  'texture',
]);

export const JAM_COMPLEXITY_LEVELS = Object.freeze(['low', 'medium', 'high']);
export const JAM_DENSITY_LEVELS = Object.freeze(['low', 'medium', 'high']);
export const JAM_ACCOMPANIMENT_STYLES = Object.freeze(['block', 'arp', 'alberti', 'pad']);
export const JAM_RESPONSIVENESS_LEVELS = Object.freeze(['low', 'medium', 'high']);

/** Warning / lifecycle codes (counts + badges — never event dumps). */
export const JAM_HARMONY_HOLD = 'jam_harmony_hold';
export const JAM_HARMONY_CHANGED = 'jam_harmony_changed';
export const JAM_PREDICT_UNAVAILABLE = 'jam_predict_unavailable';
export const JAM_ROLE_SKIPPED = 'jam_role_skipped';
export const JAM_COMMIT_MAP_INCOMPLETE = 'jam_commit_map_incomplete';
export const JAM_BELIEF_INSIDE_FEATURES = 'jam_belief_inside_features';
export const JAM_MODE_INVALID = 'jam_mode_invalid';
export const JAM_CONTROLS_INVALID = 'jam_controls_invalid';

/**
 * Locked mode → role partition.
 * @type {Readonly<Record<JamMode, { user_roles: JamTrackRole[], ai_roles: JamTrackRole[], optional_ai: JamTrackRole[] }>>}
 */
export const JAM_ROLE_MATRIX = Object.freeze({
  user_melody: Object.freeze({
    user_roles: Object.freeze(/** @type {JamTrackRole[]} */ (['melody'])),
    ai_roles: Object.freeze(/** @type {JamTrackRole[]} */ (['bass', 'accompaniment'])),
    optional_ai: Object.freeze(/** @type {JamTrackRole[]} */ (['texture'])),
  }),
  user_chords: Object.freeze({
    user_roles: Object.freeze(/** @type {JamTrackRole[]} */ (['harmony'])),
    ai_roles: Object.freeze(/** @type {JamTrackRole[]} */ (['melody', 'bass', 'texture'])),
    optional_ai: Object.freeze(/** @type {JamTrackRole[]} */ ([])),
  }),
});

/**
 * Default hysteresis / analysis caps (aligned with backend LIVE_JAM_*).
 * @param {(name: string) => string|undefined} [envReader]
 */
export function readLiveJamSettings(envReader = readViteEnv) {
  const confidenceMin = floatEnv(envReader, 'VITE_LIVE_JAM_HARMONY_CONFIDENCE_MIN', 0.55, 0.1, 0.95);
  const dwellMs = intEnv(envReader, 'VITE_LIVE_JAM_HARMONY_DWELL_MS', 400, 100, 5_000);
  const dwellMsFloor = intEnv(envReader, 'VITE_LIVE_JAM_HARMONY_DWELL_MS_FLOOR', 150, 50, dwellMs);
  const hysteresis = floatEnv(envReader, 'VITE_LIVE_JAM_HARMONY_HYSTERESIS', 0.15, 0, 0.5);
  const maxRingEvents = intEnv(envReader, 'VITE_LIVE_JAM_ANALYSIS_MAX_EVENTS', 48, 8, 256);
  const maxRingTicks = intEnv(envReader, 'VITE_LIVE_JAM_ANALYSIS_MAX_TICKS', 1920, 240, 15_360);
  return Object.freeze({
    harmonyConfidenceMin: confidenceMin,
    harmonyDwellMs: dwellMs,
    harmonyDwellMsFloor: dwellMsFloor,
    harmonyHysteresis: hysteresis,
    analysisMaxEvents: maxRingEvents,
    analysisMaxTicks: maxRingTicks,
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

function floatEnv(reader, key, defaultValue, minimum, maximum) {
  const raw = reader(key);
  if (raw == null || String(raw).trim() === '') return defaultValue;
  const parsed = Number.parseFloat(String(raw).trim());
  if (!Number.isFinite(parsed)) return defaultValue;
  return Math.max(minimum, Math.min(maximum, parsed));
}

/**
 * @param {unknown} mode
 * @returns {mode is JamMode}
 */
export function isJamMode(mode) {
  return typeof mode === 'string' && JAM_MODES.includes(/** @type {JamMode} */ (mode));
}

/**
 * Resolve AI roles for a mode given complexity (optional_ai when ≥ medium).
 * @param {JamMode} mode
 * @param {JamComplexity} [complexity]
 * @returns {{ user_roles: JamTrackRole[], ai_roles: JamTrackRole[] }}
 */
export function resolveJamRolePartition(mode, complexity = 'medium') {
  if (!isJamMode(mode)) {
    log.debug('role partition fail', { code: JAM_MODE_INVALID, mode: String(mode).slice(0, 32) });
    throw new Error(JAM_MODE_INVALID);
  }
  const matrix = JAM_ROLE_MATRIX[mode];
  const includeOptional =
    complexity === 'medium' || complexity === 'high';
  const aiRoles = includeOptional && matrix.optional_ai.length > 0
    ? [...matrix.ai_roles, ...matrix.optional_ai]
    : [...matrix.ai_roles];
  log.debug('role partition', {
    mode,
    complexity,
    user_count: matrix.user_roles.length,
    ai_count: aiRoles.length,
  });
  return {
    user_roles: [...matrix.user_roles],
    ai_roles: aiRoles,
  };
}

/**
 * Clamp / normalize jam controls to session-safe enums.
 * @param {unknown} raw
 * @returns {{
 *   ok: true,
 *   controls: {
 *     complexity: JamComplexity,
 *     density: JamDensity,
 *     style: JamAccompanimentStyle,
 *     responsiveness: JamResponsiveness,
 *     instrument_set: Record<string, { midi_program?: number, track_id?: string }>,
 *   }
 * } | { ok: false, code: string }}
 */
export function clampJamControls(raw) {
  const src = raw && typeof raw === 'object' && !Array.isArray(raw)
    ? /** @type {Record<string, unknown>} */ (raw)
    : {};
  const complexity = pickEnum(src.complexity, JAM_COMPLEXITY_LEVELS, 'medium');
  const density = pickEnum(src.density, JAM_DENSITY_LEVELS, 'medium');
  const style = pickEnum(src.style, JAM_ACCOMPANIMENT_STYLES, 'block');
  const responsiveness = pickEnum(src.responsiveness, JAM_RESPONSIVENESS_LEVELS, 'medium');
  if (!complexity || !density || !style || !responsiveness) {
    log.debug('controls clamp fail', { code: JAM_CONTROLS_INVALID });
    return { ok: false, code: JAM_CONTROLS_INVALID };
  }
  /** @type {Record<string, { midi_program?: number, track_id?: string }>} */
  const instrumentSet = {};
  if (src.instrument_set && typeof src.instrument_set === 'object' && !Array.isArray(src.instrument_set)) {
    for (const [role, entry] of Object.entries(
      /** @type {Record<string, unknown>} */ (src.instrument_set),
    )) {
      if (!JAM_TRACK_ROLES.includes(/** @type {JamTrackRole} */ (role))) continue;
      if (!entry || typeof entry !== 'object' || Array.isArray(entry)) continue;
      const e = /** @type {Record<string, unknown>} */ (entry);
      const program = Number(e.midi_program);
      const trackId = typeof e.track_id === 'string' ? e.track_id.slice(0, 64) : undefined;
      instrumentSet[role] = {
        ...(Number.isFinite(program) && program >= 0 && program <= 127
          ? { midi_program: Math.round(program) }
          : {}),
        ...(trackId ? { track_id: trackId } : {}),
      };
    }
  }
  log.debug('controls clamp pass', { complexity, density, style, responsiveness });
  return {
    ok: true,
    controls: {
      complexity,
      density,
      style,
      responsiveness,
      instrument_set: instrumentSet,
    },
  };
}

/**
 * Scale hysteresis dwell/confidence by responsiveness (high → shorter dwell, never below floor).
 * @param {JamResponsiveness} responsiveness
 * @param {ReturnType<typeof readLiveJamSettings>} [settings]
 */
export function scaleHysteresisForResponsiveness(responsiveness, settings = readLiveJamSettings()) {
  const level = JAM_RESPONSIVENESS_LEVELS.includes(responsiveness) ? responsiveness : 'medium';
  const dwellFactor = level === 'high' ? 0.55 : level === 'low' ? 1.35 : 1;
  const confDelta = level === 'high' ? -0.05 : level === 'low' ? 0.05 : 0;
  const dwellMs = Math.max(
    settings.harmonyDwellMsFloor,
    Math.round(settings.harmonyDwellMs * dwellFactor),
  );
  const confidenceMin = Math.max(
    0.1,
    Math.min(0.95, settings.harmonyConfidenceMin + confDelta),
  );
  return Object.freeze({
    confidenceMin,
    dwellMs,
    hysteresis: settings.harmonyHysteresis,
  });
}

/**
 * Default idle jam controls.
 */
export function createDefaultJamControls() {
  return {
    complexity: /** @type {JamComplexity} */ ('medium'),
    density: /** @type {JamDensity} */ ('medium'),
    style: /** @type {JamAccompanimentStyle} */ ('block'),
    responsiveness: /** @type {JamResponsiveness} */ ('medium'),
    instrument_set: {},
  };
}

/**
 * Empty / null belief snapshot.
 * @param {Partial<{ symbol: string|null, confidence: number, held: boolean, reason_code: string|null }>} [partial]
 */
export function createIdleHarmonyBelief(partial = {}) {
  return {
    symbol: partial.symbol ?? null,
    confidence: Number.isFinite(Number(partial.confidence))
      ? Math.max(0, Math.min(1, Number(partial.confidence)))
      : 0,
    held: Boolean(partial.held),
    reason_code: partial.reason_code ?? null,
  };
}

/**
 * Reject belief nested inside features (feature/belief split lock).
 * @param {unknown} features
 * @returns {{ ok: true } | { ok: false, code: string, path: string }}
 */
export function assertNoBeliefInsideFeatures(features) {
  if (!features || typeof features !== 'object' || Array.isArray(features)) {
    return { ok: true };
  }
  const walk = (node, path) => {
    if (!node || typeof node !== 'object') return null;
    if (Array.isArray(node)) {
      for (let i = 0; i < node.length; i += 1) {
        const hit = walk(node[i], `${path}[${i}]`);
        if (hit) return hit;
      }
      return null;
    }
    for (const [key, value] of Object.entries(node)) {
      const child = path ? `${path}.${key}` : key;
      const lower = String(key).toLowerCase();
      if (lower === 'belief' || lower === 'harmony_belief' || lower === 'held_belief') {
        return child;
      }
      // Belief-shaped object mistakenly nested under features
      if (
        lower === 'held'
        && typeof value === 'boolean'
        && 'symbol' in node
        && 'confidence' in node
        && path === ''
      ) {
        // top-level held+symbol+confidence looks like belief — reject
        return child || 'belief_shape';
      }
      const hit = walk(value, child);
      if (hit) return hit;
    }
    return null;
  };
  const path = walk(features, '');
  if (path) {
    log.debug('features validate fail', { code: JAM_BELIEF_INSIDE_FEATURES, path: path.slice(0, 120) });
    return { ok: false, code: JAM_BELIEF_INSIDE_FEATURES, path: path.slice(0, 120) };
  }
  return { ok: true };
}

/**
 * Validate / normalize live.performance.features.v1 (raw analysis only).
 * @param {unknown} raw
 * @param {{ maxFeaturesBytes?: number }} [bounds]
 */
export function validateLivePerformanceFeatures(raw, bounds = {}) {
  const maxBytes = bounds.maxFeaturesBytes
    ?? readLiveHorizonBounds().maxFeaturesBytes;
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return { ok: false, code: LIVE_EVENTS_FORBIDDEN_IN_REQUEST, path: 'features' };
  }
  /** @type {Record<string, unknown>} */
  const src = /** @type {Record<string, unknown>} */ (raw);
  const forbidden = findForbiddenPredictKey(src);
  if (!forbidden.ok) return forbidden;

  const beliefCheck = assertNoBeliefInsideFeatures(src);
  if (!beliefCheck.ok) return beliefCheck;

  const schema = src.schema ?? src.schema_version;
  if (schema != null && schema !== LIVE_PERFORMANCE_FEATURES_SCHEMA) {
    log.debug('features schema unexpected', { schema: String(schema).slice(0, 64) });
  }

  let size = 0;
  try {
    size = new TextEncoder().encode(JSON.stringify(src)).length;
  } catch {
    return { ok: false, code: LIVE_FEATURES_TOO_LARGE, details: { bytes: -1 } };
  }
  if (size > maxBytes) {
    log.debug('features validate fail', { code: LIVE_FEATURES_TOO_LARGE, bytes: size });
    return {
      ok: false,
      code: LIVE_FEATURES_TOO_LARGE,
      details: { bytes: size, max_bytes: maxBytes },
    };
  }

  const normalized = normalizeLivePerformanceFeatures(src);
  log.debug('features validate pass', { bytes: size });
  return { ok: true, bytes: size, features: normalized };
}

/**
 * @param {Record<string, unknown>} src
 */
export function normalizeLivePerformanceFeatures(src) {
  const pitch = asObject(src.pitch_activity);
  const beat = asObject(src.beat);
  const probableKey = asObject(src.probable_key);
  const probableHarmony = asObject(src.probable_harmony);
  const phrase = asObject(src.phrase);

  const hist = Array.isArray(pitch.pc_histogram_12)
    ? pitch.pc_histogram_12.slice(0, 12).map((v) => {
      const n = Number(v);
      return Number.isFinite(n) ? Math.max(0, Math.min(1, n)) : 0;
    })
    : Array.from({ length: 12 }, () => 0);
  while (hist.length < 12) hist.push(0);

  return {
    schema: LIVE_PERFORMANCE_FEATURES_SCHEMA,
    pitch_activity: {
      note_on_rate: clampNum(pitch.note_on_rate, 0, 64, 0),
      pc_histogram_12: hist,
      register_mean: clampNum(pitch.register_mean, 0, 127, 60),
      register_var: clampNum(pitch.register_var, 0, 4096, 0),
    },
    beat: {
      tick: Math.max(0, Math.round(Number(beat.tick) || 0)),
      bar: Math.max(1, Math.round(Number(beat.bar) || 1)),
      beat_in_bar: Math.max(1, Math.round(Number(beat.beat_in_bar) || 1)),
      tick_in_bar: Math.max(0, Math.round(Number(beat.tick_in_bar) || 0)),
    },
    probable_key: {
      tonic_pc: clampInt(probableKey.tonic_pc, 0, 11, 0),
      mode: typeof probableKey.mode === 'string'
        ? String(probableKey.mode).slice(0, 16)
        : 'major',
      confidence: clampNum(probableKey.confidence, 0, 1, 0),
    },
    probable_harmony: {
      symbol: typeof probableHarmony.symbol === 'string'
        ? String(probableHarmony.symbol).slice(0, 64)
        : null,
      root_pc: probableHarmony.root_pc == null
        ? null
        : clampInt(probableHarmony.root_pc, 0, 11, 0),
      quality: typeof probableHarmony.quality === 'string'
        ? String(probableHarmony.quality).slice(0, 32)
        : null,
      confidence: clampNum(probableHarmony.confidence, 0, 1, 0),
    },
    phrase: {
      boundary_likely: Boolean(phrase.boundary_likely),
      bars_since_boundary: Math.max(0, Math.round(Number(phrase.bars_since_boundary) || 0)),
      confidence: clampNum(phrase.confidence, 0, 1, 0),
    },
  };
}

/**
 * Empty features skeleton for idle / cold start.
 * @param {Partial<{ tick: number, bar: number, beat_in_bar: number, tick_in_bar: number }>} [beat]
 */
export function createEmptyLivePerformanceFeatures(beat = {}) {
  return normalizeLivePerformanceFeatures({
    schema: LIVE_PERFORMANCE_FEATURES_SCHEMA,
    pitch_activity: {
      note_on_rate: 0,
      pc_histogram_12: Array.from({ length: 12 }, () => 0),
      register_mean: 60,
      register_var: 0,
    },
    beat: {
      tick: beat.tick ?? 0,
      bar: beat.bar ?? 1,
      beat_in_bar: beat.beat_in_bar ?? 1,
      tick_in_bar: beat.tick_in_bar ?? 0,
    },
    probable_key: { tonic_pc: 0, mode: 'major', confidence: 0 },
    probable_harmony: { symbol: null, root_pc: null, quality: null, confidence: 0 },
    phrase: { boundary_likely: false, bars_since_boundary: 0, confidence: 0 },
  });
}

/**
 * Normalize optional predict belief object.
 * @param {unknown} raw
 */
export function normalizeHarmonyBelief(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return createIdleHarmonyBelief();
  }
  const src = /** @type {Record<string, unknown>} */ (raw);
  return createIdleHarmonyBelief({
    symbol: typeof src.symbol === 'string' ? src.symbol.slice(0, 64) : null,
    confidence: Number(src.confidence),
    held: Boolean(src.held),
    reason_code: typeof src.reason_code === 'string' ? src.reason_code.slice(0, 80) : null,
  });
}

/**
 * @template {string} T
 * @param {unknown} value
 * @param {readonly T[]} allowed
 * @param {T} fallback
 * @returns {T|null}
 */
function pickEnum(value, allowed, fallback) {
  if (value == null || value === '') return fallback;
  if (typeof value === 'string' && allowed.includes(/** @type {T} */ (value))) {
    return /** @type {T} */ (value);
  }
  return null;
}

function asObject(value) {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? /** @type {Record<string, unknown>} */ (value)
    : {};
}

function clampNum(value, min, max, fallback) {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(min, Math.min(max, n));
}

function clampInt(value, min, max, fallback) {
  return Math.round(clampNum(value, min, max, fallback));
}
