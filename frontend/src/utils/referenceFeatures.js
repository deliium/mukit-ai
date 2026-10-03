/**
 * Reference feature dimension masks for generate / develop soft conditioning.
 *
 * Labels match backend AC map (Rhythmic density → density, Orchestration texture → texture).
 * Does not copy melodies — only abstract dimension ids / summaries.
 */

import { createAppLogger } from './appLogger.js';

const logger = createAppLogger('referenceFeatures');

export const REFERENCE_FEATURES_STORAGE_KEY = 'referenceFeatures:v1';

export const REFERENCE_FEATURE_DIMENSIONS = Object.freeze([
  { id: 'harmony', label: 'Harmony' },
  { id: 'harmonic_rhythm', label: 'Harmonic rhythm' },
  { id: 'rhythm', label: 'Rhythm' },
  { id: 'melodic_contour', label: 'Melodic contour' },
  { id: 'texture', label: 'Orchestration texture' },
  { id: 'instrumentation', label: 'Instrumentation' },
  { id: 'density', label: 'Rhythmic density' },
  { id: 'dynamics', label: 'Dynamics' },
  { id: 'form', label: 'Form' },
  { id: 'tension_curve', label: 'Tension curve' },
  { id: 'motif_characteristics', label: 'Motif characteristics' },
  { id: 'performance_characteristics', label: 'Performance' },
]);

export const REFERENCE_FEATURE_DIMENSION_IDS = Object.freeze(
  REFERENCE_FEATURE_DIMENSIONS.map((item) => item.id),
);

const DIMENSION_ID_SET = new Set(REFERENCE_FEATURE_DIMENSION_IDS);

/**
 * @param {unknown} raw
 * @returns {{ ok: true, dimensions: string[]|null } | { ok: false, code: string, message: string }}
 *
 * `null` = legacy whole-summary (no mask).
 * Non-empty array = masked dimensions.
 * Empty array is invalid for conditioning.
 */
export function normalizeDimensionMask(raw) {
  if (raw == null) {
    return { ok: true, dimensions: null };
  }
  if (!Array.isArray(raw)) {
    return {
      ok: false,
      code: 'reference_feature_mask_invalid',
      message: 'dimensions must be an array or omitted',
    };
  }
  if (raw.length === 0) {
    return {
      ok: false,
      code: 'reference_feature_mask_empty',
      message: 'Select at least one reference feature dimension',
    };
  }
  const seen = new Set();
  const out = [];
  for (const item of raw) {
    const id = typeof item === 'string' ? item.trim() : '';
    if (!DIMENSION_ID_SET.has(id)) {
      return {
        ok: false,
        code: 'reference_feature_unknown_dimension',
        message: `Unknown dimension: ${id || String(item)}`,
      };
    }
    if (seen.has(id)) continue;
    seen.add(id);
    out.push(id);
  }
  if (!out.length) {
    return {
      ok: false,
      code: 'reference_feature_mask_empty',
      message: 'Select at least one reference feature dimension',
    };
  }
  return { ok: true, dimensions: out };
}

/**
 * Toggle a dimension id in a mask list (immutable).
 * @param {string[]|null} current
 * @param {string} dimensionId
 * @param {boolean} enabled
 */
export function toggleDimensionMask(current, dimensionId, enabled) {
  const base = Array.isArray(current) ? [...current] : [];
  const idx = base.indexOf(dimensionId);
  if (enabled && idx < 0 && DIMENSION_ID_SET.has(dimensionId)) {
    base.push(dimensionId);
  } else if (!enabled && idx >= 0) {
    base.splice(idx, 1);
  }
  return base;
}

/**
 * Persist session mask only (non-secret).
 * @param {{ dimensions?: string[]|null, enabled?: boolean }} state
 * @param {Storage} [storage=globalThis.localStorage]
 */
export function saveReferenceFeaturesSession(
  state,
  storage = globalThis.localStorage,
) {
  try {
    if (!storage || typeof storage.setItem !== 'function') {
      return;
    }
    const payload = {
      v: 1,
      enabled: Boolean(state?.enabled),
      dimensions: Array.isArray(state?.dimensions) ? state.dimensions.slice(0, 32) : null,
    };
    storage.setItem(REFERENCE_FEATURES_STORAGE_KEY, JSON.stringify(payload));
    logger.debug('Reference features session saved', {
      enabled: payload.enabled,
      dimensionCount: payload.dimensions?.length ?? 0,
    });
  } catch {
    // ignore quota / private mode
  }
}

/**
 * @param {Storage} [storage=globalThis.localStorage]
 * @returns {{ enabled: boolean, dimensions: string[]|null }}
 */
export function loadReferenceFeaturesSession(storage = globalThis.localStorage) {
  try {
    if (!storage || typeof storage.getItem !== 'function') {
      return { enabled: false, dimensions: null };
    }
    const raw = storage.getItem(REFERENCE_FEATURES_STORAGE_KEY);
    if (!raw) {
      return { enabled: false, dimensions: null };
    }
    const parsed = JSON.parse(raw);
    const mask = normalizeDimensionMask(parsed?.dimensions ?? null);
    return {
      enabled: Boolean(parsed?.enabled),
      dimensions: mask.ok ? mask.dimensions : null,
    };
  } catch {
    return { enabled: false, dimensions: null };
  }
}

/**
 * Attach an optional dimension mask onto a normalized style_reference object.
 * @param {object|null} styleReference
 * @param {string[]|null|undefined} dimensions
 */
export function withDimensionMask(styleReference, dimensions) {
  if (!styleReference) {
    return styleReference;
  }
  const mask = normalizeDimensionMask(dimensions);
  if (!mask.ok) {
    return { ok: false, code: mask.code, message: mask.message };
  }
  if (mask.dimensions == null) {
    return { ok: true, styleReference };
  }
  return {
    ok: true,
    styleReference: {
      ...styleReference,
      dimensions: mask.dimensions,
    },
  };
}

export function dimensionLabel(dimensionId) {
  const found = REFERENCE_FEATURE_DIMENSIONS.find((item) => item.id === dimensionId);
  return found?.label || dimensionId;
}

export { formatReferenceRightsRefuseMessage } from './personalComposerForm.js';
