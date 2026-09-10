/**
 * Shared AI candidate envelope helpers for preview-first workflows.
 * Operation-specific diagnostics stay outside this minimal contract.
 */

import {
  compositionEditFingerprint,
  editFingerprintLogPrefix,
} from './compositionCandidates.js';

export const AI_CANDIDATE_STATUS = Object.freeze({
  READY: 'ready',
  STALE: 'stale',
  APPLYING: 'applying',
  ERROR: 'error',
});

/**
 * @returns {string}
 */
export function makeAiCandidateId(prefix = 'ai') {
  const rand = typeof crypto !== 'undefined' && crypto.randomUUID
    ? crypto.randomUUID().replace(/-/g, '')
    : `${Date.now().toString(16)}${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${rand.slice(0, 16)}`;
}

/**
 * @param {object|null|undefined} composition
 * @returns {Promise<string>}
 */
export async function fingerprintCompositionOrNull(composition) {
  return compositionEditFingerprint(composition ?? null);
}

/**
 * Minimal shared candidate envelope. Operation-specific fields may be added by callers.
 * @param {object} params
 */
export function buildAiCandidateEnvelope({
  candidateId,
  operationType,
  composition,
  sourceFingerprint,
  candidateFingerprint,
  provider = null,
  model = null,
  instruction = null,
  warnings = [],
  declaredRanges = [],
  declaredTrackIds = [],
  musicXml = '',
  extras = {},
}) {
  return {
    candidate_id: candidateId,
    operation_type: operationType,
    status: AI_CANDIDATE_STATUS.READY,
    composition: composition ?? null,
    music_xml: musicXml || '',
    warnings: Array.isArray(warnings) ? warnings.slice(0, 32) : [],
    provider: provider || null,
    model: model || null,
    instruction: typeof instruction === 'string' && instruction.trim()
      ? instruction.trim().slice(0, 500)
      : null,
    source_fingerprint: sourceFingerprint,
    candidate_fingerprint: candidateFingerprint,
    declared_ranges: Array.isArray(declaredRanges) ? declaredRanges.slice(0, 64) : [],
    declared_track_ids: Array.isArray(declaredTrackIds) ? declaredTrackIds.slice(0, 64) : [],
    ...extras,
  };
}

/**
 * Capture request-scope IDs for stale response rejection.
 * @param {object} state
 */
export function captureAiRequestContext(state) {
  return {
    projectId: state.currentProjectId ?? null,
    branchId: state.activeBranchId ?? null,
    headRevisionId: state.currentRevisionId ?? null,
    workingVersion: state.workingVersion ?? null,
    workingFingerprint: state.workingFingerprint ?? null,
    compositionRevision: state.compositionRevision ?? null,
  };
}

/**
 * @param {object} capture
 * @param {object} state
 * @returns {{ stale: boolean, reason: string|null }}
 */
export function detectAiRequestStale(capture, state) {
  if (!capture) {
    return { stale: true, reason: 'missing_capture' };
  }
  if ((capture.projectId || null) !== (state.currentProjectId || null)) {
    return { stale: true, reason: 'project_changed' };
  }
  if (capture.projectId) {
    if ((capture.branchId || null) !== (state.activeBranchId || null)) {
      return { stale: true, reason: 'branch_changed' };
    }
    if ((capture.headRevisionId || null) !== (state.currentRevisionId || null)) {
      return { stale: true, reason: 'head_changed' };
    }
    if (capture.workingVersion != null && capture.workingVersion !== state.workingVersion) {
      return { stale: true, reason: 'working_version_changed' };
    }
  }
  if ((capture.compositionRevision || null) !== (state.compositionRevision || null)) {
    return { stale: true, reason: 'composition_edited' };
  }
  return { stale: false, reason: null };
}

export function aiCandidateLogFields(candidate) {
  if (!candidate) {
    return { candidateIdSuffix: null };
  }
  return {
    candidateIdSuffix: String(candidate.candidate_id || '').slice(-8),
    operationType: candidate.operation_type || null,
    provider: candidate.provider || null,
    model: candidate.model || null,
    warningCount: Array.isArray(candidate.warnings) ? candidate.warnings.length : 0,
    sourcePrefix: editFingerprintLogPrefix(candidate.source_fingerprint),
    candidatePrefix: editFingerprintLogPrefix(candidate.candidate_fingerprint),
    rangeCount: Array.isArray(candidate.declared_ranges) ? candidate.declared_ranges.length : 0,
    trackCount: Array.isArray(candidate.declared_track_ids) ? candidate.declared_track_ids.length : 0,
  };
}
