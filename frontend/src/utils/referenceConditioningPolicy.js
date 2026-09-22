/**
 * Reference conditioning policy (preserve / borrow / regenerate) for generate,
 * develop, and AI region edit. Strengths are policy-owned — never on style_reference.
 *
 * Disjoint-only partition: unspecified dims get no soft guidance (not forced to regenerate).
 */

import { createAppLogger } from './appLogger.js';
import {
  REFERENCE_FEATURE_DIMENSION_IDS,
  normalizeDimensionMask,
} from './referenceFeatures.js';

const logger = createAppLogger('referenceConditioningPolicy');

export const REFERENCE_CONDITIONING_STORAGE_KEY = 'referenceConditioning:v1';

export const CONDITIONING_STRENGTHS = Object.freeze(['off', 'light', 'normal', 'strong']);

export const CONDITIONING_DISPOSITIONS = Object.freeze(['unspecified', 'preserve', 'borrow', 'regenerate']);

/** FE presets matching plan Part D recipes (helpers only — API accepts any valid partition). */
export const REFERENCE_CONDITIONING_PRESETS = Object.freeze({
  new_piece_texture_rhythm: {
    id: 'new_piece_texture_rhythm',
    label: 'New piece: texture A + rhythm B',
    preserve: [],
    regenerate: ['harmony', 'harmonic_rhythm', 'melodic_contour'],
    // borrow dims assigned per reference row in UI
  },
  regenerate_section_texture: {
    id: 'regenerate_section_texture',
    label: 'Regenerate section w/ ref texture',
    preserve: ['harmony', 'form'],
    regenerate: ['melodic_contour', 'density'],
    borrowHint: ['texture'],
  },
  new_melody_keep_rhythm: {
    id: 'new_melody_keep_rhythm',
    label: 'New melody, keep rhythmic character',
    preserve: ['rhythm', 'density'],
    regenerate: ['melodic_contour'],
    borrowHint: [],
  },
});

/**
 * @typedef {'off'|'light'|'normal'|'strong'} ConditioningStrength
 * @typedef {'unspecified'|'preserve'|'borrow'|'regenerate'} Disposition
 */

/**
 * @param {unknown} raw
 * @returns {ConditioningStrength}
 */
export function normalizeStrength(raw, fallback = 'normal') {
  const value = typeof raw === 'string' ? raw.trim().toLowerCase() : '';
  if (CONDITIONING_STRENGTHS.includes(value)) return value;
  return fallback;
}

/**
 * Build a canonical empty policy view model.
 */
export function emptyPolicyState() {
  return {
    preserve: [],
    regenerate: [],
    /** @type {Record<string, ConditioningStrength>} */
    dimensionStrengths: {},
    /** @type {Record<string, 'A'|'B'>} which reference row owns each borrow dim */
    borrowSourceByDim: {},
    defaultBorrowStrength: 'normal',
    allowMotifReuse: false,
    strictPartition: true,
  };
}

/**
 * Apply a FE recipe preset (preserve/regenerate + suggested borrow sources).
 * @param {string} presetId
 * @param {object} [policyState]
 * @returns {{ ok: true, policy: object, suggestedBorrow: Array<{dim: string, source: 'A'|'B'}> } | { ok: false, code: string }}
 */
export function applyConditioningPreset(presetId, policyState = emptyPolicyState()) {
  const preset = REFERENCE_CONDITIONING_PRESETS[presetId];
  if (!preset) {
    return { ok: false, code: 'reference_conditioning_unknown_preset' };
  }
  /** @type {Record<string, 'A'|'B'>} */
  const borrowSourceByDim = { ...(policyState.borrowSourceByDim || {}) };
  /** @type {Array<{dim: string, source: 'A'|'B'}>} */
  const suggestedBorrow = [];

  if (presetId === 'new_piece_texture_rhythm') {
    borrowSourceByDim.texture = 'A';
    borrowSourceByDim.rhythm = 'B';
    suggestedBorrow.push({ dim: 'texture', source: 'A' }, { dim: 'rhythm', source: 'B' });
  } else if (Array.isArray(preset.borrowHint)) {
    for (const dim of preset.borrowHint) {
      borrowSourceByDim[dim] = 'A';
      suggestedBorrow.push({ dim, source: 'A' });
    }
  }

  const policy = {
    ...policyState,
    preserve: normalizeDimList(preset.preserve),
    regenerate: normalizeDimList(preset.regenerate),
    borrowSourceByDim,
  };
  logger.debug('Conditioning preset applied', {
    presetId,
    preserveCount: policy.preserve.length,
    regenerateCount: policy.regenerate.length,
    suggestedBorrowCount: suggestedBorrow.length,
  });
  return { ok: true, policy, suggestedBorrow };
}

/**
 * Build borrowRows for generate/develop/edit from primary (A) + optional secondary (B).
 *
 * @param {object} args
 * @param {boolean} args.enabled
 * @param {string[]|null} args.dimensions union of borrow dims
 * @param {Record<string, 'A'|'B'>} [args.borrowSourceByDim]
 * @param {object|null} args.primary
 * @param {object|null} [args.primaryComposition]
 * @param {object|null} [args.secondary]
 * @param {object|null} [args.secondaryComposition]
 * @returns {{ rows: Array<object>, warnings: string[] }}
 */
export function collectMultiRefBorrowRows({
  enabled = false,
  dimensions = null,
  borrowSourceByDim = {},
  primary = null,
  primaryComposition = null,
  secondary = null,
  secondaryComposition = null,
} = {}) {
  const warnings = [];
  if (!enabled) {
    return { rows: [], warnings };
  }
  const dims = normalizeDimList(dimensions);
  if (!dims.length || !primary) {
    return { rows: [], warnings };
  }

  const bySource = { A: [], B: [] };
  for (const dim of dims) {
    const src = borrowSourceByDim?.[dim] === 'B' ? 'B' : 'A';
    bySource[src].push(dim);
  }

  const rows = [];
  const pushRow = (ref, composition, rowDims) => {
    if (!ref || !rowDims.length) return;
    rows.push({
      projectId: ref.projectId || ref.project_id || undefined,
      revisionId: ref.revisionId || ref.revision_id || undefined,
      composition: composition || ref.composition || undefined,
      scope: ref.scope || { kind: 'composition' },
      dimensions: rowDims,
    });
  };

  pushRow(primary, primaryComposition, bySource.A);

  if (bySource.B.length) {
    if (secondary) {
      pushRow(secondary, secondaryComposition, bySource.B);
    } else {
      warnings.push('reference_conditioning_secondary_missing');
      // Single-ref fallback: keep B-assigned dims on A so soft borrow still works.
      if (rows.length) {
        rows[0].dimensions = normalizeDimList([...rows[0].dimensions, ...bySource.B]);
      } else {
        pushRow(primary, primaryComposition, bySource.B);
      }
    }
  }

  return { rows, warnings };
}

/**
 * Validate disjoint preserve / borrow / regenerate. Does not require full registry cover.
 *
 * @param {{ preserve?: string[], regenerate?: string[], borrow?: string[] }} parts
 * @returns {{ ok: true } | { ok: false, code: string, message: string, overlaps?: object }}
 */
export function validateDisjointPartition(parts = {}) {
  const preserve = new Set(normalizeDimList(parts.preserve));
  const regenerate = new Set(normalizeDimList(parts.regenerate));
  const borrow = new Set(normalizeDimList(parts.borrow));

  const preserveBorrow = [...preserve].filter((d) => borrow.has(d));
  const preserveRegenerate = [...preserve].filter((d) => regenerate.has(d));
  const borrowRegenerate = [...borrow].filter((d) => regenerate.has(d));
  if (preserveBorrow.length || preserveRegenerate.length || borrowRegenerate.length) {
    return {
      ok: false,
      code: 'reference_conditioning_partition_overlap',
      message: 'A dimension cannot be both preserved, borrowed, and regenerated',
      overlaps: {
        preserve_borrow: preserveBorrow,
        preserve_regenerate: preserveRegenerate,
        borrow_regenerate: borrowRegenerate,
      },
    };
  }
  return { ok: true };
}

/**
 * Detect the same dimension borrowed on two reference rows.
 *
 * @param {Array<{ dimensions?: string[] }>} rows
 */
export function validateBorrowDimensionUniqueness(rows = []) {
  const seen = new Map();
  for (let index = 0; index < rows.length; index += 1) {
    const dims = normalizeDimList(rows[index]?.dimensions);
    for (const dim of dims) {
      if (seen.has(dim)) {
        return {
          ok: false,
          code: 'reference_conditioning_borrow_dimension_conflict',
          message: `Dimension "${dim}" is assigned to more than one reference`,
          dimension: dim,
          firstIndex: seen.get(dim),
          conflictIndex: index,
        };
      }
      seen.set(dim, index);
    }
  }
  return { ok: true };
}

/**
 * Build API ``reference_conditioning_policy`` + optional style_references payload.
 *
 * @param {object} args
 * @param {object} args.policyState
 * @param {Array<{ projectId?: string, revisionId?: string, composition?: object, scope?: object, dimensions?: string[] }>} args.borrowRows
 * @param {string|null} [args.activeProjectId]
 * @param {boolean} [args.includePolicy] when false, omit policy object (legacy mask-only)
 */
export function buildConditioningRequestFields({
  policyState,
  borrowRows = [],
  activeProjectId = null,
  includePolicy = true,
} = {}) {
  const state = policyState || emptyPolicyState();
  const preserve = normalizeDimList(state.preserve);
  const regenerate = normalizeDimList(state.regenerate);

  const styleReferences = [];
  const borrowDims = [];
  for (const row of borrowRows) {
    const mask = normalizeDimensionMask(row.dimensions ?? null);
    if (!mask.ok) {
      return { ok: false, code: mask.code, message: mask.message };
    }
    if (mask.dimensions == null || !mask.dimensions.length) {
      continue;
    }
    borrowDims.push(...mask.dimensions);
    const ref = {
      scope: row.scope || { kind: 'composition' },
      dimensions: mask.dimensions,
      mode: row.mode || 'prompt_features',
    };
    if (row.projectId) ref.project_id = row.projectId;
    if (row.revisionId) ref.revision_id = row.revisionId;
    if (row.composition) ref.composition = row.composition;
    if (row.expectedFingerprint) ref.expected_fingerprint = row.expectedFingerprint;
    styleReferences.push(ref);
  }

  const uniqueness = validateBorrowDimensionUniqueness(
    styleReferences.map((r) => ({ dimensions: r.dimensions })),
  );
  if (!uniqueness.ok) return uniqueness;

  const partition = validateDisjointPartition({
    preserve,
    regenerate,
    borrow: borrowDims,
  });
  if (!partition.ok) return partition;

  /** @type {Record<string, string>} */
  const dimensionStrengths = {};
  const strengthSource = state.dimensionStrengths || {};
  for (const dim of borrowDims) {
    if (Object.prototype.hasOwnProperty.call(strengthSource, dim)) {
      dimensionStrengths[dim] = normalizeStrength(strengthSource[dim]);
    }
  }

  const out = { ok: true, fields: {} };

  if (styleReferences.length === 1) {
    out.fields.style_reference = styleReferences[0];
  } else if (styleReferences.length > 1) {
    out.fields.style_references = styleReferences;
  }

  const hasPolicyIntent = includePolicy && (
    preserve.length
    || regenerate.length
    || Object.keys(dimensionStrengths).length
    || state.allowMotifReuse
    || styleReferences.length > 0
  );

  if (hasPolicyIntent) {
    out.fields.reference_conditioning_policy = {
      schema_version: 'reference.conditioning.policy.v1',
      preserve_dimensions: preserve,
      regenerate_dimensions: regenerate,
      dimension_strengths: dimensionStrengths,
      default_borrow_strength: normalizeStrength(state.defaultBorrowStrength, 'normal'),
      allow_motif_reuse: Boolean(state.allowMotifReuse),
      strict_partition: state.strictPartition !== false,
    };
  }

  if (state.allowMotifReuse || activeProjectId) {
    if (activeProjectId) {
      out.fields.active_project_id = String(activeProjectId);
    }
  }

  logger.debug('Built conditioning request fields', {
    styleReferenceCount: styleReferences.length,
    preserveCount: preserve.length,
    regenerateCount: regenerate.length,
    hasPolicy: Boolean(out.fields.reference_conditioning_policy),
    allowMotifReuse: Boolean(state.allowMotifReuse),
  });

  return out;
}

/**
 * Persist policy ids + strengths only (never compositions).
 * @param {object} state
 * @param {Storage} [storage=globalThis.localStorage]
 */
export function saveConditioningSession(state, storage = globalThis.localStorage) {
  try {
    if (!storage || typeof storage.setItem !== 'function') {
      return;
    }
    const payload = {
      preserve: normalizeDimList(state?.preserve),
      regenerate: normalizeDimList(state?.regenerate),
      dimensionStrengths: { ...(state?.dimensionStrengths || {}) },
      borrowSourceByDim: normalizeBorrowSources(state?.borrowSourceByDim),
      defaultBorrowStrength: normalizeStrength(state?.defaultBorrowStrength, 'normal'),
      allowMotifReuse: Boolean(state?.allowMotifReuse),
      strictPartition: state?.strictPartition !== false,
    };
    storage.setItem(REFERENCE_CONDITIONING_STORAGE_KEY, JSON.stringify(payload));
  } catch {
    // ignore quota / private mode
  }
}

/**
 * @param {Storage} [storage=globalThis.localStorage]
 */
export function loadConditioningSession(storage = globalThis.localStorage) {
  try {
    if (!storage || typeof storage.getItem !== 'function') {
      return emptyPolicyState();
    }
    const raw = storage.getItem(REFERENCE_CONDITIONING_STORAGE_KEY);
    if (!raw) return emptyPolicyState();
    const parsed = JSON.parse(raw);
    return {
      preserve: normalizeDimList(parsed?.preserve),
      regenerate: normalizeDimList(parsed?.regenerate),
      dimensionStrengths: parsed?.dimensionStrengths && typeof parsed.dimensionStrengths === 'object'
        ? parsed.dimensionStrengths
        : {},
      borrowSourceByDim: normalizeBorrowSources(parsed?.borrowSourceByDim),
      defaultBorrowStrength: normalizeStrength(parsed?.defaultBorrowStrength, 'normal'),
      allowMotifReuse: Boolean(parsed?.allowMotifReuse),
      strictPartition: parsed?.strictPartition !== false,
    };
  } catch {
    return emptyPolicyState();
  }
}

function normalizeBorrowSources(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return {};
  /** @type {Record<string, 'A'|'B'>} */
  const out = {};
  for (const [key, value] of Object.entries(raw)) {
    if (!REFERENCE_FEATURE_DIMENSION_IDS.includes(key)) continue;
    if (value === 'A' || value === 'B') out[key] = value;
  }
  return out;
}

function normalizeDimList(raw) {
  if (!Array.isArray(raw)) return [];
  const seen = new Set();
  const out = [];
  for (const item of raw) {
    const id = typeof item === 'string' ? item.trim() : '';
    if (!REFERENCE_FEATURE_DIMENSION_IDS.includes(id) || seen.has(id)) continue;
    seen.add(id);
    out.push(id);
  }
  return out;
}
