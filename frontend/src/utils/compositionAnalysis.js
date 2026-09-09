/**
 * Pure helpers for composition.analysis.v1 request scope, freshness, and section keys.
 * Analysis is derived UI state — never persisted as composition authority.
 */

import { canonicalizeValue } from './compositionCanonical.js';
import { barAtTick, compileTimeline } from './compositionTimeline.js';
import { findSectionContainingTick } from './compositionMotifs.js';

export const ANALYSIS_SCHEMA_VERSION = 'composition.analysis.v1';
export const ANALYSIS_SOURCE_SCHEMA_VERSION = 'composition.v2';
export const ANALYSIS_FINGERPRINT_PROFILE = 'analysis.source.v1';
export const ANALYSIS_SCOPE_KINDS = Object.freeze(['composition', 'section', 'track']);
export const ANALYSIS_DEBOUNCE_MS = 450;
export const ANALYSIS_REVISION_LOG_PREFIX_LEN = 48;
export const ANALYSIS_FINGERPRINT_LOG_PREFIX_LEN = 12;
export const ANALYSIS_MOTIF_STALE_FINGERPRINT = 'analysis_motif_fingerprint_stale';
export const ANALYSIS_MOTIF_STALE_INDEX = 'analysis_motif_index_stale';

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

function canonicalAnalysisJsonDumps(value) {
  return JSON.stringify(canonicalizeValue(value));
}

function projectAnalysisEvent(event) {
  if (!event || typeof event !== 'object') {
    return null;
  }
  const projected = {
    type: typeof event.type === 'string' ? event.type : 'note',
    id: typeof event.id === 'string' ? event.id : null,
    pitch: event.pitch ?? null,
    start_tick: Number(event.start_tick),
    duration_ticks: Number(event.duration_ticks),
    velocity: Number(event.velocity),
    staff: event.staff ?? null,
    voice: event.voice ?? null,
    articulations: Array.isArray(event.articulations) ? [...event.articulations] : [],
    tie: event.tie
      ? { group_id: event.tie.group_id, type: event.tie.type }
      : null,
  };
  return projected;
}

/**
 * Mirror backend analysis-relevant projection for source_fingerprint parity.
 */
export function analysisRelevantProjection(composition) {
  if (!composition || typeof composition !== 'object') {
    return { fingerprint_profile: ANALYSIS_FINGERPRINT_PROFILE };
  }
  return {
    fingerprint_profile: ANALYSIS_FINGERPRINT_PROFILE,
    schema_version: composition.schema_version,
    tempo: composition.tempo,
    key: composition.key,
    time_signature: composition.time_signature,
    ticks_per_quarter: composition.ticks_per_quarter,
    duration_ticks: composition.duration_ticks,
    bar_count: composition.bar_count,
    tempo_changes: (composition.tempo_changes || []).map((change) => ({
      tick: change.tick,
      bpm: change.bpm,
    })),
    time_signature_changes: (composition.time_signature_changes || []).map((change) => ({
      tick: change.tick,
      time_signature: change.time_signature,
    })),
    key_changes: (composition.key_changes || []).map((change) => ({
      tick: change.tick,
      key: change.key,
    })),
    sections: (composition.sections || []).map((section) => ({
      id: section.id ?? null,
      type: section.type,
      label: section.label ?? null,
      start_bar: section.start_bar,
      bar_count: section.bar_count,
      start_tick: section.start_tick,
      duration_ticks: section.duration_ticks,
    })),
    harmony: (composition.harmony || []).map((item) => ({
      bar: item.bar,
      chord: item.chord,
    })),
    tracks: (composition.tracks || []).map((track) => ({
      id: track.id,
      name: track.name,
      instrument: track.instrument,
      role: track.role,
      midi_program: track.midi_program,
      channel: track.channel,
      is_drum: track.is_drum ?? false,
      staff: track.staff ?? null,
      events: (track.events || []).map((event) => projectAnalysisEvent(event)).filter(Boolean),
    })),
  };
}

export async function compositionSourceFingerprint(composition) {
  const encoded = canonicalAnalysisJsonDumps(analysisRelevantProjection(composition));
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(encoded));
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('');
}

export function analysisFingerprintMatches(report, currentFingerprint) {
  const expected = typeof report?.source_fingerprint === 'string'
    ? report.source_fingerprint
    : '';
  const actual = typeof currentFingerprint === 'string' ? currentFingerprint : '';
  return Boolean(expected) && expected === actual;
}

function normalizeDetectedNoteReference(noteRef) {
  if (!noteRef || typeof noteRef !== 'object') {
    return null;
  }
  const eventIds = Array.isArray(noteRef.event_ids)
    ? noteRef.event_ids.filter((id) => typeof id === 'string' && id.trim())
    : null;
  const eventIndexes = Array.isArray(noteRef.event_indexes)
    ? noteRef.event_indexes.filter((index) => Number.isInteger(index) && index >= 0)
    : null;
  if ((!eventIds || !eventIds.length) && (!eventIndexes || !eventIndexes.length)) {
    return null;
  }
  return {
    eventIds: eventIds?.length ? eventIds : null,
    eventIndexes: eventIndexes?.length ? eventIndexes : null,
  };
}

/**
 * Resolve detected note references to canonical event IDs.
 * Event-index fallbacks are valid only when the analysis fingerprint matches.
 */
export function resolveDetectedNoteReferences(composition, noteRefs, {
  fingerprintMatches = false,
  trackId = null,
} = {}) {
  const refs = Array.isArray(noteRefs) ? noteRefs : [];
  const tracks = Array.isArray(composition?.tracks) ? composition.tracks : [];
  const track = trackId
    ? tracks.find((item) => item?.id === trackId)
    : null;
  const eventIds = [];
  let usedIndexFallback = false;

  for (const rawRef of refs) {
    const noteRef = normalizeDetectedNoteReference(rawRef);
    if (!noteRef) {
      return {
        eventIds: [],
        valid: false,
        stale: true,
        staleReason: ANALYSIS_MOTIF_STALE_INDEX,
      };
    }
    if (noteRef.eventIds?.length) {
      eventIds.push(...noteRef.eventIds);
      continue;
    }
    if (!fingerprintMatches) {
      return {
        eventIds: [],
        valid: false,
        stale: true,
        staleReason: ANALYSIS_MOTIF_STALE_FINGERPRINT,
      };
    }
    if (!track || !Array.isArray(track.events)) {
      return {
        eventIds: [],
        valid: false,
        stale: true,
        staleReason: ANALYSIS_MOTIF_STALE_INDEX,
      };
    }
    usedIndexFallback = true;
    for (const index of noteRef.eventIndexes) {
      const event = track.events[index];
      if (!event?.id) {
        return {
          eventIds: [],
          valid: false,
          stale: true,
          staleReason: ANALYSIS_MOTIF_STALE_INDEX,
        };
      }
      eventIds.push(event.id);
    }
  }

  return {
    eventIds,
    valid: eventIds.length > 0,
    stale: false,
    staleReason: usedIndexFallback ? null : null,
  };
}

function locationLabelsForSpan(composition, startTick, endTick) {
  const timeline = compileTimeline(composition);
  const sectionMatch = findSectionContainingTick(composition, startTick);
  const section = sectionMatch?.section ?? null;
  return {
    startTick,
    endTick,
    startBar: timeline ? barAtTick(timeline, startTick) : null,
    endBar: timeline ? barAtTick(timeline, Math.max(startTick, endTick - 1)) : null,
    sectionId: typeof section?.id === 'string' ? section.id : null,
    sectionType: typeof section?.type === 'string' ? section.type : null,
    sectionIndex: sectionMatch?.index ?? null,
    sectionLabel: section
      ? (typeof section.label === 'string' && section.label.trim()
        ? section.label.trim()
        : (typeof section.type === 'string' ? section.type : null))
      : null,
  };
}

function normalizeDetectedMotifOccurrence(composition, occurrence, {
  fingerprintMatches = false,
  familyId = null,
  familyLabel = null,
} = {}) {
  if (!occurrence || typeof occurrence !== 'object') {
    return null;
  }
  const resolved = resolveDetectedNoteReferences(composition, occurrence.notes, {
    fingerprintMatches,
    trackId: occurrence.track_id,
  });
  const truncated = Array.isArray(occurrence.notes)
    && Number.isInteger(occurrence.note_count)
    && occurrence.notes.length < occurrence.note_count;
  const location = locationLabelsForSpan(
    composition,
    occurrence.start_tick,
    occurrence.end_tick,
  );
  return {
    key: `detected:${familyId}:${occurrence.id}`,
    familyId,
    label: familyLabel,
    occurrenceId: occurrence.id,
    trackId: occurrence.track_id,
    eventIds: resolved.eventIds,
    relationship: occurrence.kind,
    identityScore: occurrence.identity_score,
    stale: !resolved.valid || resolved.stale,
    staleReason: resolved.staleReason || (truncated ? 'analysis_motif_truncated' : null),
    truncated,
    ...location,
  };
}

export function normalizeMotifFamilies(repetition) {
  if (!repetition || typeof repetition !== 'object') {
    return [];
  }
  const families = Array.isArray(repetition.motif_families) ? repetition.motif_families : [];
  return families
    .filter((family) => family && typeof family === 'object' && typeof family.id === 'string')
    .map((family) => ({
      id: family.id,
      noteCount: family.note_count,
      relationshipKinds: Array.isArray(family.relationship_kinds) ? [...family.relationship_kinds] : [],
      reference: family.reference && typeof family.reference === 'object' ? { ...family.reference } : null,
      matchedOccurrences: Array.isArray(family.matched_occurrences)
        ? family.matched_occurrences.map((item) => ({ ...item }))
        : [],
    }));
}

/**
 * Expand motif families into display-ready detected usages honoring fingerprint staleness.
 */
export function projectDetectedMotifUsages(composition, report, {
  currentFingerprint = null,
} = {}) {
  const repetition = report?.repetition;
  const families = normalizeMotifFamilies(repetition);
  if (!families.length) {
    return [];
  }
  const fingerprintMatches = currentFingerprint == null
    ? false
    : analysisFingerprintMatches(report, currentFingerprint);
  const usages = [];
  for (const family of families) {
    const label = family.relationshipKinds.length
      ? `Detected ${family.relationshipKinds.join('/')}`
      : 'Detected motif';
    const occurrences = [family.reference, ...family.matchedOccurrences].filter(Boolean);
    for (const occurrence of occurrences) {
      const projected = normalizeDetectedMotifOccurrence(composition, occurrence, {
        fingerprintMatches,
        familyId: family.id,
        familyLabel: label,
      });
      if (projected) {
        usages.push(projected);
      }
    }
  }
  console.debug('[compositionAnalysis] Projected detected motif usages', {
    familyCount: families.length,
    usageCount: usages.length,
    fingerprintMatches,
    fingerprintPrefix: fingerprintLogPrefix(report?.source_fingerprint),
  });
  return usages;
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
  const repetition = report.repetition && typeof report.repetition === 'object'
    ? {
      ...report.repetition,
      motifs: Array.isArray(report.repetition.motifs) ? report.repetition.motifs : [],
      motif_families: normalizeMotifFamilies(report.repetition),
      section_fingerprint_ids: Array.isArray(report.repetition.section_fingerprint_ids)
        ? report.repetition.section_fingerprint_ids
        : [],
    }
    : {
      motifs: [],
      motif_families: [],
      section_fingerprint_ids: [],
    };
  return {
    ...report,
    warnings: normalizeAnalysisWarnings(report.warnings),
    section_summaries: Array.isArray(report.section_summaries) ? report.section_summaries : [],
    repetition,
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
