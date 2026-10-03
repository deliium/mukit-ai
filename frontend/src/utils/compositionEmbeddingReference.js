/**
 * Normalize helpers for musical-reference conditioning and similarity UI.
 *
 * Copy rules: "Musical reference" / "Similar material" only.
 * Similarity scores are affinity — not musical quality or artist style claims.
 */

import { listAnalysisSectionOptions } from './compositionAnalysis.js';
import { createAppLogger } from './appLogger.js';
import { normalizeDimensionMask } from './referenceFeatures.js';

const logger = createAppLogger('embeddingReference');

export const EMBEDDING_FINGERPRINT_PREFIX_LEN = 12;
export const STYLE_CONDITIONING_MODES = Object.freeze([
  'prompt_features',
  'tokenizer_labels',
  'vector_hint',
]);
export const DEFAULT_STYLE_CONDITIONING_MODE = 'prompt_features';
export const SIMILARITY_DEFAULT_TOP_K = 8;
export const SIMILARITY_MAX_TOP_K = 32;
export const RELATED_MOTIFS_DEFAULT_TOP_K = 8;

const EMBED_SCOPE_KINDS = new Set(['composition', 'section', 'motif', 'bar_range']);

/**
 * @param {string|null|undefined} fingerprint
 * @param {number} [length]
 */
export function fingerprintPrefix(fingerprint, length = EMBEDDING_FINGERPRINT_PREFIX_LEN) {
  if (typeof fingerprint !== 'string' || fingerprint.length === 0) {
    return null;
  }
  return fingerprint.slice(0, Math.max(1, length));
}

/**
 * @param {unknown} scope
 * @returns {{ ok: true, scope: object } | { ok: false, code: string, message: string }}
 */
export function normalizeEmbedScope(scope) {
  if (!scope || typeof scope !== 'object' || Array.isArray(scope)) {
    return { ok: false, code: 'embed_scope_invalid', message: 'Embed scope is required' };
  }
  const kind = scope.kind;
  if (!EMBED_SCOPE_KINDS.has(kind)) {
    return { ok: false, code: 'embed_scope_invalid', message: 'Unsupported embed scope kind' };
  }

  if (kind === 'composition') {
    return { ok: true, scope: { kind: 'composition' } };
  }

  if (kind === 'section') {
    const sectionIndex = Number(scope.section_index);
    if (!Number.isInteger(sectionIndex) || sectionIndex < 0) {
      return { ok: false, code: 'embed_scope_invalid', message: 'section_index must be >= 0' };
    }
    const next = { kind: 'section', section_index: sectionIndex };
    if (typeof scope.section_id === 'string' && scope.section_id.trim()) {
      next.section_id = scope.section_id.trim().slice(0, 120);
    }
    if (Number.isInteger(scope.expected_start_bar) && scope.expected_start_bar >= 1) {
      next.expected_start_bar = scope.expected_start_bar;
    }
    if (Number.isInteger(scope.expected_bar_count) && scope.expected_bar_count >= 1) {
      next.expected_bar_count = scope.expected_bar_count;
    }
    return { ok: true, scope: next };
  }

  if (kind === 'motif') {
    if (typeof scope.motif_id !== 'string' || !scope.motif_id.trim()) {
      return { ok: false, code: 'embed_scope_invalid', message: 'motif_id is required' };
    }
    const next = { kind: 'motif', motif_id: scope.motif_id.trim().slice(0, 120) };
    if (typeof scope.occurrence_id === 'string' && scope.occurrence_id.trim()) {
      next.occurrence_id = scope.occurrence_id.trim().slice(0, 120);
    }
    return { ok: true, scope: next };
  }

  // bar_range
  const startBar = Number(scope.start_bar);
  const endBar = Number(scope.end_bar);
  if (!Number.isInteger(startBar) || startBar < 1 || !Number.isInteger(endBar) || endBar < startBar) {
    return {
      ok: false,
      code: 'embed_scope_invalid',
      message: 'bar_range requires start_bar/end_bar with end_bar >= start_bar',
    };
  }
  const next = { kind: 'bar_range', start_bar: startBar, end_bar: endBar };
  if (typeof scope.track_id === 'string' && scope.track_id.trim()) {
    next.track_id = scope.track_id.trim().slice(0, 80);
  }
  return { ok: true, scope: next };
}

/**
 * Build a section embed scope from a section option (listAnalysisSectionOptions).
 * @param {{ index: number, id?: string|null, start_bar?: number|null, bar_count?: number|null }} option
 */
export function buildSectionEmbedScope(option) {
  if (!option || !Number.isInteger(option.index) || option.index < 0) {
    return { ok: false, code: 'embed_scope_invalid', message: 'Invalid section option' };
  }
  const scope = {
    kind: 'section',
    section_index: option.index,
  };
  if (typeof option.id === 'string' && option.id.trim()) {
    scope.section_id = option.id.trim().slice(0, 120);
  }
  if (Number.isInteger(option.start_bar) && option.start_bar >= 1) {
    scope.expected_start_bar = option.start_bar;
  }
  if (Number.isInteger(option.bar_count) && option.bar_count >= 1) {
    scope.expected_bar_count = option.bar_count;
  }
  return normalizeEmbedScope(scope);
}

/**
 * Current working scope for score-vs-reference / similarity query.
 * Prefers development bar range, else first matching section, else whole composition.
 */
export function buildCurrentEmbedScope({
  composition = null,
  sourceStartBar = null,
  sourceEndBar = null,
  sourceSectionKey = null,
} = {}) {
  if (
    Number.isInteger(sourceStartBar)
    && Number.isInteger(sourceEndBar)
    && sourceStartBar >= 1
    && sourceEndBar >= sourceStartBar
  ) {
    return normalizeEmbedScope({
      kind: 'bar_range',
      start_bar: sourceStartBar,
      end_bar: sourceEndBar,
    });
  }

  const options = listAnalysisSectionOptions(composition);
  if (sourceSectionKey && options.length) {
    const match = options.find((item) => item.key === sourceSectionKey);
    if (match) {
      return buildSectionEmbedScope(match);
    }
  }
  if (options.length === 1) {
    return buildSectionEmbedScope(options[0]);
  }
  return normalizeEmbedScope({ kind: 'composition' });
}

/**
 * @param {unknown} input
 * @returns {{ ok: true, styleReference: object } | { ok: false, code: string, message: string }}
 */
export function normalizeStyleReference(input) {
  if (input == null) {
    return { ok: true, styleReference: null };
  }
  if (!input || typeof input !== 'object' || Array.isArray(input)) {
    return { ok: false, code: 'style_reference_invalid', message: 'style_reference must be an object' };
  }

  const hasProject = typeof input.project_id === 'string' && input.project_id.trim();
  const hasComposition = input.composition != null && typeof input.composition === 'object';
  if (!hasProject && !hasComposition) {
    return {
      ok: false,
      code: 'style_reference_invalid',
      message: 'style_reference requires project_id or composition',
    };
  }

  const scopeResult = normalizeEmbedScope(input.scope);
  if (!scopeResult.ok) {
    return scopeResult;
  }

  const mode = input.mode || DEFAULT_STYLE_CONDITIONING_MODE;
  if (!STYLE_CONDITIONING_MODES.includes(mode)) {
    return { ok: false, code: 'style_reference_invalid', message: 'Unsupported conditioning mode' };
  }

  const styleReference = {
    scope: scopeResult.scope,
    mode,
  };
  if (hasProject) {
    styleReference.project_id = input.project_id.trim().slice(0, 80);
  }
  if (hasComposition) {
    styleReference.composition = input.composition;
  }
  if (typeof input.revision_id === 'string' && input.revision_id.trim()) {
    styleReference.revision_id = input.revision_id.trim().slice(0, 80);
  }
  if (typeof input.expected_fingerprint === 'string' && input.expected_fingerprint.length >= 16) {
    styleReference.expected_fingerprint = input.expected_fingerprint.slice(0, 128);
  }

  if (Object.prototype.hasOwnProperty.call(input, 'dimensions')) {
    const mask = normalizeDimensionMask(input.dimensions);
    if (!mask.ok) {
      return { ok: false, code: mask.code, message: mask.message };
    }
    if (mask.dimensions != null) {
      styleReference.dimensions = mask.dimensions;
    }
  }

  return { ok: true, styleReference };
}

/**
 * Build a development `style_reference` from ephemeral musicalReference session state.
 * @param {object|null} musicalReference
 */
export function buildStyleReferenceFromMusicalReference(musicalReference) {
  if (!musicalReference || typeof musicalReference !== 'object') {
    return { ok: true, styleReference: null };
  }
  if (!musicalReference.projectId && !musicalReference.composition) {
    return { ok: true, styleReference: null };
  }
  return normalizeStyleReference({
    project_id: musicalReference.projectId || undefined,
    revision_id: musicalReference.revisionId || undefined,
    composition: musicalReference.composition || undefined,
    scope: musicalReference.scope,
    expected_fingerprint: musicalReference.sourceFingerprint || undefined,
    mode: musicalReference.mode || DEFAULT_STYLE_CONDITIONING_MODE,
    dimensions: musicalReference.dimensions,
  });
}

/**
 * @param {unknown} hit
 */
export function normalizeSimilarityHit(hit) {
  if (!hit || typeof hit !== 'object' || Array.isArray(hit)) {
    return null;
  }
  const target = hit.target && typeof hit.target === 'object' ? hit.target : null;
  if (!target || !target.scope) {
    return null;
  }
  const scopeResult = normalizeEmbedScope(target.scope);
  if (!scopeResult.ok) {
    return null;
  }
  const score = Number(hit.score);
  if (!Number.isFinite(score)) {
    return null;
  }
  const fingerprint = typeof target.source_fingerprint === 'string'
    ? target.source_fingerprint
    : null;
  return {
    rank: Number.isInteger(hit.rank) && hit.rank >= 1 ? hit.rank : null,
    score,
    distance: Number.isFinite(Number(hit.distance)) ? Number(hit.distance) : null,
    projectId: typeof target.project_id === 'string' ? target.project_id : null,
    revisionId: typeof target.revision_id === 'string' ? target.revision_id : null,
    scope: scopeResult.scope,
    fingerprintPrefix: fingerprintPrefix(fingerprint),
    sourceFingerprint: fingerprint,
    modelId: typeof hit.model_id === 'string' ? hit.model_id : null,
    musicalQualityClaim: false,
  };
}

/**
 * @param {unknown} hits
 * @param {{ topK?: number }} [options]
 */
export function normalizeSimilarityHits(hits, { topK = SIMILARITY_DEFAULT_TOP_K } = {}) {
  if (!Array.isArray(hits)) {
    return [];
  }
  const limit = Math.min(
    SIMILARITY_MAX_TOP_K,
    Math.max(1, Number.isInteger(topK) ? topK : SIMILARITY_DEFAULT_TOP_K),
  );
  return hits
    .map(normalizeSimilarityHit)
    .filter(Boolean)
    .slice(0, limit);
}

/**
 * Session musical-reference record (ephemeral; never persisted).
 * @param {unknown} input
 */
export function normalizeMusicalReferenceSession(input) {
  if (input == null) {
    return null;
  }
  if (!input || typeof input !== 'object' || Array.isArray(input)) {
    return null;
  }
  const scopeResult = normalizeEmbedScope(input.scope);
  if (!scopeResult.ok) {
    return null;
  }
  const projectId = typeof input.projectId === 'string' && input.projectId.trim()
    ? input.projectId.trim().slice(0, 80)
    : null;
  const scoreRaw = input.scoreVsCurrent;
  const scoreVsCurrent = scoreRaw == null || scoreRaw === ''
    ? null
    : (Number.isFinite(Number(scoreRaw)) ? Number(scoreRaw) : null);

  return {
    projectId,
    projectName: typeof input.projectName === 'string' ? input.projectName.slice(0, 120) : null,
    revisionId: typeof input.revisionId === 'string' ? input.revisionId.slice(0, 80) : null,
    sectionIndex: Number.isInteger(input.sectionIndex) ? input.sectionIndex : null,
    sectionKey: typeof input.sectionKey === 'string' ? input.sectionKey : null,
    sectionLabel: typeof input.sectionLabel === 'string' ? input.sectionLabel.slice(0, 120) : null,
    scope: scopeResult.scope,
    fingerprintPrefix: fingerprintPrefix(input.sourceFingerprint || input.fingerprintPrefix),
    sourceFingerprint: typeof input.sourceFingerprint === 'string'
      && input.sourceFingerprint.length >= 16
      ? input.sourceFingerprint.slice(0, 128)
      : null,
    scoreVsCurrent,
    mode: STYLE_CONDITIONING_MODES.includes(input.mode)
      ? input.mode
      : DEFAULT_STYLE_CONDITIONING_MODE,
    dimensions: (() => {
      if (!Object.prototype.hasOwnProperty.call(input, 'dimensions')) {
        return undefined;
      }
      const mask = normalizeDimensionMask(input.dimensions);
      return mask.ok ? mask.dimensions : undefined;
    })(),
    referenceFeatureMaskEnabled: Boolean(input.referenceFeatureMaskEnabled),
    executionRuntime: typeof input.executionRuntime === 'string'
      ? input.executionRuntime.slice(0, 40)
      : null,
    executionDevice: typeof input.executionDevice === 'string'
      ? input.executionDevice.slice(0, 40)
      : null,
    fallbackReason: typeof input.fallbackReason === 'string'
      ? input.fallbackReason.slice(0, 80)
      : null,
  };
}

/**
 * Bounded cosine similarity for advisory UI (affinity only).
 * @param {number[]} a
 * @param {number[]} b
 * @returns {number|null}
 */
export function cosineSimilarity(a, b) {
  if (!Array.isArray(a) || !Array.isArray(b) || a.length === 0 || a.length !== b.length) {
    return null;
  }
  let dot = 0;
  let normA = 0;
  let normB = 0;
  for (let i = 0; i < a.length; i += 1) {
    const x = Number(a[i]);
    const y = Number(b[i]);
    if (!Number.isFinite(x) || !Number.isFinite(y)) {
      return null;
    }
    dot += x * y;
    normA += x * x;
    normB += y * y;
  }
  if (normA <= 0 || normB <= 0) {
    return null;
  }
  return dot / (Math.sqrt(normA) * Math.sqrt(normB));
}

/**
 * Format affinity score for UI. Never implies musical quality.
 * @param {number|null|undefined} score
 */
export function formatSimilarityScore(score) {
  if (score == null || !Number.isFinite(Number(score))) {
    return '—';
  }
  const value = Number(score);
  const pct = Math.round(Math.max(-1, Math.min(1, value)) * 100);
  return `${pct}% affinity`;
}

/**
 * Build similarity search query body (composition + scope + corpus).
 */
export function buildSimilarityQueryPayload({
  composition,
  scope,
  topK = SIMILARITY_DEFAULT_TOP_K,
  excludeProjectId = null,
  excludeSourceFingerprint = null,
  projectIds = null,
} = {}) {
  const scopeResult = normalizeEmbedScope(scope || { kind: 'composition' });
  if (!scopeResult.ok) {
    return scopeResult;
  }
  if (!composition || typeof composition !== 'object') {
    return { ok: false, code: 'similarity_query_invalid', message: 'composition required' };
  }
  const limit = Math.min(
    SIMILARITY_MAX_TOP_K,
    Math.max(1, Number.isInteger(topK) ? topK : SIMILARITY_DEFAULT_TOP_K),
  );
  const corpus = { kind: 'projects', include_composition_sections: true, include_motifs: false };
  if (Array.isArray(projectIds) && projectIds.length) {
    corpus.project_ids = projectIds
      .filter((id) => typeof id === 'string' && id.trim())
      .map((id) => id.trim().slice(0, 80))
      .slice(0, 500);
  }
  const query = {
    schema_version: 'composition.similarity_query.v1',
    composition,
    scope: scopeResult.scope,
    corpus,
    top_k: limit,
    distance_metric: 'cosine',
  };
  if (typeof excludeProjectId === 'string' && excludeProjectId.trim()) {
    query.exclude_project_id = excludeProjectId.trim().slice(0, 80);
  }
  if (typeof excludeSourceFingerprint === 'string' && excludeSourceFingerprint.length >= 16) {
    query.exclude_source_fingerprint = excludeSourceFingerprint.slice(0, 128);
  }
  logger.debug('Built similarity query', {
    scopeKind: scopeResult.scope.kind,
    topK: limit,
    excludeProjectId: query.exclude_project_id || null,
    projectIdCount: corpus.project_ids?.length || 0,
  });
  return { ok: true, query };
}

/**
 * Label a similarity hit for compact lists (no artist-style claims).
 * @param {ReturnType<typeof normalizeSimilarityHit>} hit
 * @param {{ projectNameById?: Record<string, string> }} [options]
 */
export function formatSimilarityHitLabel(hit, { projectNameById = {} } = {}) {
  if (!hit) {
    return 'Similar material';
  }
  const projectLabel = hit.projectId
    ? (projectNameById[hit.projectId] || `project ${hit.projectId.slice(0, 8)}`)
    : 'workspace';
  const scope = hit.scope || {};
  let scopeLabel = 'composition';
  if (scope.kind === 'section') {
    scopeLabel = typeof scope.section_id === 'string' && scope.section_id
      ? `section ${scope.section_id}`
      : `section #${scope.section_index ?? '?'}`;
  } else if (scope.kind === 'bar_range') {
    scopeLabel = `bars ${scope.start_bar}–${scope.end_bar}`;
  } else if (scope.kind === 'motif') {
    scopeLabel = `motif ${scope.motif_id}`;
  }
  return `${projectLabel} · ${scopeLabel}`;
}
