/**
 * Pure helpers for composition.analysis.v1 request scope, freshness, and section keys.
 * Analysis is derived UI state — never persisted as composition authority.
 */

export const ANALYSIS_SCHEMA_VERSION = 'composition.analysis.v1';
export const ANALYSIS_SOURCE_SCHEMA_VERSION = 'composition.v2';
export const ANALYSIS_SCOPE_KINDS = Object.freeze(['composition', 'section', 'track']);
export const ANALYSIS_DEBOUNCE_MS = 450;
export const ANALYSIS_REVISION_LOG_PREFIX_LEN = 48;
export const ANALYSIS_FINGERPRINT_LOG_PREFIX_LEN = 12;

/**
 * Stable UI key for a canonical section. Prefers section.id; otherwise encodes
 * index + identity fields so ID-less V2 sections remain selectable.
 */
export function makeAnalysisSectionKey(section, index) {
  if (!section || typeof section !== 'object') {
    return null;
  }
  const idx = Number.isInteger(index) && index >= 0 ? index : null;
  if (typeof section.id === 'string' && section.id.trim()) {
    return `id:${section.id.trim()}`;
  }
  if (idx === null) {
    return null;
  }
  return [
    'idx',
    String(idx),
    String(section.type ?? ''),
    String(section.start_bar ?? ''),
    String(section.bar_count ?? ''),
    String(section.start_tick ?? ''),
    String(section.duration_ticks ?? ''),
  ].join(':');
}

export function listAnalysisSectionOptions(composition) {
  const sections = Array.isArray(composition?.sections) ? composition.sections : [];
  return sections.map((section, index) => {
    const key = makeAnalysisSectionKey(section, index);
    return {
      key,
      index,
      id: typeof section?.id === 'string' ? section.id : null,
      type: typeof section?.type === 'string' ? section.type : null,
      start_bar: section?.start_bar ?? null,
      bar_count: section?.bar_count ?? null,
      start_tick: section?.start_tick ?? null,
      duration_ticks: section?.duration_ticks ?? null,
      label: formatSectionOptionLabel(section, index),
    };
  }).filter((item) => Boolean(item.key));
}

function formatSectionOptionLabel(section, index) {
  const type = typeof section?.type === 'string' && section.type ? section.type : `section ${index}`;
  const start = Number.isFinite(Number(section?.start_bar)) ? Number(section.start_bar) : null;
  const count = Number.isFinite(Number(section?.bar_count)) ? Number(section.bar_count) : null;
  if (start != null && count != null) {
    const endInclusive = start + count - 1;
    return `${type} (bars ${start}–${endInclusive})`;
  }
  return type;
}

/**
 * Resolve a stored section key against the current composition.
 * Returns { section, index, key } or null when the selection cannot be recovered.
 */
export function findSectionByAnalysisKey(composition, sectionKey) {
  if (!sectionKey || typeof sectionKey !== 'string') {
    return null;
  }
  const sections = Array.isArray(composition?.sections) ? composition.sections : [];
  if (!sections.length) {
    return null;
  }

  if (sectionKey.startsWith('id:')) {
    const wantedId = sectionKey.slice(3);
    for (let index = 0; index < sections.length; index += 1) {
      const section = sections[index];
      if (typeof section?.id === 'string' && section.id === wantedId) {
        return { section, index, key: makeAnalysisSectionKey(section, index) };
      }
    }
    return null;
  }

  if (sectionKey.startsWith('idx:')) {
    const parts = sectionKey.split(':');
    // idx : index : type : start_bar : bar_count : start_tick : duration_ticks
    if (parts.length < 7) {
      return null;
    }
    const index = Number.parseInt(parts[1], 10);
    if (!Number.isInteger(index) || index < 0 || index >= sections.length) {
      return null;
    }
    const section = sections[index];
    const expected = {
      type: parts[2],
      start_bar: parts[3],
      bar_count: parts[4],
      start_tick: parts[5],
      duration_ticks: parts[6],
    };
    const matches = String(section?.type ?? '') === expected.type
      && String(section?.start_bar ?? '') === expected.start_bar
      && String(section?.bar_count ?? '') === expected.bar_count
      && String(section?.start_tick ?? '') === expected.start_tick
      && String(section?.duration_ticks ?? '') === expected.duration_ticks;
    if (!matches) {
      // Prefer exact identity; fall back only when index still points at a section
      // with matching bar bounds (type-only drift after edit).
      const boundsMatch = String(section?.start_bar ?? '') === expected.start_bar
        && String(section?.bar_count ?? '') === expected.bar_count
        && String(section?.start_tick ?? '') === expected.start_tick
        && String(section?.duration_ticks ?? '') === expected.duration_ticks;
      if (!boundsMatch) {
        return null;
      }
    }
    return { section, index, key: makeAnalysisSectionKey(section, index) };
  }

  return null;
}

/**
 * Keep the previous section selection when still resolvable; otherwise pick the first section.
 */
export function recoverAnalysisSectionKey(composition, previousKey = null) {
  const resolved = findSectionByAnalysisKey(composition, previousKey);
  if (resolved) {
    return resolved.key;
  }
  const options = listAnalysisSectionOptions(composition);
  return options[0]?.key ?? null;
}

export function normalizeAnalysisScopeKind(kind) {
  if (ANALYSIS_SCOPE_KINDS.includes(kind)) {
    return kind;
  }
  return 'composition';
}

/**
 * Build the backend request scope from UI state. Throws AnalysisScopeError on invalid targets.
 */
export function buildAnalysisRequestScope({
  analysisScope = 'composition',
  composition = null,
  sectionKey = null,
  trackId = null,
} = {}) {
  const kind = normalizeAnalysisScopeKind(analysisScope);
  if (kind === 'composition') {
    return { kind: 'composition' };
  }

  if (kind === 'track') {
    const id = typeof trackId === 'string' ? trackId.trim() : String(trackId || '').trim();
    if (!id) {
      throw new AnalysisScopeError('A track must be selected for track-scoped analysis', {
        code: 'analysis_invalid_scope',
        details: { reason: 'missing_track_id' },
      });
    }
    const tracks = Array.isArray(composition?.tracks) ? composition.tracks : [];
    const found = tracks.some((track) => String(track?.id) === id);
    if (!found) {
      throw new AnalysisScopeError('Selected track was not found in the composition', {
        code: 'analysis_invalid_scope',
        details: { reason: 'track_not_found' },
      });
    }
    return { kind: 'track', track_id: id };
  }

  // section
  const resolved = findSectionByAnalysisKey(composition, sectionKey);
  if (!resolved) {
    throw new AnalysisScopeError('Selected section was not found in the composition', {
      code: 'analysis_invalid_scope',
      details: { reason: 'section_not_found' },
    });
  }
  const { section, index } = resolved;
  const scope = {
    kind: 'section',
    section_index: index,
    expected_start_bar: Number(section.start_bar),
    expected_bar_count: Number(section.bar_count),
    expected_start_tick: Number(section.start_tick),
    expected_duration_ticks: Number(section.duration_ticks),
  };
  if (typeof section.id === 'string' && section.id.trim()) {
    scope.section_id = section.id.trim();
  }
  return scope;
}

/**
 * Normalize scope to only the active fields used for freshness / request-key equality.
 */
export function normalizeAnalysisScopeForKey(scope) {
  const kind = normalizeAnalysisScopeKind(scope?.kind);
  if (kind === 'composition') {
    return { kind: 'composition' };
  }
  if (kind === 'track') {
    return {
      kind: 'track',
      track_id: String(scope?.track_id || '').trim(),
    };
  }
  const normalized = {
    kind: 'section',
    section_index: Number(scope?.section_index),
  };
  if (typeof scope?.section_id === 'string' && scope.section_id.trim()) {
    normalized.section_id = scope.section_id.trim();
  }
  for (const field of [
    'expected_start_bar',
    'expected_bar_count',
    'expected_start_tick',
    'expected_duration_ticks',
  ]) {
    if (scope?.[field] != null && Number.isFinite(Number(scope[field]))) {
      normalized[field] = Number(scope[field]);
    }
  }
  return normalized;
}

/**
 * Freshness / in-flight identity: analysis contract + full compositionRevision + normalized scope.
 */
export function buildAnalysisRequestKey({
  compositionRevision,
  scope,
  analysisSchemaVersion = ANALYSIS_SCHEMA_VERSION,
} = {}) {
  const revision = compositionRevision == null || compositionRevision === ''
    ? 'empty'
    : String(compositionRevision);
  const normalizedScope = normalizeAnalysisScopeForKey(scope);
  return JSON.stringify({
    analysis_schema_version: analysisSchemaVersion,
    composition_revision: revision,
    scope: normalizedScope,
  });
}

export function analysisRequestKeysEqual(left, right) {
  return Boolean(left) && Boolean(right) && left === right;
}

export function revisionLogPrefix(revision) {
  const value = String(revision || '');
  if (!value || value === 'empty') {
    return value || 'empty';
  }
  return value.slice(0, ANALYSIS_REVISION_LOG_PREFIX_LEN);
}

export function fingerprintLogPrefix(fingerprint) {
  return String(fingerprint || '').slice(0, ANALYSIS_FINGERPRINT_LOG_PREFIX_LEN);
}

export function sanitizeScopeForLog(scope) {
  const normalized = normalizeAnalysisScopeForKey(scope);
  if (normalized.kind === 'composition') {
    return { kind: 'composition' };
  }
  if (normalized.kind === 'track') {
    return { kind: 'track', hasTrackId: Boolean(normalized.track_id) };
  }
  return {
    kind: 'section',
    section_index: normalized.section_index,
    hasSectionId: Boolean(normalized.section_id),
  };
}

export class AnalysisScopeError extends Error {
  constructor(message, { code = 'analysis_invalid_scope', details = null } = {}) {
    super(message);
    this.name = 'AnalysisScopeError';
    this.code = code;
    this.details = details;
  }
}

/**
 * Validate top-level composition.analysis.v1 report contract (lightweight client gate).
 */
export function validateAnalysisReportContract(report) {
  if (!report || typeof report !== 'object' || Array.isArray(report)) {
    return { valid: false, message: 'Analysis report must be an object' };
  }
  if (report.schema_version !== ANALYSIS_SCHEMA_VERSION) {
    return {
      valid: false,
      message: `Unexpected analysis schema_version: ${report.schema_version ?? 'missing'}`,
    };
  }
  if (typeof report.algorithm_version !== 'string' || !report.algorithm_version.trim()) {
    return { valid: false, message: 'Analysis report is missing algorithm_version' };
  }
  if (report.source_schema_version !== ANALYSIS_SOURCE_SCHEMA_VERSION) {
    return {
      valid: false,
      message: `Unexpected source_schema_version: ${report.source_schema_version ?? 'missing'}`,
    };
  }
  if (typeof report.source_fingerprint !== 'string' || report.source_fingerprint.length < 16) {
    return { valid: false, message: 'Analysis report is missing source_fingerprint' };
  }
  if (typeof report.status !== 'string' || !report.status.trim()) {
    return { valid: false, message: 'Analysis report is missing status' };
  }
  if (!report.resolved_scope || typeof report.resolved_scope !== 'object') {
    return { valid: false, message: 'Analysis report is missing resolved_scope' };
  }
  if (!ANALYSIS_SCOPE_KINDS.includes(report.resolved_scope.kind)) {
    return { valid: false, message: 'Analysis report resolved_scope.kind is invalid' };
  }
  return { valid: true, message: '' };
}

export function normalizeAnalysisWarnings(warnings) {
  if (!Array.isArray(warnings)) {
    return [];
  }
  return warnings
    .filter((item) => item && typeof item === 'object' && typeof item.code === 'string')
    .map((item) => ({
      code: item.code,
      severity: typeof item.severity === 'string' ? item.severity : 'warning',
      category: typeof item.category === 'string' ? item.category : 'data_quality',
      message: typeof item.message === 'string' ? item.message : item.code,
      locator: item.locator && typeof item.locator === 'object' ? item.locator : null,
      details: item.details && typeof item.details === 'object' && !Array.isArray(item.details)
        ? item.details
        : {},
    }));
}

/**
 * Normalize optional top-level arrays and warning list; preserve other report fields.
 */
export function normalizeAnalysisReport(report) {
  const contract = validateAnalysisReportContract(report);
  if (!contract.valid) {
    throw new Error(contract.message);
  }
  return {
    ...report,
    warnings: normalizeAnalysisWarnings(report.warnings),
    section_summaries: Array.isArray(report.section_summaries) ? report.section_summaries : [],
  };
}

export function analysisWarningCodes(warnings) {
  return normalizeAnalysisWarnings(warnings).map((item) => item.code);
}

/**
 * Derive whether a retained result matches the desired revision+scope key.
 */
export function deriveAnalysisFreshness({
  analysisResult,
  analysisResultKey,
  desiredRequestKey,
  analysisStatus,
} = {}) {
  const hasResult = Boolean(analysisResult) && Boolean(analysisResultKey);
  const isCurrent = hasResult && analysisRequestKeysEqual(analysisResultKey, desiredRequestKey);
  const isStale = hasResult && !isCurrent;
  return {
    hasResult,
    isCurrent,
    isStale,
    isLoading: analysisStatus === 'loading',
    desiredRequestKey: desiredRequestKey || null,
  };
}
