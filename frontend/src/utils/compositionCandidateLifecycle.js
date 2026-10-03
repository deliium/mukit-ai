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

/** Matches backend `project_history_schemas.WARNING_CODE_MAX_*`. */
export const HISTORY_AI_WARNING_CODE_MAX_LEN = 80;
export const HISTORY_AI_WARNING_CODE_MAX_COUNT = 32;

/**
 * Normalize candidate display warnings into durable history `warning_codes`.
 * Freeform LLM prose and `code: message` projection strings must not 422 commit.
 *
 * @param {unknown} warnings
 * @returns {string[]}
 */
export function toHistoryAiWarningCodes(warnings) {
  const out = [];
  const seen = new Set();
  if (!Array.isArray(warnings)) {
    return out;
  }
  for (const item of warnings) {
    let raw = '';
    if (typeof item === 'string') {
      raw = item;
    } else if (item && typeof item === 'object') {
      raw = item.code || item.message || '';
    }
    raw = String(raw || '').trim();
    if (!raw) {
      continue;
    }

    let code = raw;
    const coded = raw.match(/^([a-z][a-z0-9_]{0,79})\s*:/i);
    if (coded) {
      code = coded[1].toLowerCase();
    } else if (/\s/.test(raw) || raw.length > HISTORY_AI_WARNING_CODE_MAX_LEN) {
      // Keep provenance without storing freeform sentences as codes.
      if (/fake\s+llm/i.test(raw)) {
        code = 'fake_llm_mode';
      } else {
        code = raw
          .toLowerCase()
          .replace(/[^a-z0-9_]+/g, '_')
          .replace(/^_+|_+$/g, '')
          .slice(0, HISTORY_AI_WARNING_CODE_MAX_LEN);
      }
    }

    if (!code || code.length > HISTORY_AI_WARNING_CODE_MAX_LEN) {
      continue;
    }
    if (seen.has(code)) {
      continue;
    }
    seen.add(code);
    out.push(code);
    if (out.length >= HISTORY_AI_WARNING_CODE_MAX_COUNT) {
      break;
    }
  }
  return out;
}

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
  modelId = null,
  modelVersion = null,
  runtime = null,
  capability = null,
  operation = null,
  generationParameters = null,
  requestedModelId = null,
  resolvedModelId = null,
  fallbackApplied = false,
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
    model_id: modelId || resolvedModelId || null,
    model_version: modelVersion || null,
    runtime: runtime || null,
    capability: capability || null,
    operation: operation || operationType || null,
    generation_parameters: generationParameters && typeof generationParameters === 'object'
      ? generationParameters
      : null,
    requested_model_id: requestedModelId || null,
    resolved_model_id: resolvedModelId || modelId || null,
    fallback_applied: Boolean(fallbackApplied),
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
    modelId: candidate.model_id || candidate.resolved_model_id || null,
    runtime: candidate.runtime || null,
    fallbackApplied: Boolean(candidate.fallback_applied),
    warningCount: Array.isArray(candidate.warnings) ? candidate.warnings.length : 0,
    sourcePrefix: editFingerprintLogPrefix(candidate.source_fingerprint),
    candidatePrefix: editFingerprintLogPrefix(candidate.candidate_fingerprint),
    rangeCount: Array.isArray(candidate.declared_ranges) ? candidate.declared_ranges.length : 0,
    trackCount: Array.isArray(candidate.declared_track_ids) ? candidate.declared_track_ids.length : 0,
  };
}

/**
 * Map API/candidate fields into durable `AiProvenance` for history commit.
 * @param {object|null|undefined} candidate
 * @param {object} [overrides]
 */
export function buildHistoryAiProvenance(candidate, overrides = {}) {
  const src = candidate && typeof candidate === 'object' ? candidate : {};
  return {
    provider: src.provider || null,
    model: src.model || null,
    model_id: src.model_id || src.resolved_model_id || null,
    model_version: src.model_version || null,
    runtime: src.runtime || null,
    capability: src.capability || null,
    operation: src.operation || src.operation_type || null,
    generation_parameters: src.generation_parameters && typeof src.generation_parameters === 'object'
      ? src.generation_parameters
      : null,
    user_instruction: src.instruction || src.user_instruction || undefined,
    candidate_id: src.candidate_id || undefined,
    candidate_fingerprint: src.candidate_fingerprint || undefined,
    warning_codes: toHistoryAiWarningCodes(src.warnings),
    ...overrides,
  };
}

/**
 * Build session `generationMeta` from a generate/apply candidate (durable fields).
 * Prefer backend `generation.provenance.v1` in generation_parameters; mirror pipeline/seed.
 * @param {object|null|undefined} candidate
 * @param {object|null|undefined} promptSnapshot
 */
export function buildGenerationMetaFromCandidate(candidate, promptSnapshot = null) {
  const src = candidate && typeof candidate === 'object' ? candidate : {};
  const fromApi = src.generation_parameters && typeof src.generation_parameters === 'object'
    ? { ...src.generation_parameters }
    : {};
  if (src.pipeline_id && !fromApi.pipeline_id) {
    fromApi.pipeline_id = src.pipeline_id;
  }
  if (src.seed != null && fromApi.seed == null) {
    fromApi.seed = src.seed;
  }
  if (Array.isArray(src.stages) && src.stages.length && !Array.isArray(fromApi.stages)) {
    fromApi.stages = src.stages;
  }
  if (!fromApi.provenance_schema && (fromApi.pipeline_id || fromApi.stages)) {
    fromApi.provenance_schema = 'generation.provenance.v1';
  }
  const stageModelIds = Array.isArray(fromApi.stages)
    ? fromApi.stages.map((stage) => stage?.model_id).filter(Boolean)
    : [];
  return {
    provider: src.provider || null,
    model: src.model || null,
    model_id: src.model_id || src.resolved_model_id || null,
    model_version: src.model_version || null,
    runtime: src.runtime || null,
    capability: src.capability || null,
    operation: src.operation || src.operation_type || null,
    generation_parameters: Object.keys(fromApi).length ? fromApi : null,
    prompt: promptSnapshot || src.prompt || null,
    pipeline_id: fromApi.pipeline_id || src.pipeline_id || null,
    seed: fromApi.seed ?? src.seed ?? null,
    stage_model_ids: stageModelIds,
  };
}

/**
 * Compact read-only provenance line for Versions UI.
 * @param {object|null|undefined} summary
 * @returns {string|null}
 */
export function formatRevisionProvenanceSummary(summary) {
  if (!summary || typeof summary !== 'object') {
    return null;
  }
  const gp = summary.generation_parameters && typeof summary.generation_parameters === 'object'
    ? summary.generation_parameters
    : summary;
  const pipeline = gp.pipeline_id || null;
  const seed = gp.seed ?? null;
  const stages = Array.isArray(gp.stages) ? gp.stages : [];
  const modelIds = stages.map((stage) => stage?.model_id).filter(Boolean);
  const artifactLine = formatRevisionAiArtifactSummary(summary);
  if (!pipeline && seed == null && !modelIds.length && !summary.model_id && !artifactLine) {
    return null;
  }
  const parts = [];
  if (pipeline) {
    parts.push(pipeline);
  }
  if (modelIds.length) {
    parts.push(modelIds.join(' → '));
  } else if (summary.model_id) {
    parts.push(summary.model_id);
  }
  if (seed != null) {
    parts.push(`seed ${seed}`);
  }
  if (artifactLine) {
    parts.push(artifactLine);
  }
  return parts.join(' · ') || null;
}

/** Product role labels for Versions UI (never raw slot names). */
export const AI_ARTIFACT_ROLE_LABELS = {
  brief: 'Brief',
  harmony_plan: 'Harmony',
  motif_plan: 'Motif',
  arrangement_plan: 'Arrangement',
  critique: 'Critique',
  revision_plan: 'Revision plan',
};

const ROLE_CONTENT_TYPES = {
  brief: 'agent.brief.v1',
  harmony_plan: 'agent.harmony_plan.v1',
  motif_plan: 'agent.motif_plan.v1',
  arrangement_plan: 'agent.arrangement_plan.v1',
  critique: 'agent.critique.v1',
  revision_plan: 'agent.revision_plan.v1',
};

/**
 * Build generation_parameters.artifact_role_map from preview artifact_log.
 * @param {Array<object>|null|undefined} artifactLog
 * @param {{ requireRevisionPlan?: boolean }} [options]
 * @returns {object}
 */
export function buildArtifactRoleMapFromLog(artifactLog, options = {}) {
  const log = Array.isArray(artifactLog) ? artifactLog : [];
  const byType = new Map();
  for (const entry of log) {
    if (!entry || typeof entry !== 'object') continue;
    const contentType = String(entry.content_type || '');
    const artifactId = String(entry.artifact_id || '');
    if (!contentType || !artifactId) continue;
    // Prefer last matching typed plan of each role.
    byType.set(contentType, { artifact_id: artifactId, content_type: contentType });
  }
  const roleMap = {};
  for (const [role, contentType] of Object.entries(ROLE_CONTENT_TYPES)) {
    roleMap[role] = byType.get(contentType) || null;
  }
  if (!options.requireRevisionPlan) {
    // revision_plan remains optional on approve path.
  }
  return roleMap;
}

/**
 * Validate spine Apply role map presence (client-side mirror of server rules).
 * @param {object|null|undefined} roleMap
 * @param {{ requireRevisionPlan?: boolean }} [options]
 * @returns {{ ok: boolean, missing: string[] }}
 */
export function validateArtifactRoleMap(roleMap, options = {}) {
  const required = ['brief', 'harmony_plan', 'motif_plan', 'arrangement_plan', 'critique'];
  if (options.requireRevisionPlan) {
    required.push('revision_plan');
  }
  const missing = [];
  const map = roleMap && typeof roleMap === 'object' ? roleMap : {};
  for (const role of required) {
    const entry = map[role];
    if (!entry || !entry.artifact_id || !entry.content_type) {
      missing.push(role);
    }
  }
  return { ok: missing.length === 0, missing };
}

/**
 * Human-readable AI artifact role summary for Versions panel.
 * @param {object|null|undefined} summary
 * @returns {string|null}
 */
export function formatRevisionAiArtifactSummary(summary) {
  if (!summary || typeof summary !== 'object') {
    return null;
  }
  const block = summary.ai_artifacts && typeof summary.ai_artifacts === 'object'
    ? summary.ai_artifacts
    : null;
  const roles = block?.roles && typeof block.roles === 'object' ? block.roles : null;
  if (!roles) {
    return null;
  }
  const labels = [];
  for (const [role, label] of Object.entries(AI_ARTIFACT_ROLE_LABELS)) {
    if (roles[role] && roles[role].artifact_id) {
      labels.push(label);
    }
  }
  return labels.length ? `AI: ${labels.join(', ')}` : null;
}

/**
 * Pick additive AI runtime fields from an operation HTTP response.
 * @param {object|null|undefined} response
 */
export function aiRuntimeFieldsFromResponse(response) {
  if (!response || typeof response !== 'object') {
    return {};
  }
  return {
    modelId: response.model_id || response.resolved_model_id || null,
    modelVersion: response.model_version || null,
    runtime: response.runtime || null,
    capability: response.capability || null,
    operation: response.operation || response.ai_operation || null,
    generationParameters: response.generation_parameters || null,
    requestedModelId: response.requested_model_id || null,
    resolvedModelId: response.resolved_model_id || response.model_id || null,
    fallbackApplied: Boolean(response.fallback_applied),
  };
}

/**
 * Project an ensemble survivor into the generate-apply candidate envelope.
 * Does not call Apply — suggestion never writes the working score.
 *
 * @param {object} params
 * @param {object} params.survivor - ensemble.candidate.v1 row
 * @param {object|null|undefined} params.report - ensemble.arbitration.v1
 * @param {object|null|undefined} params.workingComposition
 * @param {object|null|undefined} params.promptSnapshot
 */
export async function stageEnsembleSurvivorAsGenerationCandidate({
  survivor,
  report = null,
  workingComposition = null,
  promptSnapshot = null,
} = {}) {
  if (!survivor || typeof survivor !== 'object' || !survivor.composition) {
    throw new Error('Ensemble survivor composition is required');
  }
  const provenance = survivor.provenance && typeof survivor.provenance === 'object'
    ? survivor.provenance
    : {};
  const policy = report?.policy && typeof report.policy === 'object' ? report.policy : {};
  const siblingModelIds = Array.isArray(policy.model_ids) ? policy.model_ids : [];
  const seed = provenance.seed ?? policy.base_seed ?? null;
  const modelId = provenance.model_id || null;
  const generationParameters = {
    provenance_schema: 'generation.provenance.v1',
    pipeline_id: 'ensemble_arbitration',
    seed,
    strategy: provenance.strategy || policy.strategy || 'parallel_once',
    attempt_ordinal: provenance.attempt_ordinal ?? null,
    ensemble_candidate_id: survivor.candidate_id || null,
    sibling_model_ids: siblingModelIds,
    suggested_candidate_id: report?.suggested_candidate_id || null,
    musical_quality_claim: false,
    critic_is_subjective_layer: true,
    ranking_is_preference_not_quality: true,
    ranking_applied: Boolean(report?.ranking_applied),
    stages: [
      {
        operation: 'ensemble_symbolic_compose',
        model_id: modelId,
        capability: 'symbolic_composer',
        runtime: provenance.runtime || null,
        model_version: provenance.model_version || null,
        seed,
      },
    ],
  };
  const sourceFingerprint = await fingerprintCompositionOrNull(workingComposition);
  const candidateFingerprint = await compositionEditFingerprint(survivor.composition);
  console.debug('[ensemble] stage survivor', {
    candidateId: survivor.candidate_id || null,
    suggestedId: report?.suggested_candidate_id || null,
    modelId,
    suggestedEqualsSelected: Boolean(
      report?.suggested_candidate_id
      && survivor.candidate_id
      && report.suggested_candidate_id === survivor.candidate_id,
    ),
  });
  return buildAiCandidateEnvelope({
    candidateId: survivor.candidate_id || makeAiCandidateId('ens'),
    operationType: 'generate-apply',
    composition: survivor.composition,
    sourceFingerprint,
    candidateFingerprint,
    provider: 'ensemble',
    model: modelId,
    modelId,
    modelVersion: provenance.model_version || null,
    runtime: provenance.runtime || null,
    capability: 'symbolic_composer',
    operation: 'generate-apply',
    generationParameters,
    resolvedModelId: modelId,
    instruction: promptSnapshot?.instructions || null,
    warnings: [
      'ensemble_arbitration_preview',
      'preference_critic_not_musical_truth',
    ],
    extras: {
      prompt: promptSnapshot || null,
      pipeline_id: 'ensemble_arbitration',
      seed,
      stages: generationParameters.stages,
      ensemble_report_honesty: {
        musical_quality_claim: false,
        critic_is_subjective_layer: true,
        ranking_is_preference_not_quality: true,
      },
    },
  });
}
