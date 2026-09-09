/**
 * Arrangement catalog/API contracts and topology-aware candidate verification.
 *
 * Preview candidates are ephemeral — never persist into project payloads.
 * Development verification still requires fixed topology; arrangement allows
 * authorized add/remove/reinstrument only when the manifest explains every track.
 */

import { canonicalizeValue } from './compositionCanonical.js';
import {
  compositionEditFingerprint,
  editFingerprintLogPrefix,
  EDIT_FINGERPRINT_PROFILE,
  eventFingerprint,
} from './compositionCandidates.js';
import { compileTimeline } from './compositionTimeline.js';
import { validateMotifDefinitions } from './compositionMotifs.js';
import { isCanonicalComposition, SUPPORTED_TRACK_ROLES } from './musicJsonValidation.js';
import { createAppLogger } from './appLogger.js';

const logger = createAppLogger('arrangementCandidates');

export { EDIT_FINGERPRINT_PROFILE, compositionEditFingerprint, editFingerprintLogPrefix };

export const ARRANGEMENT_ALGORITHM_VERSION = 'composition.arrangement.v1';
export const ARRANGEMENT_CATALOG_VERSION = 'arrangement.instruments.v1';
export const ARRANGEMENT_RANGE_POLICY_VERSION = 'arrangement.ranges.v1';

export const ARRANGEMENT_OPERATIONS = Object.freeze([
  'change_instrumentation',
  'add_accompaniment',
  'remove_accompaniment',
  'orchestrate_selected_tracks',
  'piano_to_ensemble',
  'simplify_arrangement',
  'increase_texture_density',
  'decrease_texture_density',
  'create_countermelody',
  'double_melody',
]);

export const ARRANGEMENT_ACCOMPANIMENT_ROLES = Object.freeze([
  'harmony',
  'pad',
  'rhythm',
  'bass',
]);

export const ARRANGEMENT_DOUBLING_POLICIES = Object.freeze([
  'none',
  'unison',
  'octave',
  'declared',
]);

export const AUTHORIZED_DOUBLING_POLICIES = Object.freeze([
  'unison',
  'octave',
  'declared',
]);

export const ARRANGEMENT_RANGE_ADJUSTMENTS = Object.freeze([
  'reject',
  'octave_shift_unprotected',
]);

export const ARRANGEMENT_MIN_CANDIDATE_COUNT = 1;
export const ARRANGEMENT_MAX_CANDIDATE_COUNT = 4;
export const ARRANGEMENT_MAX_INSTRUCTION_CHARS = 500;
export const ARRANGEMENT_MAX_SOURCE_TRACKS = 64;
export const ARRANGEMENT_MAX_PROTECTED_TRACKS = 64;
export const ARRANGEMENT_MAX_BEFORE_PARTS = 64;
export const ARRANGEMENT_MAX_AFTER_PARTS = 64;
export const ARRANGEMENT_MAX_PART_SOURCE_TRACKS = 32;
export const ARRANGEMENT_MAX_WARNINGS = 32;
export const ARRANGEMENT_MAX_ASSERTIONS = 64;
export const ARRANGEMENT_MAX_REJECTED_ATTEMPTS = 16;
export const ARRANGEMENT_MAX_REJECTED_REASONS = 8;
export const ARRANGEMENT_MAX_REASON_CHARS = 240;
export const ARRANGEMENT_MAX_RANGE_FINDINGS = 64;
export const ARRANGEMENT_MAX_DUPLICATE_FINDINGS = 64;
export const ARRANGEMENT_MAX_TARGET_PROFILE_FINGERPRINTS = 64;
export const ARRANGEMENT_MAX_TRACK_COUNT = 64;
export const ARRANGEMENT_MAX_MANIFEST_MAPPINGS = 256;

const OPERATIONS_REQUIRING_INSTRUMENTATION_CHANGE = new Set([
  'change_instrumentation',
  'add_accompaniment',
  'remove_accompaniment',
  'orchestrate_selected_tracks',
  'piano_to_ensemble',
  'create_countermelody',
  'double_melody',
]);

export const ARRANGEMENT_WARNING_CODES = Object.freeze([
  'candidate_failed_validation',
  'candidate_failed_preservation',
  'candidate_failed_range',
  'candidate_failed_density',
  'candidate_failed_duplicate',
  'candidate_repaired',
  'candidate_partial_success',
  'context_truncated',
  'empty_harmony_context',
  'questionable_range',
  'baseline_range_retained',
  'baseline_instrument_mismatch',
  'ambiguous_role',
  'mild_harmony_tension',
  'density_metric_advisory',
  'motif_occurrence_pruned',
  'declared_doubling_applied',
  'octave_adjustment_applied',
  'unlisted_after_allowed',
]);

export const ARRANGEMENT_REJECTED_STAGES = Object.freeze([
  'request',
  'context',
  'draft_validation',
  'realization',
  'preservation',
  'range',
  'density',
  'duplicate',
  'harmony',
  'identity',
  'repair',
  'provider',
]);

export const ARRANGEMENT_ASSERTION_KINDS = Object.freeze([
  'melody_preservation',
  'harmony_preservation',
  'protected_tracks',
  'unselected_tracks',
  'topology_authorization',
  'instrumentation_after',
  'source_note_cardinality',
  'density_direction',
  'range_policy',
  'motif_integrity',
  'audible_effect',
  'declared_doubling',
]);

const GLOBAL_METADATA_FIELDS = Object.freeze([
  'tempo',
  'key',
  'time_signature',
  'ticks_per_quarter',
  'bar_count',
  'duration_ticks',
  'sections',
  'tempo_changes',
  'time_signature_changes',
  'key_changes',
  'markers',
]);

const RANGE_FINDING_CODES = new Set([
  'absolute_out_of_range',
  'questionable_range',
  'baseline_range_retained',
  'unbounded_policy',
  'unknown_policy',
]);

const MAPPING_RELATIONSHIPS = new Set([
  'retained',
  'reinstrumented',
  'redistributed',
  'split',
  'merged',
  'doubled',
  'removed',
]);

/** @type {object | null} */
let catalogCache = null;

function fail(code, message) {
  return { ok: false, code, message };
}

function normalizeToken(value) {
  return String(value || '')
    .trim()
    .toLowerCase()
    .replace(/-/g, '_')
    .replace(/\s+/g, '_');
}

function isPianoInstrumentId(instrumentId) {
  return normalizeToken(instrumentId).includes('piano');
}

function uniqueNormalizedIds(values, fieldName) {
  if (!Array.isArray(values)) {
    return { ok: false, message: `${fieldName} must be an array` };
  }
  const cleaned = [];
  const seen = new Set();
  for (const raw of values) {
    if (typeof raw !== 'string') {
      return { ok: false, message: `${fieldName} must contain strings` };
    }
    const item = raw.trim();
    if (!item) {
      return { ok: false, message: `${fieldName} must not contain empty ids` };
    }
    if (seen.has(item)) {
      return { ok: false, message: `${fieldName} must be unique` };
    }
    seen.add(item);
    cleaned.push(item);
  }
  return { ok: true, ids: cleaned };
}

function partInventorySignature(part) {
  return JSON.stringify([
    part.instrument_id,
    part.role,
    part.doubling_policy,
    part.source_track_ids,
  ]);
}

function clipReason(text) {
  return String(text || '')
    .trim()
    .replace(/\s+/g, ' ')
    .slice(0, ARRANGEMENT_MAX_REASON_CHARS);
}

function deepCanonicalEqual(left, right) {
  return JSON.stringify(canonicalizeValue(left)) === JSON.stringify(canonicalizeValue(right));
}

function sortedEventPayloads(events) {
  return [...(events || [])]
    .map((event) => canonicalizeValue(event))
    .sort((a, b) => {
      const aStart = Number(a?.start_tick) || 0;
      const bStart = Number(b?.start_tick) || 0;
      if (aStart !== bStart) return aStart - bStart;
      const aPitch = String(a?.pitch || '');
      const bPitch = String(b?.pitch || '');
      if (aPitch !== bPitch) return aPitch.localeCompare(bPitch);
      const aDur = Number(a?.duration_ticks) || 0;
      const bDur = Number(b?.duration_ticks) || 0;
      if (aDur !== bDur) return aDur - bDur;
      return String(a?.id || '').localeCompare(String(b?.id || ''));
    });
}

function tracksByteEqual(left, right) {
  if (!left || !right) return false;
  const leftDump = { ...canonicalizeValue(left) };
  const rightDump = { ...canonicalizeValue(right) };
  const leftEvents = leftDump.events || [];
  const rightEvents = rightDump.events || [];
  delete leftDump.events;
  delete rightDump.events;
  if (!deepCanonicalEqual(leftDump, rightDump)) {
    return false;
  }
  return deepCanonicalEqual(sortedEventPayloads(leftEvents), sortedEventPayloads(rightEvents));
}

function globalMetadataEqual(source, candidate) {
  for (const field of GLOBAL_METADATA_FIELDS) {
    if (!deepCanonicalEqual(source?.[field] ?? null, candidate?.[field] ?? null)) {
      return false;
    }
  }
  return true;
}

function harmonyMetadataEqual(source, candidate) {
  return (
    deepCanonicalEqual(source?.harmony || [], candidate?.harmony || [])
    && source?.key === candidate?.key
    && deepCanonicalEqual(source?.key_changes || [], candidate?.key_changes || [])
  );
}

function emptyManifest() {
  return {
    retained_track_ids: [],
    removed_track_ids: [],
    added_track_ids: [],
    reordered_track_ids: [],
    reinstrumented_track_ids: [],
    split_track_ids: [],
    merged_track_ids: [],
    source_to_target: [],
  };
}

function emptyEventCounts() {
  return {
    copied: 0,
    moved: 0,
    generated: 0,
    removed: 0,
    octave_adjusted: 0,
    unchanged: 0,
  };
}

/**
 * Module-level selectable catalog cache (fingerprint + version gated).
 */
export function getCachedArrangementCatalog() {
  return catalogCache;
}

export function clearArrangementCatalogCache() {
  catalogCache = null;
}

export function cacheArrangementCatalog(catalog) {
  catalogCache = catalog;
  return catalogCache;
}

export function loadedCatalogFingerprintMatches(expectedFingerprint) {
  if (!catalogCache || typeof expectedFingerprint !== 'string') {
    return false;
  }
  return catalogCache.fingerprint === expectedFingerprint;
}

/**
 * Normalize GET /composition/arrangement/instruments response.
 * Does not mutate the inbound payload.
 */
export function normalizeArrangementCatalog(response) {
  if (!response || typeof response !== 'object' || Array.isArray(response)) {
    return fail('arrangement_catalog_unavailable', 'Empty arrangement catalog response');
  }
  if (typeof response.catalog_version !== 'string' || !response.catalog_version.trim()) {
    return fail('arrangement_catalog_unavailable', 'Missing catalog_version');
  }
  if (typeof response.range_policy_version !== 'string' || !response.range_policy_version.trim()) {
    return fail('arrangement_catalog_unavailable', 'Missing range_policy_version');
  }
  if (typeof response.fingerprint !== 'string' || response.fingerprint.length < 16) {
    return fail('arrangement_catalog_unavailable', 'Missing catalog fingerprint');
  }
  if (!Array.isArray(response.instruments) || response.instruments.length < 1) {
    return fail('arrangement_catalog_unavailable', 'Catalog instruments must be non-empty');
  }
  if (!Array.isArray(response.track_roles) || response.track_roles.length < 1) {
    return fail('arrangement_catalog_unavailable', 'Catalog track_roles must be non-empty');
  }

  const roleSet = new Set();
  const trackRoles = [];
  for (const role of response.track_roles) {
    if (typeof role !== 'string' || !role.trim()) {
      return fail('arrangement_catalog_unavailable', 'Invalid track role in catalog');
    }
    const normalized = normalizeToken(role);
    if (!SUPPORTED_TRACK_ROLES.has(normalized)) {
      return fail('arrangement_catalog_unavailable', `Unsupported track role: ${role}`);
    }
    if (!roleSet.has(normalized)) {
      roleSet.add(normalized);
      trackRoles.push(normalized);
    }
  }
  for (const required of SUPPORTED_TRACK_ROLES) {
    if (!roleSet.has(required)) {
      return fail(
        'arrangement_catalog_unavailable',
        `Catalog track_roles missing required role: ${required}`,
      );
    }
  }

  const ids = new Set();
  const instruments = [];
  for (let index = 0; index < response.instruments.length; index += 1) {
    const raw = response.instruments[index];
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
      return fail('arrangement_catalog_unavailable', `Instrument ${index} is not an object`);
    }
    const instrumentId = typeof raw.instrument_id === 'string' ? raw.instrument_id.trim() : '';
    if (!instrumentId) {
      return fail('arrangement_catalog_unavailable', `Instrument ${index} missing instrument_id`);
    }
    if (ids.has(instrumentId)) {
      return fail('arrangement_catalog_unavailable', `Duplicate instrument_id: ${instrumentId}`);
    }
    ids.add(instrumentId);
    if (typeof raw.display_name !== 'string' || !raw.display_name.trim()) {
      return fail('arrangement_catalog_unavailable', `Instrument ${instrumentId} missing display_name`);
    }
    if (!Number.isInteger(raw.midi_program) || raw.midi_program < 0 || raw.midi_program > 127) {
      return fail('arrangement_catalog_unavailable', `Instrument ${instrumentId} invalid midi_program`);
    }
    if (typeof raw.fingerprint !== 'string' || raw.fingerprint.length < 16) {
      return fail('arrangement_catalog_unavailable', `Instrument ${instrumentId} missing fingerprint`);
    }
    if (!['absolute', 'unbounded', 'unknown'].includes(raw.range_policy)) {
      return fail('arrangement_catalog_unavailable', `Instrument ${instrumentId} invalid range_policy`);
    }
    const suggested = Array.isArray(raw.suggested_roles)
      ? raw.suggested_roles.map((role) => normalizeToken(role)).filter((role) => SUPPORTED_TRACK_ROLES.has(role))
      : [];
    instruments.push({
      instrument_id: instrumentId,
      display_name: raw.display_name.trim(),
      aliases: Array.isArray(raw.aliases)
        ? raw.aliases.filter((item) => typeof item === 'string').map((item) => item.trim()).filter(Boolean)
        : [],
      midi_program: raw.midi_program,
      gm_family: typeof raw.gm_family === 'string' ? raw.gm_family.trim() : '',
      compatibility_identity: typeof raw.compatibility_identity === 'string'
        ? raw.compatibility_identity.trim()
        : '',
      compatibility_family: typeof raw.compatibility_family === 'string'
        ? raw.compatibility_family.trim()
        : '',
      is_drum: Boolean(raw.is_drum),
      range_policy: raw.range_policy,
      playable_low: raw.playable_low ?? null,
      playable_high: raw.playable_high ?? null,
      preferred_low: raw.preferred_low ?? null,
      preferred_high: raw.preferred_high ?? null,
      suggested_roles: suggested,
      fingerprint: raw.fingerprint,
    });
  }

  const catalog = {
    catalog_version: response.catalog_version.trim(),
    range_policy_version: response.range_policy_version.trim(),
    fingerprint: response.fingerprint,
    source_path_category: typeof response.source_path_category === 'string'
      ? response.source_path_category
      : 'packaged',
    instruments,
    track_roles: trackRoles,
    instrumentById: Object.freeze(
      Object.fromEntries(instruments.map((item) => [item.instrument_id, item])),
    ),
  };

  logger.debug('Arrangement catalog normalized', {
    catalogVersion: catalog.catalog_version,
    instrumentCount: catalog.instruments.length,
    roleCount: catalog.track_roles.length,
    fingerprintPrefix: editFingerprintLogPrefix(catalog.fingerprint),
  });

  return { ok: true, catalog };
}

export function normalizeArrangementPart(part, { side = 'after' } = {}) {
  if (!part || typeof part !== 'object' || Array.isArray(part)) {
    return fail('arrangement_inventory_mismatch', `instrumentation.${side} part is not an object`);
  }
  const partId = typeof part.part_id === 'string' ? part.part_id.trim() : '';
  const instrumentId = typeof part.instrument_id === 'string' ? part.instrument_id.trim() : '';
  if (!partId || !instrumentId) {
    return fail('arrangement_inventory_mismatch', `instrumentation.${side} requires part_id and instrument_id`);
  }
  let role = part.role ?? null;
  if (role != null) {
    if (typeof role !== 'string') {
      return fail('arrangement_invalid_operation', `instrumentation.${side} role must be a string`);
    }
    role = normalizeToken(role);
    if (!SUPPORTED_TRACK_ROLES.has(role)) {
      return fail('arrangement_invalid_operation', `Unsupported track role: ${part.role}`);
    }
  }
  const sourceIds = uniqueNormalizedIds(part.source_track_ids || [], 'source_track_ids');
  if (!sourceIds.ok) {
    return fail('arrangement_invalid_operation', sourceIds.message);
  }
  if (sourceIds.ids.length > ARRANGEMENT_MAX_PART_SOURCE_TRACKS) {
    return fail('arrangement_request_too_large', 'part source_track_ids exceeds limit');
  }
  const doubling = typeof part.doubling_policy === 'string'
    ? normalizeToken(part.doubling_policy)
    : 'none';
  if (!ARRANGEMENT_DOUBLING_POLICIES.includes(doubling)) {
    return fail('arrangement_invalid_operation', `Unsupported doubling_policy: ${part.doubling_policy}`);
  }
  return {
    ok: true,
    part: {
      part_id: partId,
      instrument_id: instrumentId,
      role,
      source_track_ids: sourceIds.ids,
      doubling_policy: doubling,
    },
  };
}

function normalizePartList(parts, side) {
  if (!Array.isArray(parts) || parts.length < 1) {
    return fail('arrangement_inventory_mismatch', `instrumentation.${side} requires at least one part`);
  }
  if (parts.length > (side === 'before' ? ARRANGEMENT_MAX_BEFORE_PARTS : ARRANGEMENT_MAX_AFTER_PARTS)) {
    return fail('arrangement_request_too_large', `instrumentation.${side} exceeds part limit`);
  }
  const normalized = [];
  const partIds = new Set();
  for (const raw of parts) {
    const result = normalizeArrangementPart(raw, { side });
    if (!result.ok) {
      return result;
    }
    if (partIds.has(result.part.part_id)) {
      return fail('arrangement_inventory_mismatch', `duplicate part_id in instrumentation.${side}`);
    }
    partIds.add(result.part.part_id);
    normalized.push(result.part);
  }
  return { ok: true, parts: normalized };
}

/**
 * Normalize every arrangement preview operation request and explicit part mapping.
 * Never mutates the inbound payload.
 */
export function normalizeArrangementRequest(payload) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    return fail('arrangement_invalid_operation', 'Arrangement request must be an object');
  }
  const operation = payload.operation;
  if (!ARRANGEMENT_OPERATIONS.includes(operation)) {
    return fail('arrangement_invalid_operation', 'Unsupported arrangement operation');
  }

  const source = uniqueNormalizedIds(payload.source_track_ids, 'source_track_ids');
  if (!source.ok) {
    return fail('arrangement_invalid_source', source.message);
  }
  if (source.ids.length < 1 || source.ids.length > ARRANGEMENT_MAX_SOURCE_TRACKS) {
    return fail('arrangement_invalid_source', 'source_track_ids must contain 1-64 ids');
  }

  const protectedIds = uniqueNormalizedIds(payload.protected_track_ids || [], 'protected_track_ids');
  if (!protectedIds.ok) {
    return fail('arrangement_invalid_source', protectedIds.message);
  }
  if (protectedIds.ids.length > ARRANGEMENT_MAX_PROTECTED_TRACKS) {
    return fail('arrangement_request_too_large', 'protected_track_ids exceeds limit');
  }

  const sourceSet = new Set(source.ids);
  const overlap = protectedIds.ids.filter((id) => sourceSet.has(id));
  if (overlap.length) {
    return fail(
      'arrangement_source_protected_overlap',
      'source_track_ids and protected_track_ids must not intersect',
    );
  }

  const composition = payload.composition;
  if (composition && typeof composition === 'object' && Array.isArray(composition.tracks)) {
    const trackIds = new Set(composition.tracks.map((track) => track?.id).filter(Boolean));
    for (const id of source.ids) {
      if (!trackIds.has(id)) {
        return fail('arrangement_invalid_source', `Unknown source track id: ${id}`);
      }
    }
    for (const id of protectedIds.ids) {
      if (!trackIds.has(id)) {
        return fail('arrangement_invalid_source', `Unknown protected track id: ${id}`);
      }
    }
  }

  const instrumentation = payload.instrumentation;
  if (!instrumentation || typeof instrumentation !== 'object') {
    return fail('arrangement_inventory_mismatch', 'instrumentation is required');
  }
  const before = normalizePartList(instrumentation.before, 'before');
  if (!before.ok) return before;
  const after = normalizePartList(instrumentation.after, 'after');
  if (!after.ok) return after;

  const beforeSigs = before.parts.map(partInventorySignature).toSorted();
  const afterSigs = after.parts.map(partInventorySignature).toSorted();
  if (
    OPERATIONS_REQUIRING_INSTRUMENTATION_CHANGE.has(operation)
    && beforeSigs.join('|') === afterSigs.join('|')
  ) {
    return fail(
      'arrangement_noop_instrumentation',
      'before and after instrumentation must differ for this operation',
    );
  }

  if (operation === 'remove_accompaniment') {
    for (const part of before.parts) {
      if (part.role != null && !ARRANGEMENT_ACCOMPANIMENT_ROLES.includes(part.role)) {
        return fail(
          'arrangement_invalid_accompaniment_role',
          'remove_accompaniment requires accompaniment-role before parts',
        );
      }
    }
  }

  if (operation === 'piano_to_ensemble') {
    if (!before.parts.some((part) => isPianoInstrumentId(part.instrument_id))) {
      return fail('arrangement_piano_required', 'piano_to_ensemble requires at least one piano in before');
    }
    const distinctAfter = new Set(after.parts.map((part) => part.instrument_id));
    if (distinctAfter.size < 2) {
      return fail(
        'arrangement_ensemble_targets_required',
        'piano_to_ensemble requires at least two distinct after instruments',
      );
    }
  }

  if (operation === 'create_countermelody') {
    if (!after.parts.some((part) => part.role === 'countermelody')) {
      return fail(
        'arrangement_countermelody_required',
        'create_countermelody requires at least one after part with role countermelody',
      );
    }
  }

  if (operation === 'double_melody') {
    const doublingParts = after.parts.filter(
      (part) => AUTHORIZED_DOUBLING_POLICIES.includes(part.doubling_policy),
    );
    if (doublingParts.length !== 1) {
      return fail(
        'arrangement_doubling_required',
        'double_melody requires exactly one authorized doubling relationship',
      );
    }
  }

  const candidateCount = Number(payload.candidate_count ?? 1);
  if (
    !Number.isInteger(candidateCount)
    || candidateCount < ARRANGEMENT_MIN_CANDIDATE_COUNT
    || candidateCount > ARRANGEMENT_MAX_CANDIDATE_COUNT
  ) {
    return fail('arrangement_invalid_operation', 'candidate_count must be 1-4');
  }

  const rangeAdjustment = payload.range_adjustment ?? 'reject';
  if (!ARRANGEMENT_RANGE_ADJUSTMENTS.includes(rangeAdjustment)) {
    return fail('arrangement_invalid_operation', 'Unsupported range_adjustment');
  }

  let instruction = payload.instruction ?? null;
  if (typeof instruction === 'string') {
    instruction = instruction.trim().replace(/\s+/g, ' ').slice(0, ARRANGEMENT_MAX_INSTRUCTION_CHARS) || null;
  } else {
    instruction = null;
  }

  const request = {
    composition: payload.composition,
    operation,
    source_track_ids: source.ids,
    protected_track_ids: protectedIds.ids,
    instrumentation: {
      before: before.parts,
      after: after.parts,
    },
    allow_unlisted_after: Boolean(payload.allow_unlisted_after),
    preserve_melody: payload.preserve_melody !== false,
    preserve_harmony: payload.preserve_harmony !== false,
    range_adjustment: rangeAdjustment,
    candidate_count: candidateCount,
    instruction,
    selection: payload.selection && typeof payload.selection === 'object'
      ? { ...payload.selection }
      : {},
    options: payload.options && typeof payload.options === 'object'
      ? { ...payload.options }
      : {},
  };

  return { ok: true, request };
}

function normalizeWarningCodes(codes) {
  if (!Array.isArray(codes)) {
    return [];
  }
  const cleaned = [];
  const seen = new Set();
  for (const item of codes) {
    if (typeof item !== 'string') continue;
    const code = item.trim();
    if (!code || seen.has(code)) continue;
    if (!ARRANGEMENT_WARNING_CODES.includes(code)) {
      return { ok: false, message: `Unknown arrangement warning code: ${code}` };
    }
    seen.add(code);
    cleaned.push(code);
    if (cleaned.length >= ARRANGEMENT_MAX_WARNINGS) break;
  }
  return { ok: true, codes: cleaned };
}

function normalizeIdList(values, fieldName) {
  if (!Array.isArray(values)) {
    return fail('arrangement_invalid_response', `${fieldName} must be an array`);
  }
  if (values.length > ARRANGEMENT_MAX_TRACK_COUNT) {
    return fail('arrangement_invalid_response', `${fieldName} exceeds track limit`);
  }
  const cleaned = [];
  const seen = new Set();
  for (const raw of values) {
    if (typeof raw !== 'string' || !raw.trim()) {
      return fail('arrangement_invalid_response', `${fieldName} contains invalid id`);
    }
    const id = raw.trim();
    if (seen.has(id)) continue;
    seen.add(id);
    cleaned.push(id);
  }
  return { ok: true, ids: cleaned };
}

function normalizeManifest(manifest) {
  const source = manifest && typeof manifest === 'object' ? manifest : {};
  const fields = [
    'retained_track_ids',
    'removed_track_ids',
    'added_track_ids',
    'reordered_track_ids',
    'reinstrumented_track_ids',
    'split_track_ids',
    'merged_track_ids',
  ];
  const normalized = emptyManifest();
  for (const field of fields) {
    const result = normalizeIdList(source[field] || [], field);
    if (!result.ok) return result;
    normalized[field] = result.ids;
  }
  const mappings = Array.isArray(source.source_to_target) ? source.source_to_target : [];
  if (mappings.length > ARRANGEMENT_MAX_MANIFEST_MAPPINGS) {
    return fail('arrangement_invalid_response', 'manifest source_to_target exceeds limit');
  }
  const sourceToTarget = [];
  for (const mapping of mappings) {
    if (!mapping || typeof mapping !== 'object') {
      return fail('arrangement_invalid_response', 'Invalid source_to_target mapping');
    }
    const sourceTrackId = typeof mapping.source_track_id === 'string'
      ? mapping.source_track_id.trim()
      : '';
    const targetTrackId = typeof mapping.target_track_id === 'string'
      ? mapping.target_track_id.trim()
      : '';
    if (!sourceTrackId || !MAPPING_RELATIONSHIPS.has(mapping.relationship)) {
      return fail('arrangement_invalid_response', 'Invalid source_to_target relationship');
    }
    if (mapping.relationship !== 'removed' && !targetTrackId) {
      return fail('arrangement_invalid_response', 'source_to_target missing target_track_id');
    }
    sourceToTarget.push({
      source_track_id: sourceTrackId,
      target_track_id: targetTrackId || sourceTrackId,
      relationship: mapping.relationship,
    });
  }
  normalized.source_to_target = sourceToTarget;
  return { ok: true, manifest: normalized };
}

function normalizeInventory(items, fieldName) {
  if (!Array.isArray(items)) {
    return fail('arrangement_invalid_response', `${fieldName} must be an array`);
  }
  if (items.length > ARRANGEMENT_MAX_TRACK_COUNT) {
    return fail('arrangement_invalid_response', `${fieldName} exceeds track limit`);
  }
  const normalized = [];
  for (const item of items) {
    if (!item || typeof item !== 'object') {
      return fail('arrangement_invalid_response', `${fieldName} item invalid`);
    }
    if (typeof item.track_id !== 'string' || !item.track_id.trim()) {
      return fail('arrangement_invalid_response', `${fieldName} missing track_id`);
    }
    if (!Number.isInteger(item.event_count) || item.event_count < 0) {
      return fail('arrangement_invalid_response', `${fieldName} invalid event_count`);
    }
    normalized.push({
      track_id: item.track_id.trim(),
      instrument: typeof item.instrument === 'string' ? item.instrument : '',
      role: typeof item.role === 'string' ? item.role : '',
      midi_program: item.midi_program ?? null,
      event_count: item.event_count,
      part_id: typeof item.part_id === 'string' ? item.part_id : null,
    });
  }
  return { ok: true, inventory: normalized };
}

function normalizeEventCounts(counts) {
  const source = counts && typeof counts === 'object' ? counts : {};
  const next = emptyEventCounts();
  for (const key of Object.keys(next)) {
    const value = Number(source[key] ?? 0);
    if (!Number.isInteger(value) || value < 0) {
      return fail('arrangement_invalid_response', `Invalid event_counts.${key}`);
    }
    next[key] = value;
  }
  return { ok: true, event_counts: next };
}

/**
 * Normalize a rejected attempt summary (never includes a composition).
 */
export function normalizeRejectedAttempt(attempt, index = 0) {
  if (!attempt || typeof attempt !== 'object' || Array.isArray(attempt)) {
    return fail('arrangement_invalid_response', `Rejected attempt ${index} is not an object`);
  }
  if (attempt.composition != null) {
    return fail('arrangement_invalid_response', 'Rejected attempts must not include compositions');
  }
  if (!Number.isInteger(attempt.ordinal) || attempt.ordinal < 1
    || attempt.ordinal > ARRANGEMENT_MAX_CANDIDATE_COUNT) {
    return fail('arrangement_invalid_response', `Rejected attempt ${index} has invalid ordinal`);
  }
  if (!ARRANGEMENT_REJECTED_STAGES.includes(attempt.stage)) {
    return fail('arrangement_invalid_response', `Rejected attempt ${index} has invalid stage`);
  }
  if (!Array.isArray(attempt.codes) || attempt.codes.length < 1) {
    return fail('arrangement_invalid_response', `Rejected attempt ${index} missing codes`);
  }
  const codes = [];
  const seen = new Set();
  for (const raw of attempt.codes) {
    if (typeof raw !== 'string') continue;
    const code = raw.trim();
    if (!code || seen.has(code) || code.length > 64) continue;
    seen.add(code);
    codes.push(code);
    if (codes.length >= ARRANGEMENT_MAX_REJECTED_REASONS) break;
  }
  if (!codes.length) {
    return fail('arrangement_invalid_response', `Rejected attempt ${index} requires stable codes`);
  }
  const reasons = Array.isArray(attempt.reasons)
    ? attempt.reasons
      .filter((item) => typeof item === 'string')
      .map(clipReason)
      .filter(Boolean)
      .slice(0, ARRANGEMENT_MAX_REJECTED_REASONS)
    : [];
  return {
    ok: true,
    attempt: {
      ordinal: attempt.ordinal,
      stage: attempt.stage,
      codes,
      reasons,
    },
  };
}

function normalizeAssertions(assertions) {
  if (!Array.isArray(assertions)) {
    return fail('arrangement_invalid_response', 'assertions must be an array');
  }
  if (assertions.length > ARRANGEMENT_MAX_ASSERTIONS) {
    return fail('arrangement_invalid_response', 'assertions exceed limit');
  }
  const normalized = [];
  for (const item of assertions) {
    if (!item || typeof item !== 'object') {
      return fail('arrangement_invalid_response', 'Invalid assertion entry');
    }
    if (!ARRANGEMENT_ASSERTION_KINDS.includes(item.kind)) {
      return fail('arrangement_invalid_response', `Unknown assertion kind: ${item.kind}`);
    }
    if (typeof item.satisfied !== 'boolean') {
      return fail('arrangement_invalid_response', 'Assertion missing satisfied boolean');
    }
    if (typeof item.detail !== 'string' || !item.detail.trim()) {
      return fail('arrangement_invalid_response', 'Assertion missing detail');
    }
    normalized.push({
      kind: item.kind,
      satisfied: item.satisfied,
      required: item.required !== false,
      detail: clipReason(item.detail),
      track_id: typeof item.track_id === 'string' ? item.track_id : null,
    });
  }
  return { ok: true, assertions: normalized };
}

function normalizeRangeFindings(findings) {
  if (!Array.isArray(findings)) {
    return fail('arrangement_invalid_response', 'range_findings must be an array');
  }
  if (findings.length > ARRANGEMENT_MAX_RANGE_FINDINGS) {
    return fail('arrangement_invalid_response', 'range_findings exceed limit');
  }
  const normalized = [];
  for (const item of findings) {
    if (!item || typeof item !== 'object') {
      return fail('arrangement_invalid_response', 'Invalid range finding');
    }
    if (!['error', 'warning', 'info'].includes(item.severity)) {
      return fail('arrangement_invalid_response', 'Invalid range finding severity');
    }
    if (!RANGE_FINDING_CODES.has(item.code)) {
      return fail('arrangement_invalid_response', `Unknown range finding code: ${item.code}`);
    }
    normalized.push({
      severity: item.severity,
      code: item.code,
      track_id: typeof item.track_id === 'string' ? item.track_id : null,
      instrument_id: typeof item.instrument_id === 'string' ? item.instrument_id : null,
      detail: clipReason(item.detail || item.code),
    });
  }
  return { ok: true, range_findings: normalized };
}

function validateCandidateContract(candidate, index, response) {
  if (!candidate || typeof candidate !== 'object' || Array.isArray(candidate)) {
    return `Candidate ${index} is not an object`;
  }
  if (typeof candidate.candidate_id !== 'string' || candidate.candidate_id.trim().length < 16) {
    return `Candidate ${index} missing candidate_id`;
  }
  if (typeof candidate.candidate_fingerprint !== 'string' || candidate.candidate_fingerprint.length < 16) {
    return `Candidate ${index} missing candidate_fingerprint`;
  }
  if (typeof candidate.edit_source_fingerprint !== 'string' || candidate.edit_source_fingerprint.length < 16) {
    return `Candidate ${index} missing edit_source_fingerprint`;
  }
  if (typeof candidate.catalog_fingerprint !== 'string' || candidate.catalog_fingerprint.length < 16) {
    return `Candidate ${index} missing catalog_fingerprint`;
  }
  if (!isCanonicalComposition(candidate.composition)) {
    return `Candidate ${index} composition is not canonical V2`;
  }
  if (candidate.edit_source_fingerprint !== response.edit_source_fingerprint) {
    return `Candidate ${index} edit_source_fingerprint mismatch`;
  }
  if (candidate.catalog_fingerprint !== response.catalog_fingerprint) {
    return `Candidate ${index} catalog_fingerprint mismatch`;
  }
  if (candidate.operation !== response.operation) {
    return `Candidate ${index} operation mismatch`;
  }
  if ((candidate.algorithm_version || ARRANGEMENT_ALGORITHM_VERSION) !== response.algorithm_version) {
    return `Candidate ${index} algorithm_version mismatch`;
  }
  if ((candidate.catalog_version || ARRANGEMENT_CATALOG_VERSION) !== response.catalog_version) {
    return `Candidate ${index} catalog_version mismatch`;
  }
  if (
    (candidate.range_policy_version || ARRANGEMENT_RANGE_POLICY_VERSION)
    !== response.range_policy_version
  ) {
    return `Candidate ${index} range_policy_version mismatch`;
  }
  return null;
}

/**
 * Normalize multi-candidate arrangement preview response.
 * Rejects malformed entries; never mutates the inbound response.
 */
export function normalizeArrangementPreviewResponse(response) {
  if (!response || typeof response !== 'object' || Array.isArray(response)) {
    return fail('arrangement_invalid_response', 'Empty arrangement preview response');
  }
  if (typeof response.edit_source_fingerprint !== 'string' || response.edit_source_fingerprint.length < 16) {
    return fail('arrangement_invalid_response', 'Missing edit_source_fingerprint');
  }
  if (typeof response.catalog_fingerprint !== 'string' || response.catalog_fingerprint.length < 16) {
    return fail('arrangement_invalid_response', 'Missing catalog_fingerprint');
  }
  if (!ARRANGEMENT_OPERATIONS.includes(response.operation)) {
    return fail('arrangement_invalid_response', 'Unsupported response operation');
  }
  if (
    !Number.isInteger(response.requested_candidate_count)
    || response.requested_candidate_count < ARRANGEMENT_MIN_CANDIDATE_COUNT
    || response.requested_candidate_count > ARRANGEMENT_MAX_CANDIDATE_COUNT
  ) {
    return fail('arrangement_invalid_response', 'Invalid requested_candidate_count');
  }

  const candidates = Array.isArray(response.candidates) ? response.candidates : [];
  const rejectedRaw = Array.isArray(response.rejected_attempts) ? response.rejected_attempts : [];
  if (candidates.length < 1 && rejectedRaw.length < 1) {
    return fail('arrangement_invalid_response', 'Response requires candidates and/or rejected_attempts');
  }
  if (candidates.length > ARRANGEMENT_MAX_CANDIDATE_COUNT) {
    return fail('arrangement_invalid_response', 'candidates exceed limit');
  }
  if (rejectedRaw.length > ARRANGEMENT_MAX_REJECTED_ATTEMPTS) {
    return fail('arrangement_invalid_response', 'rejected_attempts exceed limit');
  }

  const warningResult = normalizeWarningCodes(response.warning_codes || []);
  if (warningResult.ok === false) {
    return fail('arrangement_invalid_response', warningResult.message);
  }

  const algorithmVersion = typeof response.algorithm_version === 'string' && response.algorithm_version.trim()
    ? response.algorithm_version.trim()
    : ARRANGEMENT_ALGORITHM_VERSION;
  const catalogVersion = typeof response.catalog_version === 'string' && response.catalog_version.trim()
    ? response.catalog_version.trim()
    : ARRANGEMENT_CATALOG_VERSION;
  const rangePolicyVersion = typeof response.range_policy_version === 'string'
    && response.range_policy_version.trim()
    ? response.range_policy_version.trim()
    : ARRANGEMENT_RANGE_POLICY_VERSION;

  const responseShell = {
    edit_source_fingerprint: response.edit_source_fingerprint,
    catalog_fingerprint: response.catalog_fingerprint,
    operation: response.operation,
    algorithm_version: algorithmVersion,
    catalog_version: catalogVersion,
    range_policy_version: rangePolicyVersion,
  };

  const ids = new Set();
  const normalizedCandidates = [];
  for (let index = 0; index < candidates.length; index += 1) {
    const err = validateCandidateContract(candidates[index], index, responseShell);
    if (err) {
      return fail('arrangement_invalid_response', err);
    }
    const id = candidates[index].candidate_id.trim();
    if (ids.has(id)) {
      return fail('arrangement_invalid_response', 'Duplicate candidate_id');
    }
    ids.add(id);

    const manifest = normalizeManifest(candidates[index].manifest);
    if (!manifest.ok) return manifest;
    const beforeInventory = normalizeInventory(
      candidates[index].before_inventory || [],
      'before_inventory',
    );
    if (!beforeInventory.ok) return beforeInventory;
    const afterInventory = normalizeInventory(
      candidates[index].after_inventory || [],
      'after_inventory',
    );
    if (!afterInventory.ok) return afterInventory;
    const eventCounts = normalizeEventCounts(candidates[index].event_counts);
    if (!eventCounts.ok) return eventCounts;
    const assertions = normalizeAssertions(candidates[index].assertions || []);
    if (!assertions.ok) return assertions;
    const rangeFindings = normalizeRangeFindings(candidates[index].range_findings || []);
    if (!rangeFindings.ok) return rangeFindings;
    const candidateWarnings = normalizeWarningCodes(candidates[index].warning_codes || []);
    if (candidateWarnings.ok === false) {
      return fail('arrangement_invalid_response', candidateWarnings.message);
    }

    const profiles = Array.isArray(candidates[index].target_profile_fingerprints)
      ? candidates[index].target_profile_fingerprints
      : [];
    if (profiles.length > ARRANGEMENT_MAX_TARGET_PROFILE_FINGERPRINTS) {
      return fail('arrangement_invalid_response', 'target_profile_fingerprints exceed limit');
    }
    const targetProfiles = [];
    for (const profile of profiles) {
      if (!profile || typeof profile !== 'object') {
        return fail('arrangement_invalid_response', 'Invalid target profile fingerprint');
      }
      const instrumentId = typeof profile.instrument_id === 'string'
        ? profile.instrument_id.trim()
        : '';
      const fingerprint = typeof profile.profile_fingerprint === 'string'
        ? profile.profile_fingerprint
        : '';
      if (!instrumentId || fingerprint.length < 16) {
        return fail('arrangement_invalid_response', 'Invalid target profile fingerprint entry');
      }
      targetProfiles.push({ instrument_id: instrumentId, profile_fingerprint: fingerprint });
    }

    const duplicateFindings = Array.isArray(candidates[index].duplicate_findings)
      ? candidates[index].duplicate_findings.slice(0, ARRANGEMENT_MAX_DUPLICATE_FINDINGS)
      : [];

    normalizedCandidates.push({
      ...candidates[index],
      candidate_id: id,
      algorithm_version: candidates[index].algorithm_version || algorithmVersion,
      catalog_version: candidates[index].catalog_version || catalogVersion,
      range_policy_version: candidates[index].range_policy_version || rangePolicyVersion,
      composition: candidates[index].composition,
      before_inventory: beforeInventory.inventory,
      after_inventory: afterInventory.inventory,
      manifest: manifest.manifest,
      event_counts: eventCounts.event_counts,
      assertions: assertions.assertions,
      range_findings: rangeFindings.range_findings,
      duplicate_findings: duplicateFindings,
      target_profile_fingerprints: targetProfiles,
      warning_codes: candidateWarnings.codes,
      density: candidates[index].density && typeof candidates[index].density === 'object'
        ? candidates[index].density
        : null,
      harmony_compatibility: candidates[index].harmony_compatibility
        && typeof candidates[index].harmony_compatibility === 'object'
        ? candidates[index].harmony_compatibility
        : null,
    });
  }

  const rejectedAttempts = [];
  for (let index = 0; index < rejectedRaw.length; index += 1) {
    const result = normalizeRejectedAttempt(rejectedRaw[index], index);
    if (!result.ok) return result;
    rejectedAttempts.push(result.attempt);
  }

  const normalized = {
    edit_source_fingerprint: response.edit_source_fingerprint,
    algorithm_version: algorithmVersion,
    catalog_version: catalogVersion,
    range_policy_version: rangePolicyVersion,
    catalog_fingerprint: response.catalog_fingerprint,
    operation: response.operation,
    requested_candidate_count: response.requested_candidate_count,
    candidates: normalizedCandidates,
    rejected_attempts: rejectedAttempts,
    warning_codes: warningResult.codes,
    provider: typeof response.provider === 'string' ? response.provider : null,
    model: typeof response.model === 'string' ? response.model : null,
  };

  logger.debug('Arrangement preview response normalized', {
    operation: normalized.operation,
    catalogVersion: normalized.catalog_version,
    candidateCount: normalized.candidates.length,
    rejectedCount: normalized.rejected_attempts.length,
    warningCount: normalized.warning_codes.length,
    editSourcePrefix: editFingerprintLogPrefix(normalized.edit_source_fingerprint),
    catalogPrefix: editFingerprintLogPrefix(normalized.catalog_fingerprint),
  });

  return { ok: true, response: normalized };
}

export function findArrangementCandidateById(candidates, candidateId) {
  if (!Array.isArray(candidates) || typeof candidateId !== 'string') {
    return null;
  }
  return candidates.find((item) => item?.candidate_id === candidateId) || null;
}

/**
 * Independently recompute topology/event diffs from base → candidate.
 */
export function computeArrangementTopologyDiff(baseComposition, candidateComposition) {
  const baseTracks = baseComposition?.tracks || [];
  const candTracks = candidateComposition?.tracks || [];
  const baseById = new Map(baseTracks.map((track) => [track.id, track]));
  const candById = new Map(candTracks.map((track) => [track.id, track]));

  const retained = [];
  const removed = [];
  const added = [];
  const reinstrumented = [];
  const eventChanged = [];

  for (const [id, baseTrack] of baseById) {
    const candTrack = candById.get(id);
    if (!candTrack) {
      removed.push(id);
      continue;
    }
    const sameInstrument = baseTrack.instrument === candTrack.instrument
      && baseTrack.midi_program === candTrack.midi_program
      && Boolean(baseTrack.is_drum) === Boolean(candTrack.is_drum);
    const sameEvents = deepCanonicalEqual(
      sortedEventPayloads(baseTrack.events),
      sortedEventPayloads(candTrack.events),
    );
    if (!sameInstrument) {
      reinstrumented.push(id);
    } else {
      retained.push(id);
    }
    if (!sameEvents) {
      eventChanged.push(id);
    }
  }
  for (const id of candById.keys()) {
    if (!baseById.has(id)) {
      added.push(id);
    }
  }

  return {
    retained_track_ids: retained,
    removed_track_ids: removed,
    added_track_ids: added,
    reinstrumented_track_ids: reinstrumented,
    event_changed_track_ids: eventChanged,
  };
}

function inventoryAgreesWithComposition(inventory, composition, fieldName, failures) {
  const tracks = composition?.tracks || [];
  const byId = new Map(tracks.map((track) => [track.id, track]));
  if (inventory.length !== tracks.length) {
    failures.push({ code: 'inventory_count_mismatch', field: fieldName });
  }
  for (const item of inventory) {
    const track = byId.get(item.track_id);
    if (!track) {
      failures.push({ code: 'inventory_unknown_track', field: fieldName, track_id: item.track_id });
      continue;
    }
    if (item.event_count !== (track.events || []).length) {
      failures.push({
        code: 'inventory_event_count_mismatch',
        field: fieldName,
        track_id: item.track_id,
      });
    }
  }
}

function validateTopologyAgainstManifest({
  baseComposition,
  candidateComposition,
  manifest,
  sourceTrackIds,
  protectedTrackIds,
  failures,
}) {
  const sourceIds = new Set((baseComposition.tracks || []).map((track) => track.id));
  const candidateIds = new Set((candidateComposition.tracks || []).map((track) => track.id));
  const sourceSet = new Set(sourceTrackIds || []);
  const protectedSet = new Set(protectedTrackIds || []);

  const retained = new Set(manifest.retained_track_ids);
  const removed = new Set(manifest.removed_track_ids);
  const added = new Set(manifest.added_track_ids);
  const reinstrumented = new Set(manifest.reinstrumented_track_ids);
  const split = new Set(manifest.split_track_ids || []);
  const merged = new Set(manifest.merged_track_ids || []);

  if ([...retained].some((id) => removed.has(id))) {
    failures.push({ code: 'manifest_retained_removed_conflict' });
  }
  if ([...added].some((id) => removed.has(id))) {
    failures.push({ code: 'manifest_added_removed_conflict' });
  }

  for (const trackId of protectedSet) {
    if (removed.has(trackId) || split.has(trackId) || merged.has(trackId)) {
      failures.push({ code: 'protected_track_removed', track_id: trackId });
    }
    if (!candidateIds.has(trackId)) {
      failures.push({ code: 'protected_track_missing', track_id: trackId });
    }
  }

  // Every base and result track must be explained by the manifest.
  const explainedBase = new Set([
    ...retained,
    ...removed,
    ...reinstrumented,
    ...split,
    ...merged,
  ]);
  for (const trackId of sourceIds) {
    if (!explainedBase.has(trackId)) {
      failures.push({ code: 'unexplained_base_track', track_id: trackId });
    }
  }

  const explainedCandidate = new Set([...retained, ...added, ...reinstrumented]);
  for (const trackId of candidateIds) {
    if (!explainedCandidate.has(trackId)) {
      failures.push({ code: 'unexplained_candidate_track', track_id: trackId });
    }
  }

  for (const trackId of sourceIds) {
    if (candidateIds.has(trackId)) continue;
    if (!removed.has(trackId) && !split.has(trackId) && !merged.has(trackId)) {
      failures.push({ code: 'unexplained_removed_track', track_id: trackId });
    }
  }

  for (const trackId of removed) {
    if (candidateIds.has(trackId) && !reinstrumented.has(trackId)) {
      failures.push({ code: 'removed_track_still_present', track_id: trackId });
    }
    if (!sourceSet.has(trackId)) {
      failures.push({ code: 'unauthorized_removal', track_id: trackId });
    }
  }

  for (const trackId of reinstrumented) {
    if (!sourceSet.has(trackId) && !sourceIds.has(trackId)) {
      failures.push({ code: 'unauthorized_reinstrument', track_id: trackId });
    }
  }

  for (const trackId of added) {
    if (sourceIds.has(trackId) && !reinstrumented.has(trackId)) {
      failures.push({ code: 'added_track_was_source', track_id: trackId });
    }
  }

  for (const mapping of manifest.source_to_target) {
    if (mapping.relationship === 'removed') {
      if (!removed.has(mapping.source_track_id) && candidateIds.has(mapping.source_track_id)) {
        failures.push({
          code: 'mapping_removed_conflict',
          track_id: mapping.source_track_id,
        });
      }
      continue;
    }
    if (!candidateIds.has(mapping.target_track_id)) {
      failures.push({
        code: 'mapping_missing_target',
        track_id: mapping.target_track_id,
      });
    }
  }

  // Actual diffs must not invent unauthorized topology beyond the manifest.
  const actual = computeArrangementTopologyDiff(baseComposition, candidateComposition);
  for (const trackId of actual.added_track_ids) {
    if (!added.has(trackId) && !reinstrumented.has(trackId)) {
      failures.push({ code: 'actual_add_not_in_manifest', track_id: trackId });
    }
  }
  for (const trackId of actual.removed_track_ids) {
    if (!removed.has(trackId) && !split.has(trackId) && !merged.has(trackId)) {
      failures.push({ code: 'actual_remove_not_in_manifest', track_id: trackId });
    }
  }
  for (const trackId of actual.reinstrumented_track_ids) {
    if (!reinstrumented.has(trackId) && !added.has(trackId)) {
      failures.push({ code: 'actual_reinstrument_not_in_manifest', track_id: trackId });
    }
  }
}

/**
 * Independently verify a selected arrangement candidate before apply.
 * Recomputes fingerprints locally; does not trust response values alone.
 *
 * Unlike development verification, authorized topology changes are accepted
 * when every base/result track is explained by the manifest.
 */
export async function verifyArrangementCandidate({
  baseComposition,
  candidate,
  request,
  responseSourceFingerprint,
  loadedCatalog = null,
}) {
  const failures = [];
  if (!candidate || !baseComposition) {
    return { ok: false, failures: [{ code: 'missing_candidate' }] };
  }

  const catalog = loadedCatalog || catalogCache;
  const sourceTrackIds = request?.source_track_ids || [];
  const protectedTrackIds = request?.protected_track_ids || [];
  const sourceSet = new Set(sourceTrackIds);
  const protectedSet = new Set(protectedTrackIds);
  const unselectedIds = (baseComposition.tracks || [])
    .map((track) => track.id)
    .filter((id) => !sourceSet.has(id));

  const localSourceFp = await compositionEditFingerprint(baseComposition);
  if (responseSourceFingerprint && localSourceFp !== responseSourceFingerprint) {
    failures.push({ code: 'stale_edit_source_fingerprint' });
  }
  if (candidate.edit_source_fingerprint && localSourceFp !== candidate.edit_source_fingerprint) {
    failures.push({ code: 'stale_candidate_source_fingerprint' });
  }

  const localCandidateFp = await compositionEditFingerprint(candidate.composition);
  if (localCandidateFp !== candidate.candidate_fingerprint) {
    failures.push({ code: 'candidate_fingerprint_mismatch' });
  }

  if (catalog) {
    if (candidate.catalog_fingerprint && catalog.fingerprint !== candidate.catalog_fingerprint) {
      failures.push({ code: 'stale_catalog_fingerprint' });
    }
    const profileById = catalog.instrumentById || Object.fromEntries(
      (catalog.instruments || []).map((item) => [item.instrument_id, item]),
    );
    for (const profile of candidate.target_profile_fingerprints || []) {
      const local = profileById[profile.instrument_id];
      if (!local) {
        failures.push({
          code: 'unknown_target_profile',
          instrument_id: profile.instrument_id,
        });
        continue;
      }
      if (local.fingerprint !== profile.profile_fingerprint) {
        failures.push({
          code: 'target_profile_fingerprint_mismatch',
          instrument_id: profile.instrument_id,
        });
      }
    }
  } else if (candidate.catalog_fingerprint) {
    failures.push({ code: 'catalog_not_loaded' });
  }

  if (!globalMetadataEqual(baseComposition, candidate.composition)) {
    failures.push({ code: 'root_metadata_changed' });
  }
  if (request?.preserve_harmony !== false && !harmonyMetadataEqual(baseComposition, candidate.composition)) {
    failures.push({ code: 'harmony_metadata_changed' });
  }

  const baseById = new Map((baseComposition.tracks || []).map((track) => [track.id, track]));
  const candById = new Map((candidate.composition?.tracks || []).map((track) => [track.id, track]));

  for (const trackId of protectedSet) {
    const baseTrack = baseById.get(trackId);
    const candTrack = candById.get(trackId);
    if (!baseTrack || !candTrack || !tracksByteEqual(baseTrack, candTrack)) {
      failures.push({ code: 'protected_track_changed', track_id: trackId });
    }
  }

  for (const trackId of unselectedIds) {
    const baseTrack = baseById.get(trackId);
    const candTrack = candById.get(trackId);
    if (!baseTrack) continue;
    if (!candTrack || !tracksByteEqual(baseTrack, candTrack)) {
      failures.push({ code: 'unselected_track_changed', track_id: trackId });
    }
  }

  const manifest = candidate.manifest || emptyManifest();
  validateTopologyAgainstManifest({
    baseComposition,
    candidateComposition: candidate.composition,
    manifest,
    sourceTrackIds,
    protectedTrackIds,
    failures,
  });

  inventoryAgreesWithComposition(
    candidate.before_inventory || [],
    baseComposition,
    'before_inventory',
    failures,
  );
  inventoryAgreesWithComposition(
    candidate.after_inventory || [],
    candidate.composition,
    'after_inventory',
    failures,
  );

  const motifResult = validateMotifDefinitions(candidate.composition);
  if (!motifResult.valid) {
    failures.push({ code: 'motif_integrity_failed', message: motifResult.message });
  }

  const requiredAssertions = (candidate.assertions || []).filter(
    (item) => item && item.required !== false,
  );
  for (const assertion of requiredAssertions) {
    if (assertion.satisfied === false) {
      failures.push({ code: assertion.kind || 'required_assertion_failed' });
    }
  }

  // Absolute range errors are hard failures; questionable_range stays a warning.
  for (const finding of candidate.range_findings || []) {
    if (finding.severity === 'error' || finding.code === 'absolute_out_of_range') {
      failures.push({ code: 'range_hard_failure', finding_code: finding.code });
    }
  }

  try {
    compileTimeline(candidate.composition);
  } catch (error) {
    failures.push({ code: 'candidate_timeline_invalid', message: error.message });
  }

  if (!isCanonicalComposition(candidate.composition)) {
    failures.push({ code: 'candidate_not_canonical' });
  }

  logger.debug('Arrangement candidate verification finished', {
    operation: request?.operation || candidate.operation || null,
    ok: failures.length === 0,
    failureCount: failures.length,
    assertionCount: requiredAssertions.length,
    catalogPrefix: editFingerprintLogPrefix(candidate.catalog_fingerprint),
    candidatePrefix: editFingerprintLogPrefix(localCandidateFp),
    sourcePrefix: editFingerprintLogPrefix(localSourceFp),
  });

  return {
    ok: failures.length === 0,
    failures,
    localSourceFingerprint: localSourceFp,
    localCandidateFingerprint: localCandidateFp,
    topologyDiff: computeArrangementTopologyDiff(baseComposition, candidate.composition),
  };
}

/**
 * Apply-time entry point matching Task 9 deliverable signature.
 * @param {object} sourceComposition
 * @param {object} candidate
 * @param {{ catalog?: object|null, request?: object|null, responseSourceFingerprint?: string|null }} [options]
 */
export async function verifyArrangementCandidateForApply(
  sourceComposition,
  candidate,
  {
    catalog = null,
    request = null,
    responseSourceFingerprint = null,
  } = {},
) {
  return verifyArrangementCandidate({
    baseComposition: sourceComposition,
    candidate,
    request,
    responseSourceFingerprint,
    loadedCatalog: catalog,
  });
}

/**
 * Convenience helper: event fingerprints for a track (tests / diagnostics).
 */
export function trackEventFingerprints(track) {
  return (track?.events || []).map(eventFingerprint);
}
