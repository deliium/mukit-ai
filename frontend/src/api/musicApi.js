import axios from 'axios';
import { prepareCompositionForStore, CompositionVersionError } from '../utils/compositionVersion.js';
import {
  AnalysisScopeError,
  buildAnalysisRequestScope,
  fingerprintLogPrefix,
  normalizeAnalysisReport,
  normalizeAnalysisScopeForKey,
  sanitizeScopeForLog,
  analysisWarningCodes,
} from '../utils/compositionAnalysis.js';
import { isCanonicalComposition, validateMusicJson } from '../utils/musicJsonValidation.js';
import { downloadBlob, filenameFromContentDisposition } from '../utils/downloadFile.js';
import {
  editFingerprintLogPrefix,
  normalizeDevelopmentPreviewResponse,
  normalizeDevelopmentRequest,
} from '../utils/compositionCandidates.js';
import {
  cacheArrangementCatalog,
  clearArrangementCatalogCache,
  editFingerprintLogPrefix as arrangementFingerprintPrefix,
  getCachedArrangementCatalog,
  normalizeArrangementCatalog,
  normalizeArrangementPreviewResponse,
  normalizeArrangementRequest,
} from '../utils/compositionArrangementCandidates.js';
import {
  fingerprintPrefix,
  normalizeEmbedScope,
  normalizeSimilarityHits,
  normalizeStyleReference,
} from '../utils/compositionEmbeddingReference.js';
import { createAppLogger } from '../utils/appLogger.js';

const arrangementLogger = createAppLogger('musicApi.arrangement');
const embeddingLogger = createAppLogger('musicApi.embeddings');

export const PROJECTION_HEADER_NAMES = {
  status: 'x-mukit-projection-status',
  issues: 'x-mukit-projection-issues',
  exactCount: 'x-mukit-projection-exact-count',
  approximatedCount: 'x-mukit-projection-approximated-count',
  omittedCount: 'x-mukit-projection-omitted-count',
  failedCount: 'x-mukit-projection-failed-count',
};

export function parseProjectionHeaders(headers = {}) {
  const normalized = Object.fromEntries(
    Object.entries(headers || {}).map(([key, value]) => [String(key).toLowerCase(), value]),
  );
  const rawIssues = normalized[PROJECTION_HEADER_NAMES.issues];
  const issues = typeof rawIssues === 'string' && rawIssues.trim()
    ? rawIssues.split(',').map((code) => code.trim()).filter(Boolean)
    : [];
  const parseCount = (name) => {
    const raw = normalized[name];
    if (raw === undefined || raw === null || raw === '') {
      return 0;
    }
    const parsed = Number.parseInt(String(raw), 10);
    return Number.isFinite(parsed) && parsed >= 0 ? parsed : 0;
  };
  const status = typeof normalized[PROJECTION_HEADER_NAMES.status] === 'string'
    ? normalized[PROJECTION_HEADER_NAMES.status]
    : 'exact';
  return {
    status,
    issues,
    exactCount: parseCount(PROJECTION_HEADER_NAMES.exactCount),
    approximatedCount: parseCount(PROJECTION_HEADER_NAMES.approximatedCount),
    omittedCount: parseCount(PROJECTION_HEADER_NAMES.omittedCount),
    failedCount: parseCount(PROJECTION_HEADER_NAMES.failedCount),
    hasIssues: issues.length > 0 || status !== 'exact',
  };
}

export function projectionWarningsFromHeaders(headers = {}) {
  const projection = parseProjectionHeaders(headers);
  if (!projection.hasIssues) {
    return [];
  }
  if (projection.issues.length > 0) {
    return projection.issues.map((code) => `Projection ${code}`);
  }
  return [`Projection status: ${projection.status}`];
}

function normalizeApiComposition(raw, { context = 'response' } = {}) {
  try {
    const composition = prepareCompositionForStore(raw);
    console.debug('[musicApi] Composition version normalized', {
      context,
      sourceSchemaVersion: raw?.schema_version ?? null,
      targetSchemaVersion: composition.schema_version,
    });
    return composition;
  } catch (error) {
    if (error instanceof CompositionVersionError) {
      console.warn('[musicApi] Composition normalization failed', {
        context,
        code: error.code,
        schemaVersion: error.schemaVersion,
        message: error.message,
      });
      throw error;
    }
    throw error;
  }
}

function validateCanonicalForApi(composition, { action = 'request' } = {}) {
  const validation = validateMusicJson(composition);
  if (!validation.valid || !isCanonicalComposition(composition)) {
    const message = validation.message || `Canonical composition JSON is required for ${action}`;
    console.error('[musicApi] Canonical composition validation failed', { action, message });
    throw new Error(message);
  }
  return validation;
}

export async function getHealth() {
  return request('get', '/health');
}

export async function getLlmModels() {
  return request('get', '/llm/models');
}

/**
 * Canonical AI model catalog (capabilities, ops, stubs). Prefer over /llm/models for new UI.
 * Current MusicGenerator selector may keep using getLlmModels until per-op UX lands.
 */
export async function fetchAiModels(params = {}) {
  const query = {};
  if (params.capability) query.capability = params.capability;
  if (params.operation) query.operation = params.operation;
  if (params.status) query.status = params.status;
  const response = await request('get', '/ai/models', null, { params: query });
  const modelCount = Array.isArray(response?.models) ? response.models.length : 0;
  console.debug('[musicApi] AI model catalog loaded', {
    modelCount,
    defaultModelId: response?.default_model_id || null,
    capability: params.capability || null,
    operation: params.operation || null,
  });
  return response;
}

export async function generateLlmMusicJson(payload) {
  const response = await request('post', '/llm/generate-music-json', payload);
  const composition = normalizeApiComposition(response.music, { context: 'generate-response' });
  const validation = validateMusicJson(composition);
  console.debug('[musicApi] LLM music response validation completed', {
    valid: validation.valid,
    schemaVersion: composition.schema_version,
    canonical: isCanonicalComposition(composition),
    warningCount: response.warnings?.length || 0,
    generationValidationStatus: response.validation?.status || null,
    generationValidationErrorCount: response.validation?.errors?.length || 0,
    generationValidationWarningCount: response.validation?.warnings?.length || 0,
    pipelineId: response.pipeline_id || null,
    stageModelIds: Array.isArray(response.stages)
      ? response.stages.map((stage) => stage.model_id).filter(Boolean)
      : [],
    seed: response.seed ?? null,
  });
  if (!validation.valid) {
    console.error('[musicApi] LLM music response failed validation', { message: validation.message });
    throw new Error(validation.message);
  }
  return { ...response, music: composition };
}

export async function editCompositionRegion(payload) {
  const selection = payload?.edit?.selection || {};
  const trackScopeCount = Array.isArray(selection.track_ids) ? selection.track_ids.length : 0;
  console.debug('[musicApi] Composition region edit request started', {
    provider: payload?.selection?.provider || null,
    model: payload?.selection?.model || null,
    startBar: selection.start_bar,
    endBar: selection.end_bar,
    trackScopeCount,
    schemaVersion: payload?.composition?.schema_version || 'legacy',
  });

  const inboundComposition = normalizeApiComposition(payload.composition, { context: 'edit-request' });
  validateCanonicalForApi(inboundComposition, { action: 'region edit' });

  try {
    const response = await request('post', '/llm/edit-composition-region', {
      ...payload,
      composition: inboundComposition,
    });
    const composition = normalizeApiComposition(response.composition, { context: 'edit-response' });
    const validation = validateMusicJson(composition);
    const hasPatch = Boolean(response.patch && response.patch.operation === 'replace_region');
    console.debug('[musicApi] Composition region edit response validation completed', {
      provider: response.provider || null,
      model: response.model || null,
      startBar: selection.start_bar,
      endBar: selection.end_bar,
      trackScopeCount,
      valid: validation.valid,
      schemaVersion: composition.schema_version,
      warningCount: response.warnings?.length || 0,
      hasPatch,
    });
    if (!validation.valid) {
      console.error('[musicApi] Composition region edit response failed validation', {
        message: validation.message,
      });
      throw new Error(validation.message);
    }
    if (!hasPatch) {
      console.error('[musicApi] Composition region edit response missing replace_region patch', {
        status: 'invalid_patch',
      });
      throw new Error('Edit response is missing a replace_region patch');
    }
    return { ...response, composition };
  } catch (error) {
    console.error('[musicApi] Composition region edit request failed', {
      status: error.response?.status || null,
      message: error.message,
    });
    throw error;
  }
}

export async function exportMusicXml(composition, options = {}) {
  return exportComposition(composition, {
    endpoint: '/export/musicxml',
    format: 'musicxml',
    fallbackFilename: 'composition.musicxml',
    expectedType: 'application/vnd.recordare.musicxml+xml',
    download: options.download !== false,
  });
}

export async function renderMusicXmlPreview(composition) {
  const normalized = normalizeApiComposition(composition, { context: 'preview-request' });
  validateCanonicalForApi(normalized, { action: 'MusicXML preview' });
  const eventCount = Array.isArray(normalized.tracks)
    ? normalized.tracks.reduce((total, track) => total + (track.events?.length || 0), 0)
    : 0;
  console.debug('[musicApi] MusicXML preview request started', {
    schemaVersion: normalized.schema_version,
    trackCount: normalized.tracks?.length || 0,
    eventCount,
  });

  try {
    const response = await axios.post('/export/musicxml/preview', normalized, {
      responseType: 'text',
      headers: { Accept: 'application/vnd.recordare.musicxml+xml, application/xml, text/xml, text/plain' },
    });
    const musicxml = typeof response.data === 'string' ? response.data : String(response.data || '');
    const projection = parseProjectionHeaders(response.headers);
    console.debug('[musicApi] MusicXML preview request completed', {
      schemaVersion: normalized.schema_version,
      eventCount,
      musicXmlLength: musicxml.length,
      projectionStatus: projection.status,
      projectionIssueCount: projection.issues.length,
    });
    return { musicxml, projection, warnings: projectionWarningsFromHeaders(response.headers) };
  } catch (error) {
    const detail = error.response?.data?.detail || error.message || 'Unknown MusicXML preview failure';
    const message = typeof detail === 'string' ? detail : JSON.stringify(detail);
    console.error('[musicApi] MusicXML preview request failed', {
      status: error.response?.status,
      detail: message,
    });
    throw new Error(message);
  }
}

export async function exportMidi(composition, options = {}) {
  return exportComposition(composition, {
    endpoint: '/export/midi',
    format: 'midi',
    fallbackFilename: 'composition.mid',
    expectedType: 'audio/midi',
    download: options.download !== false,
  });
}

export async function exportWav(composition) {
  return exportComposition(composition, {
    endpoint: '/export/wav',
    format: 'wav',
    fallbackFilename: 'composition.wav',
    expectedType: 'audio/wav',
  });
}

export class ImportApiError extends Error {
  constructor(message, { status = null, code = null, details = null } = {}) {
    super(message);
    this.name = 'ImportApiError';
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export class AnalysisApiError extends Error {
  constructor(message, { status = null, code = null, details = null } = {}) {
    super(message);
    this.name = 'AnalysisApiError';
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export class MotifApiError extends Error {
  constructor(message, { status = null, code = null, details = null } = {}) {
    super(message);
    this.name = 'MotifApiError';
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export class ReharmonizeApiError extends Error {
  constructor(message, { status = null, code = null, details = null } = {}) {
    super(message);
    this.name = 'ReharmonizeApiError';
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export class DevelopmentApiError extends Error {
  constructor(message, { status = null, code = null, details = null } = {}) {
    super(message);
    this.name = 'DevelopmentApiError';
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export class ArrangementApiError extends Error {
  constructor(message, { status = null, code = null, details = null } = {}) {
    super(message);
    this.name = 'ArrangementApiError';
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export class EmbeddingApiError extends Error {
  constructor(message, { status = null, code = null, details = null } = {}) {
    super(message);
    this.name = 'EmbeddingApiError';
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export class TranscriptionApiError extends Error {
  constructor(message, { status = null, code = null, details = null } = {}) {
    super(message);
    this.name = 'TranscriptionApiError';
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export const REHARMONIZE_OPERATIONS = Object.freeze([
  'suggest_progression',
  'reharmonize',
  'increase_tension',
  'decrease_tension',
  'strengthen_cadence',
  'tonicize_target',
  'use_secondary_dominants',
  'use_modal_interchange',
  'simplify_harmony',
]);

export const REHARMONIZE_CONTENT_POLICIES = Object.freeze([
  'preserve_melody_adapt_harmony',
  'preserve_harmony_adapt_melody',
  'adapt_accompaniment_only',
]);

export const REHARMONIZE_ENGINES = Object.freeze(['deterministic', 'ai']);

/**
 * POST /analysis/composition — deterministic composition.analysis.v1 sidecar.
 * Validates complete V2 + scope locally; preserves structured backend 422 errors.
 */
function validateMotifApplyResult(result) {
  if (!result || typeof result !== 'object' || Array.isArray(result)) {
    return 'Motif apply response is missing result metadata';
  }
  const requiredStrings = [
    'motif_id',
    'source_occurrence_id',
    'destination_track_id',
    'new_occurrence_id',
    'relationship',
  ];
  for (const field of requiredStrings) {
    if (typeof result[field] !== 'string' || !result[field].trim()) {
      return `Motif apply result missing ${field}`;
    }
  }
  if (!Number.isInteger(result.destination_start_bar) || result.destination_start_bar < 1) {
    return 'Motif apply result missing destination_start_bar';
  }
  if (!Number.isInteger(result.destination_start_tick) || result.destination_start_tick < 0) {
    return 'Motif apply result missing destination_start_tick';
  }
  if (!Array.isArray(result.created_event_ids) || result.created_event_ids.length < 1) {
    return 'Motif apply result missing created_event_ids';
  }
  if (typeof result.identity_score !== 'number' || !Number.isFinite(result.identity_score)) {
    return 'Motif apply result missing identity_score';
  }
  if (!result.transform || typeof result.transform !== 'object') {
    return 'Motif apply result missing transform provenance';
  }
  if (!result.diagnostics || typeof result.diagnostics !== 'object') {
    return 'Motif apply result missing diagnostics';
  }
  return null;
}

function parseMotifErrorDetail(detail) {
  if (!detail) {
    return { code: null, message: 'Unknown motif apply failure', details: null };
  }
  if (typeof detail === 'string') {
    return { code: null, message: detail, details: null };
  }
  if (typeof detail === 'object') {
    const code = typeof detail.code === 'string' ? detail.code : null;
    const message = typeof detail.message === 'string'
      ? detail.message
      : (typeof detail.detail === 'string' ? detail.detail : JSON.stringify(detail));
    const details = detail.details && typeof detail.details === 'object' ? detail.details : null;
    return { code, message, details };
  }
  return { code: null, message: String(detail), details: null };
}

/**
 * POST /motifs/apply — mechanical or creative motif transformation.
 */
export async function applyMotif(payload) {
  const source = payload?.source || {};
  const destination = payload?.destination || {};
  console.debug('[musicApi] Motif apply request started', {
    motifId: source.motif_id || null,
    sourceOccurrenceId: source.occurrence_id || null,
    destinationSectionId: destination.section_id || null,
    destinationTrackId: destination.track_id || null,
    destinationStartBar: destination.start_bar ?? null,
    operation: payload?.operation || null,
    variationStrength: payload?.variation_strength ?? null,
    provider: payload?.selection?.provider || null,
    model: payload?.selection?.model || null,
    schemaVersion: payload?.composition?.schema_version || 'legacy',
  });

  const inboundComposition = normalizeApiComposition(payload.composition, { context: 'motif-apply-request' });
  validateCanonicalForApi(inboundComposition, { action: 'motif apply' });

  try {
    const axiosResponse = await axios.post('/motifs/apply', {
      ...payload,
      composition: inboundComposition,
    });
    const response = axiosResponse.data || {};
    const composition = normalizeApiComposition(response.composition, { context: 'motif-apply-response' });
    const validation = validateMusicJson(composition);
    const resultError = validateMotifApplyResult(response.result);
    const createdCount = Array.isArray(response.result?.created_event_ids)
      ? response.result.created_event_ids.length
      : 0;
    console.debug('[musicApi] Motif apply response validation completed', {
      motifId: response.result?.motif_id || null,
      sourceOccurrenceId: response.result?.source_occurrence_id || null,
      destinationTrackId: response.result?.destination_track_id || null,
      operation: response.result?.relationship || payload?.operation || null,
      variationStrength: payload?.variation_strength ?? null,
      createdEventCount: createdCount,
      replacedEventCount: response.result?.diagnostics?.replaced_event_count ?? null,
      valid: validation.valid,
      schemaVersion: composition.schema_version,
      warningCount: response.warnings?.length || 0,
      identityScore: response.result?.identity_score ?? null,
      status: validation.valid && !resultError ? 'ok' : 'invalid',
    });
    if (!validation.valid) {
      console.error('[musicApi] Motif apply response failed composition validation', {
        message: validation.message,
      });
      throw new MotifApiError(validation.message, { code: 'motif_invalid_response' });
    }
    if (resultError) {
      console.error('[musicApi] Motif apply response failed result contract', {
        message: resultError,
      });
      throw new MotifApiError(resultError, { code: 'motif_invalid_response' });
    }
    return { ...response, composition };
  } catch (error) {
    if (error instanceof MotifApiError) {
      throw error;
    }
    const status = error.response?.status ?? null;
    const parsed = parseMotifErrorDetail(error.response?.data?.detail ?? error.message);
    console.error('[musicApi] Motif apply request failed', {
      status,
      code: parsed.code,
      message: parsed.message,
      operation: payload?.operation || null,
    });
    throw new MotifApiError(parsed.message, {
      status,
      code: parsed.code,
      details: parsed.details,
    });
  }
}

/**
 * POST /harmony/reharmonize/preview — stateless candidate; never mutates a project.
 */
export async function previewReharmonization(payload) {
  const selection = payload?.selection || {};
  const operation = payload?.operation;
  const contentPolicy = payload?.content_policy;
  const engine = payload?.engine || 'deterministic';
  const targetTrackIds = Array.isArray(payload?.target_track_ids)
    ? payload.target_track_ids.filter((id) => typeof id === 'string' && id.trim())
    : [];

  if (!REHARMONIZE_OPERATIONS.includes(operation)) {
    throw new ReharmonizeApiError('Unsupported reharmonization operation', {
      code: 'reharmonize_invalid_request',
    });
  }
  if (!REHARMONIZE_CONTENT_POLICIES.includes(contentPolicy)) {
    throw new ReharmonizeApiError('Unsupported reharmonization content policy', {
      code: 'reharmonize_invalid_request',
    });
  }
  if (!REHARMONIZE_ENGINES.includes(engine)) {
    throw new ReharmonizeApiError('Unsupported reharmonization engine', {
      code: 'reharmonize_invalid_request',
    });
  }
  if (!Number.isInteger(selection.start_bar) || !Number.isInteger(selection.end_bar)
    || selection.start_bar < 1 || selection.end_bar < selection.start_bar) {
    throw new ReharmonizeApiError('Invalid bar selection for reharmonization', {
      code: 'reharmonize_invalid_selection',
    });
  }
  if (!targetTrackIds.length) {
    throw new ReharmonizeApiError('target_track_ids must be explicit', {
      code: 'reharmonize_invalid_targets',
    });
  }

  console.info('[musicApi] Reharmonize preview request started', {
    operation,
    contentPolicy,
    engine,
    startBar: selection.start_bar,
    endBar: selection.end_bar,
    targetCount: targetTrackIds.length,
    provider: payload?.selection_options?.provider || null,
    model: payload?.selection_options?.model || null,
    instructionLen: typeof payload?.instruction === 'string' ? payload.instruction.length : 0,
  });

  const inboundComposition = normalizeApiComposition(payload.composition, {
    context: 'reharmonize-preview-request',
  });
  validateCanonicalForApi(inboundComposition, { action: 'reharmonize preview' });

  try {
    const axiosResponse = await axios.post('/harmony/reharmonize/preview', {
      ...payload,
      composition: inboundComposition,
      target_track_ids: targetTrackIds,
      engine,
    });
    const response = axiosResponse.data || {};
    const composition = normalizeApiComposition(response.composition, {
      context: 'reharmonize-preview-response',
    });
    const validation = validateMusicJson(composition);
    const contractError = validateReharmonizePreviewResponse(response, composition);
    console.debug('[musicApi] Reharmonize preview response validation completed', {
      operation,
      provider: response.provider || null,
      changedSpanCount: Array.isArray(response.harmony_changes) ? response.harmony_changes.length : 0,
      changedTrackCount: Array.isArray(response.track_changes)
        ? response.track_changes.filter((item) => item?.events_changed > 0).length
        : 0,
      compatibilityStatus: response.compatibility?.status || null,
      baseFingerprintPrefix: fingerprintLogPrefix(response.base_fingerprint),
      proposalFingerprintPrefix: fingerprintLogPrefix(response.proposal_fingerprint),
      valid: validation.valid && !contractError,
    });
    if (!validation.valid) {
      throw new ReharmonizeApiError(validation.message, { code: 'reharmonize_invalid_response' });
    }
    if (contractError) {
      throw new ReharmonizeApiError(contractError, { code: 'reharmonize_invalid_response' });
    }
    return { ...response, composition };
  } catch (error) {
    if (error instanceof ReharmonizeApiError) {
      throw error;
    }
    const status = error.response?.status ?? null;
    const parsed = parseMotifErrorDetail(error.response?.data?.detail ?? error.message);
    console.error('[musicApi] Reharmonize preview request failed', {
      status,
      code: parsed.code,
      message: parsed.message,
      operation,
    });
    throw new ReharmonizeApiError(parsed.message, {
      status,
      code: parsed.code,
      details: parsed.details,
    });
  }
}

/**
 * POST /composition/development/preview — multi-candidate continuation/variation.
 * Stateless: never persists projects; returns 1-4 ephemeral candidates.
 */
export async function previewCompositionDevelopment(payload) {
  const normalized = normalizeDevelopmentRequest(payload);
  if (!normalized.ok) {
    throw new DevelopmentApiError(normalized.message, { code: normalized.code });
  }

  const requestBody = normalized.request;
  console.info('[musicApi] Composition development preview request started', {
    operation: requestBody.operation,
    intent: requestBody.development_intent,
    strength: requestBody.variation_strength,
    candidateCount: requestBody.candidate_count,
    outputBars: requestBody.output_bars,
    provider: requestBody.selection?.provider || null,
    model: requestBody.selection?.model || null,
    instructionLen: typeof requestBody.instruction === 'string' ? requestBody.instruction.length : 0,
    hasStyleReference: Boolean(requestBody.style_reference),
    styleReferenceProjectId: requestBody.style_reference?.project_id || null,
    styleReferenceScopeKind: requestBody.style_reference?.scope?.kind || null,
  });

  const inboundComposition = normalizeApiComposition(requestBody.composition, {
    context: 'development-preview-request',
  });
  validateCanonicalForApi(inboundComposition, { action: 'composition development preview' });
  if (!isCanonicalComposition(inboundComposition)) {
    throw new DevelopmentApiError('Development accepts only composition.v2 documents', {
      code: 'development_invalid_request',
    });
  }

  try {
    const axiosResponse = await axios.post('/composition/development/preview', {
      ...requestBody,
      composition: inboundComposition,
    });
    const response = axiosResponse.data || {};
    const contract = normalizeDevelopmentPreviewResponse(response);
    if (!contract.ok) {
      throw new DevelopmentApiError(contract.message, { code: contract.code });
    }
    const candidates = contract.response.candidates.map((candidate) => {
      const composition = normalizeApiComposition(candidate.composition, {
        context: 'development-preview-candidate',
      });
      const validation = validateMusicJson(composition);
      if (!validation.valid) {
        throw new DevelopmentApiError(validation.message, { code: 'development_invalid_response' });
      }
      return { ...candidate, composition };
    });
    console.debug('[musicApi] Composition development preview response validated', {
      operation: contract.response.operation,
      returnedCandidateCount: candidates.length,
      warningCodeCount: contract.response.warning_codes.length,
      editSourcePrefix: editFingerprintLogPrefix(contract.response.edit_source_fingerprint),
      provider: contract.response.provider || null,
    });
    return { ...contract.response, candidates };
  } catch (error) {
    if (error instanceof DevelopmentApiError) {
      throw error;
    }
    const status = error.response?.status ?? null;
    const parsed = parseMotifErrorDetail(error.response?.data?.detail ?? error.message);
    console.error('[musicApi] Composition development preview failed', {
      status,
      code: parsed.code,
      message: parsed.message,
      operation: requestBody.operation,
    });
    throw new DevelopmentApiError(parsed.message, {
      status,
      code: parsed.code,
      details: parsed.details,
    });
  }
}

/**
 * GET /composition/arrangement/instruments — versioned selectable catalog.
 * Normalizes, caches by fingerprint, and never logs catalog payloads.
 * Pass `forceRefresh: true` to bypass the module cache.
 */
export async function loadArrangementInstruments({ forceRefresh = false } = {}) {
  if (!forceRefresh) {
    const cached = getCachedArrangementCatalog();
    if (cached) {
      arrangementLogger.debug('Arrangement instruments cache hit', {
        endpoint: 'GET /composition/arrangement/instruments',
        catalogVersion: cached.catalog_version,
        instrumentCount: cached.instruments.length,
        fingerprintPrefix: arrangementFingerprintPrefix(cached.fingerprint),
      });
      return cached;
    }
  }

  arrangementLogger.debug('Arrangement instruments request started', {
    endpoint: 'GET /composition/arrangement/instruments',
    forceRefresh: Boolean(forceRefresh),
  });
  try {
    const axiosResponse = await axios.get('/composition/arrangement/instruments');
    const normalized = normalizeArrangementCatalog(axiosResponse.data || {});
    if (!normalized.ok) {
      throw new ArrangementApiError(normalized.message, { code: normalized.code });
    }
    const catalog = cacheArrangementCatalog(normalized.catalog);
    arrangementLogger.debug('Arrangement instruments catalog accepted', {
      endpoint: 'GET /composition/arrangement/instruments',
      catalogVersion: catalog.catalog_version,
      instrumentCount: catalog.instruments.length,
      roleCount: catalog.track_roles.length,
      fingerprintPrefix: arrangementFingerprintPrefix(catalog.fingerprint),
      status: axiosResponse.status,
    });
    return catalog;
  } catch (error) {
    if (error instanceof ArrangementApiError) {
      throw error;
    }
    const status = error.response?.status ?? null;
    const parsed = parseMotifErrorDetail(error.response?.data?.detail ?? error.message);
    arrangementLogger.error('Arrangement instruments request failed', {
      endpoint: 'GET /composition/arrangement/instruments',
      status,
      code: parsed.code,
    });
    throw new ArrangementApiError(parsed.message, {
      status,
      code: parsed.code || 'arrangement_catalog_unavailable',
      details: parsed.details,
    });
  }
}

/** @deprecated Prefer loadArrangementInstruments — kept as a thin alias. */
export async function fetchArrangementInstruments(options) {
  return loadArrangementInstruments(options);
}

/** Test/helper: clear the module arrangement catalog cache. */
export function resetArrangementInstrumentCache() {
  clearArrangementCatalogCache();
}

/**
 * POST /composition/arrangement/preview — multi-candidate arrangement.
 * Stateless: never persists projects; returns ephemeral candidates + rejected attempts.
 */
export async function previewCompositionArrangement(payload) {
  const normalized = normalizeArrangementRequest(payload);
  if (!normalized.ok) {
    throw new ArrangementApiError(normalized.message, { code: normalized.code });
  }

  const requestBody = normalized.request;
  arrangementLogger.info('Arrangement preview request started', {
    endpoint: 'POST /composition/arrangement/preview',
    operation: requestBody.operation,
    candidateCount: requestBody.candidate_count,
    sourceTrackCount: requestBody.source_track_ids.length,
    protectedTrackCount: requestBody.protected_track_ids.length,
    beforePartCount: requestBody.instrumentation.before.length,
    afterPartCount: requestBody.instrumentation.after.length,
    provider: requestBody.selection?.provider || null,
    model: requestBody.selection?.model || null,
    instructionLen: typeof requestBody.instruction === 'string' ? requestBody.instruction.length : 0,
  });

  const inboundComposition = normalizeApiComposition(requestBody.composition, {
    context: 'arrangement-preview-request',
  });
  validateCanonicalForApi(inboundComposition, { action: 'composition arrangement preview' });
  if (!isCanonicalComposition(inboundComposition)) {
    throw new ArrangementApiError('Arrangement accepts only composition.v2 documents', {
      code: 'arrangement_invalid_source',
    });
  }

  try {
    const axiosResponse = await axios.post('/composition/arrangement/preview', {
      ...requestBody,
      composition: inboundComposition,
    });
    const response = axiosResponse.data || {};
    const contract = normalizeArrangementPreviewResponse(response);
    if (!contract.ok) {
      throw new ArrangementApiError(contract.message, { code: contract.code });
    }
    const candidates = contract.response.candidates.map((candidate) => {
      const composition = normalizeApiComposition(candidate.composition, {
        context: 'arrangement-preview-candidate',
      });
      const validation = validateMusicJson(composition);
      if (!validation.valid) {
        throw new ArrangementApiError(validation.message, { code: 'arrangement_invalid_response' });
      }
      return { ...candidate, composition };
    });
    arrangementLogger.debug('Arrangement preview response validated', {
      endpoint: 'POST /composition/arrangement/preview',
      operation: contract.response.operation,
      catalogVersion: contract.response.catalog_version,
      returnedCandidateCount: candidates.length,
      rejectedCount: contract.response.rejected_attempts.length,
      warningCodeCount: contract.response.warning_codes.length,
      editSourcePrefix: arrangementFingerprintPrefix(contract.response.edit_source_fingerprint),
      catalogPrefix: arrangementFingerprintPrefix(contract.response.catalog_fingerprint),
      provider: contract.response.provider || null,
      status: axiosResponse.status,
    });
    return { ...contract.response, candidates };
  } catch (error) {
    if (error instanceof ArrangementApiError) {
      throw error;
    }
    const status = error.response?.status ?? null;
    const parsed = parseMotifErrorDetail(error.response?.data?.detail ?? error.message);
    arrangementLogger.error('Arrangement preview request failed', {
      endpoint: 'POST /composition/arrangement/preview',
      status,
      code: parsed.code,
      operation: requestBody.operation,
    });
    throw new ArrangementApiError(parsed.message, {
      status,
      code: parsed.code,
      details: parsed.details,
    });
  }
}

function validateReharmonizePreviewResponse(response, composition) {
  if (typeof response.base_fingerprint !== 'string' || response.base_fingerprint.length < 16) {
    return 'Reharmonize preview missing base_fingerprint';
  }
  if (typeof response.proposal_fingerprint !== 'string' || response.proposal_fingerprint.length < 16) {
    return 'Reharmonize preview missing proposal_fingerprint';
  }
  if (!composition || !isCanonicalComposition(composition)) {
    return 'Reharmonize preview missing canonical composition';
  }
  if (!response.compatibility || typeof response.compatibility !== 'object') {
    return 'Reharmonize preview missing compatibility report';
  }
  if (!['compatible', 'compatible_with_warnings', 'incompatible'].includes(response.compatibility.status)) {
    return 'Reharmonize preview has invalid compatibility status';
  }
  if (!Array.isArray(response.harmony_changes) || !Array.isArray(response.track_changes)
    || !Array.isArray(response.preservation)) {
    return 'Reharmonize preview missing change summaries';
  }
  if (typeof response.provider !== 'string' || !response.provider.trim()) {
    return 'Reharmonize preview missing provider';
  }
  if (!Number.isInteger(response.start_tick) || !Number.isInteger(response.end_tick)
    || response.end_tick <= response.start_tick) {
    return 'Reharmonize preview missing selection ticks';
  }
  return null;
}

export async function analyzeComposition(composition, scope = { kind: 'composition' }) {
  let normalized;
  try {
    normalized = normalizeApiComposition(composition, { context: 'analysis-request' });
  } catch (error) {
    if (error instanceof CompositionVersionError) {
      throw new AnalysisApiError(error.message, {
        status: null,
        code: 'analysis_invalid_composition',
        details: { schemaVersion: error.schemaVersion, reason: error.code },
      });
    }
    throw error;
  }

  validateCanonicalForApi(normalized, { action: 'composition analysis' });
  if (normalized.schema_version !== 'composition.v2') {
    throw new AnalysisApiError('Analysis accepts only composition.v2 documents', {
      code: 'analysis_invalid_composition',
      details: { schema_version: normalized.schema_version },
    });
  }

  let requestScope;
  try {
    requestScope = typeof scope?.kind === 'string' && scope.kind === 'composition' && Object.keys(scope).length === 1
      ? { kind: 'composition' }
      : buildAnalysisRequestScopeFromPayload(normalized, scope);
  } catch (error) {
    if (error instanceof AnalysisScopeError || error instanceof AnalysisApiError) {
      throw error instanceof AnalysisApiError
        ? error
        : new AnalysisApiError(error.message, {
          code: error.code || 'analysis_invalid_scope',
          details: error.details || null,
        });
    }
    throw error;
  }

  const eventCount = Array.isArray(normalized.tracks)
    ? normalized.tracks.reduce((total, track) => total + (track.events?.length || 0), 0)
    : 0;
  console.debug('[musicApi] Composition analysis request started', {
    scope: sanitizeScopeForLog(requestScope),
    schemaVersion: normalized.schema_version,
    trackCount: normalized.tracks?.length || 0,
    sectionCount: normalized.sections?.length || 0,
    eventCount,
    barCount: normalized.bar_count || 0,
  });

  try {
    const response = await axios.post('/analysis/composition', {
      composition: normalized,
      scope: requestScope,
    });
    const report = normalizeAnalysisReport(response.data);
    const warningCodes = analysisWarningCodes(report.warnings);
    console.info('[musicApi] Composition analysis response accepted', {
      status: report.status,
      algorithmVersion: report.algorithm_version,
      scopeKind: report.resolved_scope?.kind || null,
      warningCount: report.warnings.length,
      warningCodes: warningCodes.slice(0, 32),
      fingerprintPrefix: fingerprintLogPrefix(report.source_fingerprint),
    });
    if (warningCodes.length) {
      console.warn('[musicApi] Composition analysis returned warning codes', {
        warningCodes: warningCodes.slice(0, 32),
      });
    }
    return report;
  } catch (error) {
    if (error instanceof AnalysisApiError) {
      throw error;
    }
    if (!axios.isAxiosError(error) && error instanceof Error) {
      const message = error.message || 'Analysis response failed validation';
      console.error('[musicApi] Composition analysis response contract failed', {
        message,
      });
      throw new AnalysisApiError(message, {
        code: 'analysis_invalid_response',
      });
    }
    const status = error.response?.status ?? null;
    const parsed = parseAnalysisErrorDetail(error.response?.data?.detail);
    console.warn('[musicApi] Composition analysis request failed', {
      status,
      code: parsed.code,
      message: parsed.message,
      scope: sanitizeScopeForLog(requestScope),
    });
    throw new AnalysisApiError(parsed.message, {
      status,
      code: parsed.code,
      details: parsed.details,
    });
  }
}

/**
 * POST /critique/evaluate — session Music Evaluation Engine (non-mutating).
 */
export async function evaluateCritique(composition, options = {}) {
  let normalized;
  try {
    normalized = normalizeApiComposition(composition, { context: 'critique-request' });
  } catch (error) {
    if (error instanceof CompositionVersionError) {
      throw new AnalysisApiError(error.message, {
        status: null,
        code: 'critique_invalid_composition',
        details: { schemaVersion: error.schemaVersion, reason: error.code },
      });
    }
    throw error;
  }
  validateCanonicalForApi(normalized, { action: 'composition critique' });
  if (normalized.schema_version !== 'composition.v2') {
    throw new AnalysisApiError('Critique accepts only composition.v2 documents', {
      code: 'critique_invalid_composition',
    });
  }

  const scope = options.scope && typeof options.scope === 'object'
    ? options.scope
    : { kind: 'composition' };
  const body = {
    composition: normalized,
    scope,
    include_model_critique: Boolean(options.includeModelCritique),
    revise_on_technical: Boolean(options.reviseOnTechnical),
  };
  if (options.requestedClimaxSectionIndex != null) {
    body.requested_climax_section_index = options.requestedClimaxSectionIndex;
  }
  if (typeof options.briefExcerpt === 'string' && options.briefExcerpt.trim()) {
    body.brief_excerpt = options.briefExcerpt.trim().slice(0, 200);
  }

  console.debug('[musicApi] Critique evaluate request', {
    scopeKind: scope.kind,
    includeModelCritique: body.include_model_critique,
  });

  try {
    const response = await axios.post('/critique/evaluate', body);
    const data = response.data;
    if (!data || typeof data !== 'object' || !data.critique) {
      throw new AnalysisApiError('Critique response missing critique payload', {
        code: 'critique_payload_rejected',
      });
    }
    return data;
  } catch (error) {
    if (error instanceof AnalysisApiError) throw error;
    const status = error.response?.status ?? null;
    const detail = error.response?.data?.detail;
    const code = (detail && detail.code) || 'critique_invalid_composition';
    const message = (detail && detail.message) || error.message || 'Critique evaluate failed';
    console.warn('[musicApi] Critique evaluate failed', { status, code });
    throw new AnalysisApiError(message, { status, code, details: detail?.details || null });
  }
}

function buildAnalysisRequestScopeFromPayload(composition, scope) {
  if (!scope || typeof scope !== 'object') {
    return { kind: 'composition' };
  }
  const kind = scope.kind;
  if (kind === 'composition') {
    return { kind: 'composition' };
  }
  if (kind === 'track') {
    return buildAnalysisRequestScope({
      analysisScope: 'track',
      composition,
      trackId: scope.track_id,
    });
  }
  if (kind === 'section') {
    // Prefer explicit backend-shaped scope when section_index is already supplied.
    if (Number.isInteger(scope.section_index) && scope.section_index >= 0) {
      const sections = Array.isArray(composition.sections) ? composition.sections : [];
      if (scope.section_index >= sections.length) {
        throw new AnalysisApiError('section_index is out of range', {
          code: 'analysis_invalid_scope',
          details: {
            section_index: scope.section_index,
            section_count: sections.length,
          },
        });
      }
      const section = sections[scope.section_index];
      const mismatches = [];
      if (scope.section_id != null && section.id !== scope.section_id) {
        mismatches.push('section_id');
      }
      if (scope.expected_start_bar != null && section.start_bar !== scope.expected_start_bar) {
        mismatches.push('start_bar');
      }
      if (scope.expected_bar_count != null && section.bar_count !== scope.expected_bar_count) {
        mismatches.push('bar_count');
      }
      if (scope.expected_start_tick != null && section.start_tick !== scope.expected_start_tick) {
        mismatches.push('start_tick');
      }
      if (scope.expected_duration_ticks != null && section.duration_ticks !== scope.expected_duration_ticks) {
        mismatches.push('duration_ticks');
      }
      if (mismatches.length) {
        throw new AnalysisApiError(
          'section selectors do not match the canonical section at section_index',
          {
            code: 'analysis_invalid_scope',
            details: { section_index: scope.section_index, mismatch_fields: mismatches },
          },
        );
      }
      return normalizeAnalysisScopeForKey({
        kind: 'section',
        section_index: scope.section_index,
        section_id: scope.section_id ?? (typeof section.id === 'string' ? section.id : undefined),
        expected_start_bar: scope.expected_start_bar ?? section.start_bar,
        expected_bar_count: scope.expected_bar_count ?? section.bar_count,
        expected_start_tick: scope.expected_start_tick ?? section.start_tick,
        expected_duration_ticks: scope.expected_duration_ticks ?? section.duration_ticks,
      });
    }
    throw new AnalysisApiError('section scope requires section_index', {
      code: 'analysis_invalid_scope',
      details: { reason: 'missing_section_index' },
    });
  }
  throw new AnalysisApiError('Unknown analysis scope kind', {
    code: 'analysis_invalid_scope',
    details: { reason: 'unknown_kind' },
  });
}

function parseAnalysisErrorDetail(detail) {
  if (!detail) {
    return { code: null, message: 'Unknown analysis failure', details: null };
  }
  if (typeof detail === 'string') {
    return { code: null, message: detail, details: null };
  }
  if (typeof detail === 'object') {
    const code = typeof detail.code === 'string' ? detail.code : null;
    const message = typeof detail.message === 'string'
      ? detail.message
      : (typeof detail.detail === 'string' ? detail.detail : JSON.stringify(detail));
    const details = detail.details && typeof detail.details === 'object' ? detail.details : null;
    return { code, message, details };
  }
  return { code: null, message: String(detail), details: null };
}

export async function importMidi(file) {
  return importCompositionUpload('/imports/midi', file, { format: 'midi' });
}

export async function importMusicXml(file) {
  return importCompositionUpload('/imports/musicxml', file, { format: 'musicxml' });
}

/**
 * Upload audio for monophonic transcription. Returns preview only (no composition).
 * @param {Blob|File} file
 * @param {{ tempoBpm?: number|null, ticksPerQuarter?: number|null, originTick?: number }} options
 */
export async function transcribeAudio(file, options = {}) {
  const byteCount = typeof file?.size === 'number' ? file.size : null;
  console.debug('[musicApi] Audio transcription request started', {
    endpoint: '/transcription/audio',
    byteCount,
    hasTempo: options.tempoBpm != null,
  });
  const formData = new FormData();
  formData.append('file', file, file?.name || 'capture.wav');
  if (options.tempoBpm != null && Number.isFinite(Number(options.tempoBpm))) {
    formData.append('tempo_bpm', String(Math.round(Number(options.tempoBpm))));
  }
  if (options.ticksPerQuarter != null && Number.isFinite(Number(options.ticksPerQuarter))) {
    formData.append('ticks_per_quarter', String(Math.round(Number(options.ticksPerQuarter))));
  }
  if (options.originTick != null && Number.isFinite(Number(options.originTick))) {
    formData.append('origin_tick', String(Math.max(0, Math.round(Number(options.originTick)))));
  }

  try {
    const response = await axios.post('/transcription/audio', formData, {
      headers: { 'Content-Type': undefined },
      transformRequest: [
        (data, headers) => {
          if (typeof FormData !== 'undefined' && data instanceof FormData) {
            if (headers && typeof headers.set === 'function') {
              headers.set('Content-Type', false);
            } else if (headers) {
              delete headers['Content-Type'];
              delete headers['content-type'];
            }
          }
          return data;
        },
      ],
    });
    const payload = response.data || {};
    const preview = payload.preview || null;
    if (!preview || preview.schema_version !== 'transcription.preview.v1') {
      throw new TranscriptionApiError('Invalid transcription preview response', {
        status: response.status,
        code: 'audio_internal_error',
      });
    }
    console.debug('[musicApi] Audio transcription response received', {
      status: response.status,
      engineId: payload.engine?.id || preview.engine?.id || null,
      noteCount: preview.summary?.note_count ?? preview.notes?.length ?? 0,
      lowConfidenceCount: preview.summary?.low_confidence_count ?? null,
      retentionDeleted: payload.retention?.deleted === true,
    });
    return {
      preview,
      engine: payload.engine || preview.engine || null,
      retention: payload.retention || { deleted: true },
    };
  } catch (error) {
    if (error instanceof TranscriptionApiError) {
      throw error;
    }
    const status = error.response?.status ?? null;
    const parsed = parseImportErrorDetail(error.response?.data?.detail);
    console.error('[musicApi] Audio transcription request failed', {
      status,
      code: parsed.code,
      message: parsed.message,
    });
    throw new TranscriptionApiError(parsed.message || 'Audio transcription failed', {
      status,
      code: parsed.code,
      details: parsed.details,
    });
  }
}

async function importCompositionUpload(endpoint, file, { format }) {
  const byteCount = typeof file?.size === 'number' ? file.size : null;
  console.debug('[musicApi] Composition import request started', {
    format,
    endpoint,
    byteCount,
  });
  const formData = new FormData();
  formData.append('file', file);

  try {
    // Do not set multipart Content-Type manually; clear axios defaults so the
    // runtime can supply the correct boundary (browser) or leave FormData intact.
    const response = await axios.post(endpoint, formData, {
      headers: { 'Content-Type': undefined },
      transformRequest: [
        (data, headers) => {
          if (typeof FormData !== 'undefined' && data instanceof FormData) {
            if (headers && typeof headers.set === 'function') {
              headers.set('Content-Type', false);
            } else if (headers) {
              delete headers['Content-Type'];
              delete headers['content-type'];
            }
          }
          return data;
        },
      ],
    });
    const payload = response.data || {};
    const composition = normalizeApiComposition(payload.composition, {
      context: `${format}-import-response`,
    });
    const validation = validateMusicJson(composition);
    console.debug('[musicApi] Composition import response validated', {
      format,
      status: response.status,
      schemaVersion: composition.schema_version,
      valid: validation.valid,
      trackCount: composition.tracks?.length || 0,
      importStatus: payload.import_report?.status || null,
      importIssueCount: payload.import_report?.issues?.length || 0,
      hasNotationReport: Boolean(payload.notation_report),
    });
    if (!validation.valid) {
      console.error('[musicApi] Imported composition failed validation', {
        format,
        message: validation.message,
      });
      throw new ImportApiError(validation.message || 'Imported composition is invalid', {
        status: response.status,
        code: 'import_internal_error',
      });
    }
    return {
      composition,
      musicxml: typeof payload.musicxml === 'string' ? payload.musicxml : '',
      import_report: payload.import_report || null,
      notation_report: payload.notation_report || {},
    };
  } catch (error) {
    if (error instanceof ImportApiError) {
      throw error;
    }
    const status = error.response?.status ?? null;
    const parsed = parseImportErrorDetail(error.response?.data?.detail);
    console.error('[musicApi] Composition import request failed', {
      format,
      endpoint,
      status,
      code: parsed.code,
      message: parsed.message,
    });
    throw new ImportApiError(parsed.message, {
      status,
      code: parsed.code,
      details: parsed.details,
    });
  }
}

function parseImportErrorDetail(detail) {
  if (!detail) {
    return { code: null, message: 'Unknown import failure', details: null };
  }
  if (typeof detail === 'string') {
    return { code: null, message: detail, details: null };
  }
  if (typeof detail === 'object') {
    const code = typeof detail.code === 'string' ? detail.code : null;
    const message = typeof detail.message === 'string'
      ? detail.message
      : (typeof detail.detail === 'string' ? detail.detail : JSON.stringify(detail));
    const details = detail.details && typeof detail.details === 'object' ? detail.details : null;
    return { code, message, details };
  }
  return { code: null, message: String(detail), details: null };
}

async function exportComposition(composition, { endpoint, format, fallbackFilename, expectedType, download = true }) {
  const normalized = normalizeApiComposition(composition, { context: `${format}-export` });
  validateCanonicalForApi(normalized, { action: `${format} export` });
  const eventCount = Array.isArray(normalized.tracks)
    ? normalized.tracks.reduce((total, track) => total + (track.events?.length || 0), 0)
    : 0;
  console.debug('[musicApi] Export request started', {
    format,
    schemaVersion: normalized.schema_version,
    trackCount: normalized.tracks?.length || 0,
    eventCount,
    download,
  });

  try {
    const response = await axios.post(endpoint, normalized, { responseType: 'blob' });
    const blob = response.data;
    const contentType = response.headers?.['content-type'] || blob.type || expectedType;
    const filename = filenameFromContentDisposition(
      response.headers?.['content-disposition'],
      fallbackFilename,
    );
    const projection = parseProjectionHeaders(response.headers);
    const warnings = projectionWarningsFromHeaders(response.headers);
    console.debug('[musicApi] Export request completed', {
      format,
      schemaVersion: normalized.schema_version,
      trackCount: normalized.tracks.length,
      eventCount,
      blobSize: blob.size,
      contentType,
      filename,
      projectionStatus: projection.status,
      projectionIssueCount: projection.issues.length,
      download,
    });
    if (download) {
      downloadBlob(blob, filename);
    }
    return { blob, filename, contentType, projection, warnings };
  } catch (error) {
    const detail = await extractBlobErrorDetail(error);
    console.error('[musicApi] Export request failed', {
      format,
      status: error.response?.status,
      detail,
    });
    throw new Error(detail);
  }
}

async function extractBlobErrorDetail(error) {
  const data = error.response?.data;
  if (data instanceof Blob) {
    try {
      const text = await data.text();
      const parsed = JSON.parse(text);
      if (parsed?.detail) {
        return typeof parsed.detail === 'string' ? parsed.detail : JSON.stringify(parsed.detail);
      }
      return text || error.message || 'Unknown export failure';
    } catch {
      return error.message || 'Unknown export failure';
    }
  }
  return error.response?.data?.detail || error.message || 'Unknown export failure';
}

async function request(method, endpoint, data, config = {}) {
  console.debug('[musicApi] Request started', { method, endpoint });
  try {
    const response = await axios({ method, url: endpoint, data, ...config });
    console.debug('[musicApi] Request completed', { method, endpoint, status: response.status });
    return response.data;
  } catch (error) {
    const detail = formatAxiosFailureDetail(error);
    console.error('[musicApi] Request failed', {
      method,
      endpoint,
      status: error.response?.status,
      code: error.code || null,
      detail,
    });
    throw new Error(detail);
  }
}

/** Prefer API detail; make bare axios "Network Error" actionable. */
function formatAxiosFailureDetail(error) {
  const apiDetail = error?.response?.data?.detail;
  if (typeof apiDetail === 'string' && apiDetail.trim()) {
    return apiDetail;
  }
  if (apiDetail != null) {
    try {
      return JSON.stringify(apiDetail);
    } catch {
      return String(apiDetail);
    }
  }
  const message = typeof error?.message === 'string' ? error.message : '';
  const code = typeof error?.code === 'string' ? error.code : '';
  if (!error?.response && (message === 'Network Error' || code === 'ERR_NETWORK' || code === 'ECONNABORTED')) {
    return (
      'Network Error: no HTTP response (connection reset, proxy idle timeout, or browser abort). '
      + 'If generation was in progress, check backend logs; hard-refresh after restarting Vite/backend.'
    );
  }
  return message || 'Unknown request failure';
}

/**
 * POST /embeddings/compute — symbolic embedding card for a composition scope.
 * Never logs full vectors.
 */
export async function computeEmbedding(composition, scope = { kind: 'composition' }, modelId = null) {
  const inboundComposition = normalizeApiComposition(composition, {
    context: 'embedding-compute-request',
  });
  validateCanonicalForApi(inboundComposition, { action: 'embedding compute' });
  if (!isCanonicalComposition(inboundComposition)) {
    throw new EmbeddingApiError('Embedding accepts only composition.v2 documents', {
      code: 'embed_invalid_composition',
    });
  }
  const scopeResult = normalizeEmbedScope(scope);
  if (!scopeResult.ok) {
    throw new EmbeddingApiError(scopeResult.message, { code: scopeResult.code });
  }

  embeddingLogger.debug('Embedding compute started', {
    scopeKind: scopeResult.scope.kind,
    modelId: modelId || null,
  });

  try {
    const body = {
      composition: inboundComposition,
      scope: scopeResult.scope,
    };
    if (typeof modelId === 'string' && modelId.trim()) {
      body.model_id = modelId.trim();
    }
    const axiosResponse = await axios.post('/embeddings/compute', body);
    const response = axiosResponse.data || {};
    const embedding = response.embedding;
    if (!embedding || typeof embedding !== 'object' || !Array.isArray(embedding.vector)) {
      throw new EmbeddingApiError('Invalid embedding compute response', {
        code: 'embed_invalid_response',
      });
    }
    embeddingLogger.debug('Embedding compute ready', {
      dims: embedding.dims,
      noteCount: embedding.note_count,
      fingerprintPrefix: fingerprintPrefix(embedding.source_fingerprint),
      warningCodeCount: Array.isArray(response.warning_codes) ? response.warning_codes.length : 0,
    });
    return {
      embedding,
      warning_codes: Array.isArray(response.warning_codes) ? response.warning_codes.slice(0, 32) : [],
    };
  } catch (error) {
    if (error instanceof EmbeddingApiError) {
      throw error;
    }
    const status = error.response?.status ?? null;
    const parsed = parseMotifErrorDetail(error.response?.data?.detail ?? error.message);
    embeddingLogger.error('Embedding compute failed', {
      status,
      code: parsed.code,
    });
    throw new EmbeddingApiError(parsed.message, {
      status,
      code: parsed.code,
      details: parsed.details,
    });
  }
}

/**
 * POST /embeddings/similarity — ranked similar scopes (affinity, not quality).
 * @param {object} query composition.similarity_query.v1 (or partial with composition+scope)
 * @param {string|null} [modelId]
 */
export async function searchSimilarEmbeddings(query, modelId = null) {
  if (!query || typeof query !== 'object' || Array.isArray(query)) {
    throw new EmbeddingApiError('Similarity query is required', { code: 'similarity_query_invalid' });
  }

  let requestQuery = { ...query };
  if (requestQuery.composition) {
    requestQuery = {
      ...requestQuery,
      composition: normalizeApiComposition(requestQuery.composition, {
        context: 'embedding-similarity-request',
      }),
    };
    validateCanonicalForApi(requestQuery.composition, { action: 'embedding similarity' });
  }
  if (requestQuery.scope) {
    const scopeResult = normalizeEmbedScope(requestQuery.scope);
    if (!scopeResult.ok) {
      throw new EmbeddingApiError(scopeResult.message, { code: scopeResult.code });
    }
    requestQuery.scope = scopeResult.scope;
  }

  embeddingLogger.debug('Similarity search started', {
    scopeKind: requestQuery.scope?.kind || null,
    topK: requestQuery.top_k ?? null,
    corpusKind: requestQuery.corpus?.kind || 'projects',
    excludeProjectId: requestQuery.exclude_project_id || null,
  });

  try {
    const body = { query: requestQuery };
    if (typeof modelId === 'string' && modelId.trim()) {
      body.model_id = modelId.trim();
    }
    const axiosResponse = await axios.post('/embeddings/similarity', body);
    const response = axiosResponse.data || {};
    const hits = normalizeSimilarityHits(response.hits, {
      topK: requestQuery.top_k || 10,
    });
    embeddingLogger.debug('Similarity search ready', {
      hitCount: hits.length,
      queryFingerprintPrefix: response.query_fingerprint_prefix || null,
      modelId: response.model_id || null,
    });
    return {
      hits,
      rawHits: Array.isArray(response.hits) ? response.hits : [],
      query_fingerprint_prefix: response.query_fingerprint_prefix || null,
      model_id: response.model_id || null,
      profile_id: response.profile_id || null,
      warning_codes: Array.isArray(response.warning_codes) ? response.warning_codes.slice(0, 32) : [],
      musical_quality_claim: false,
    };
  } catch (error) {
    if (error instanceof EmbeddingApiError) {
      throw error;
    }
    const status = error.response?.status ?? null;
    const parsed = parseMotifErrorDetail(error.response?.data?.detail ?? error.message);
    embeddingLogger.error('Similarity search failed', {
      status,
      code: parsed.code,
    });
    throw new EmbeddingApiError(parsed.message, {
      status,
      code: parsed.code,
      details: parsed.details,
    });
  }
}

/**
 * POST /embeddings/related-motifs — related motif affinity within/across projects.
 */
export async function searchRelatedMotifs({
  composition,
  motifId,
  occurrenceId = null,
  topK = 8,
  searchCrossProject = false,
  modelId = null,
} = {}) {
  if (typeof motifId !== 'string' || !motifId.trim()) {
    throw new EmbeddingApiError('motif_id is required', { code: 'embed_scope_invalid' });
  }
  const inboundComposition = normalizeApiComposition(composition, {
    context: 'embedding-related-motifs-request',
  });
  validateCanonicalForApi(inboundComposition, { action: 'related motifs' });
  if (!isCanonicalComposition(inboundComposition)) {
    throw new EmbeddingApiError('Related motifs accept only composition.v2 documents', {
      code: 'embed_invalid_composition',
    });
  }

  embeddingLogger.debug('Related motifs search started', {
    motifId: motifId.trim(),
    occurrenceId: occurrenceId || null,
    topK,
    searchCrossProject: Boolean(searchCrossProject),
  });

  try {
    const body = {
      composition: inboundComposition,
      motif_id: motifId.trim().slice(0, 120),
      top_k: Math.min(50, Math.max(1, Number.isInteger(topK) ? topK : 8)),
      search_cross_project: Boolean(searchCrossProject),
    };
    if (typeof occurrenceId === 'string' && occurrenceId.trim()) {
      body.occurrence_id = occurrenceId.trim().slice(0, 120);
    }
    if (typeof modelId === 'string' && modelId.trim()) {
      body.model_id = modelId.trim();
    }
    const axiosResponse = await axios.post('/embeddings/related-motifs', body);
    const response = axiosResponse.data || {};
    const hits = normalizeSimilarityHits(response.hits, { topK: body.top_k });
    embeddingLogger.debug('Related motifs search ready', {
      hitCount: hits.length,
      modelId: response.model_id || null,
    });
    return {
      hits,
      rawHits: Array.isArray(response.hits) ? response.hits : [],
      model_id: response.model_id || null,
      warning_codes: Array.isArray(response.warning_codes) ? response.warning_codes.slice(0, 32) : [],
      musical_quality_claim: false,
    };
  } catch (error) {
    if (error instanceof EmbeddingApiError) {
      throw error;
    }
    const status = error.response?.status ?? null;
    const parsed = parseMotifErrorDetail(error.response?.data?.detail ?? error.message);
    embeddingLogger.error('Related motifs search failed', {
      status,
      code: parsed.code,
    });
    throw new EmbeddingApiError(parsed.message, {
      status,
      code: parsed.code,
      details: parsed.details,
    });
  }
}

/**
 * POST /embeddings/reference/resolve — resolve style_reference to embedding + provenance.
 * Optional helper for advisory score / fingerprint gates.
 */
export async function resolveMusicalReference(styleReference, modelId = null) {
  const normalized = normalizeStyleReference(styleReference);
  if (!normalized.ok) {
    throw new EmbeddingApiError(normalized.message, { code: normalized.code });
  }
  if (!normalized.styleReference) {
    throw new EmbeddingApiError('style_reference is required', { code: 'style_reference_invalid' });
  }

  const requestRef = { ...normalized.styleReference };
  if (requestRef.composition) {
    requestRef.composition = normalizeApiComposition(requestRef.composition, {
      context: 'embedding-reference-resolve',
    });
    validateCanonicalForApi(requestRef.composition, { action: 'musical reference resolve' });
  }

  embeddingLogger.debug('Musical reference resolve started', {
    projectId: requestRef.project_id || null,
    scopeKind: requestRef.scope?.kind || null,
    mode: requestRef.mode || null,
  });

  try {
    const body = { style_reference: requestRef };
    if (typeof modelId === 'string' && modelId.trim()) {
      body.model_id = modelId.trim();
    }
    const axiosResponse = await axios.post('/embeddings/reference/resolve', body);
    const response = axiosResponse.data || {};
    if (!response.embedding || !response.provenance) {
      throw new EmbeddingApiError('Invalid reference resolve response', {
        code: 'reference_resolve_invalid',
      });
    }
    embeddingLogger.debug('Musical reference resolve ready', {
      projectId: response.provenance?.project_id || null,
      fingerprintPrefix: fingerprintPrefix(response.provenance?.source_fingerprint),
      dims: response.embedding?.dims ?? null,
    });
    return {
      embedding: response.embedding,
      provenance: response.provenance,
      conditioning: response.conditioning || null,
      warning_codes: Array.isArray(response.warning_codes) ? response.warning_codes.slice(0, 32) : [],
    };
  } catch (error) {
    if (error instanceof EmbeddingApiError) {
      throw error;
    }
    const status = error.response?.status ?? null;
    const parsed = parseMotifErrorDetail(error.response?.data?.detail ?? error.message);
    embeddingLogger.error('Musical reference resolve failed', {
      status,
      code: parsed.code,
    });
    throw new EmbeddingApiError(parsed.message, {
      status,
      code: parsed.code,
      details: parsed.details,
    });
  }
}

/**
 * POST /reference-features/analyze — derived reference.features.v1 report (no V2 mutation).
 */
export async function analyzeReferenceFeatures(payload = {}) {
  const body = { ...payload };
  if (body.composition) {
    body.composition = normalizeApiComposition(body.composition, {
      context: 'reference-features-analyze',
    });
    validateCanonicalForApi(body.composition, { action: 'reference features analyze' });
  }
  if (!body.scope) {
    body.scope = { kind: 'composition' };
  } else {
    const scopeResult = normalizeEmbedScope(body.scope);
    if (!scopeResult.ok) {
      throw new EmbeddingApiError(scopeResult.message, { code: scopeResult.code });
    }
    body.scope = scopeResult.scope;
  }

  embeddingLogger.debug('Reference features analyze started', {
    projectId: body.project_id || null,
    scopeKind: body.scope?.kind || null,
    dimensionCount: Array.isArray(body.requested_dimensions)
      ? body.requested_dimensions.length
      : null,
    hasCompareTo: Boolean(body.compare_to),
  });

  try {
    const axiosResponse = await axios.post('/reference-features/analyze', body);
    const response = axiosResponse.data || {};
    if (!response.report || response.report.schema_version !== 'reference.features.v1') {
      throw new EmbeddingApiError('Invalid reference features response', {
        code: 'reference_feature_invalid',
      });
    }
    embeddingLogger.debug('Reference features analyze ready', {
      dimensionCount: Object.keys(response.report.dimensions || {}).length,
      unavailableCount: Array.isArray(response.report.unavailable)
        ? response.report.unavailable.length
        : 0,
      hasAffinity: Boolean(response.report.embedding_affinity),
    });
    return {
      report: response.report,
      warning_codes: Array.isArray(response.warning_codes)
        ? response.warning_codes.slice(0, 32)
        : [],
    };
  } catch (error) {
    if (error instanceof EmbeddingApiError) {
      throw error;
    }
    const status = error.response?.status ?? null;
    const parsed = parseMotifErrorDetail(error.response?.data?.detail ?? error.message);
    embeddingLogger.error('Reference features analyze failed', {
      status,
      code: parsed.code,
    });
    throw new EmbeddingApiError(parsed.message, {
      status,
      code: parsed.code || 'reference_feature_invalid',
      details: parsed.details,
    });
  }
}

const neuralAudioLogger = createAppLogger('musicApi.neuralAudio');

function parseNeuralAudioError(error) {
  const status = error?.response?.status ?? null;
  const detail = error?.response?.data?.detail;
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    return {
      status,
      code: typeof detail.code === 'string' ? detail.code : 'neural_audio_error',
      message: typeof detail.message === 'string' ? detail.message : 'Neural audio request failed',
      details: detail.details && typeof detail.details === 'object' ? detail.details : {},
    };
  }
  if (typeof detail === 'string' && detail.trim()) {
    return { status, code: 'neural_audio_error', message: detail, details: {} };
  }
  return {
    status,
    code: 'neural_audio_error',
    message: error?.message || 'Neural audio request failed',
    details: {},
  };
}

export class NeuralAudioApiError extends Error {
  constructor(message, { status = null, code = 'neural_audio_error', details = {} } = {}) {
    super(message);
    this.name = 'NeuralAudioApiError';
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

/**
 * Enqueue a neural audio render. Never mutates composition on the server.
 * Prefer project_id + source_revision_id when Versions exist.
 */
export async function enqueueNeuralAudioRender(payload) {
  const body = {
    instructions: typeof payload?.instructions === 'string' ? payload.instructions : '',
    genre: payload?.genre || null,
    mood: payload?.mood || null,
    model_id: payload?.model_id || null,
    adapter_kind: payload?.adapter_kind || null,
    tempo_bpm: payload?.tempo_bpm ?? null,
    instrumentation_summary: payload?.instrumentation_summary || null,
    seed: payload?.seed ?? null,
  };
  if (payload?.project_id && payload?.source_revision_id) {
    body.project_id = payload.project_id;
    body.source_revision_id = payload.source_revision_id;
  }
  if (payload?.composition) {
    validateCanonicalForApi(payload.composition, { action: 'neural-audio-render' });
    body.composition = payload.composition;
  }
  neuralAudioLogger.info('Enqueue neural audio render', {
    hasProjectId: Boolean(body.project_id),
    hasRevision: Boolean(body.source_revision_id),
    hasComposition: Boolean(body.composition),
    modelId: body.model_id,
    adapterKind: body.adapter_kind,
    instructionChars: body.instructions.length,
  });
  try {
    const job = await request('post', '/neural-audio/renders', body);
    neuralAudioLogger.info('Neural audio enqueue response', {
      renderId: job?.id || null,
      status: job?.status || null,
      fidelityClass: job?.fidelity_class || null,
      modelId: job?.model_id || null,
    });
    return job;
  } catch (error) {
    const parsed = parseNeuralAudioError(error);
    neuralAudioLogger.error('Neural audio enqueue failed', {
      status: parsed.status,
      code: parsed.code,
    });
    throw new NeuralAudioApiError(parsed.message, parsed);
  }
}

export async function listNeuralAudioRenders(projectId) {
  neuralAudioLogger.info('List neural audio renders', { projectId });
  try {
    return await request('get', '/neural-audio/renders', null, {
      params: { project_id: projectId },
    });
  } catch (error) {
    const parsed = parseNeuralAudioError(error);
    throw new NeuralAudioApiError(parsed.message, parsed);
  }
}

export async function getNeuralAudioRender(renderId) {
  try {
    return await request('get', `/neural-audio/renders/${encodeURIComponent(renderId)}`);
  } catch (error) {
    const parsed = parseNeuralAudioError(error);
    throw new NeuralAudioApiError(parsed.message, parsed);
  }
}

export async function downloadNeuralAudioRender(renderId) {
  neuralAudioLogger.info('Download neural audio render', { renderId });
  try {
    const response = await axios.get(
      `/neural-audio/renders/${encodeURIComponent(renderId)}/audio`,
      { responseType: 'blob' },
    );
    const filename =
      filenameFromContentDisposition(response.headers?.['content-disposition'])
      || `neural-audio-${renderId}.wav`;
    downloadBlob(response.data, filename);
    return { filename, byteSize: response.data?.size ?? null };
  } catch (error) {
    const parsed = parseNeuralAudioError(error);
    throw new NeuralAudioApiError(parsed.message, parsed);
  }
}

export async function deleteNeuralAudioRender(renderId) {
  neuralAudioLogger.info('Delete neural audio render', { renderId });
  try {
    await request('delete', `/neural-audio/renders/${encodeURIComponent(renderId)}`);
  } catch (error) {
    const parsed = parseNeuralAudioError(error);
    throw new NeuralAudioApiError(parsed.message, parsed);
  }
}

const audioRecoveryLogger = createAppLogger('musicApi.audioRecovery');

function parseAudioRecoveryError(error) {
  const status = error?.response?.status ?? null;
  const detail = error?.response?.data?.detail;
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    return {
      status,
      code: typeof detail.code === 'string' ? detail.code : 'audio_recovery_error',
      message: typeof detail.message === 'string' ? detail.message : 'Audio recovery request failed',
      details: detail.details && typeof detail.details === 'object' ? detail.details : {},
    };
  }
  if (typeof detail === 'string' && detail.trim()) {
    return { status, code: 'audio_recovery_error', message: detail, details: {} };
  }
  return {
    status,
    code: 'audio_recovery_error',
    message: error?.message || 'Audio recovery request failed',
    details: {},
  };
}

export class AudioRecoveryApiError extends Error {
  constructor(message, { status = null, code = 'audio_recovery_error', details = {} } = {}) {
    super(message);
    this.name = 'AudioRecoveryApiError';
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

/**
 * POST /audio-recovery/jobs — multipart upload; often returns complete when run_inline.
 * @param {Blob|File} file
 * @param {{ projectId?: string|null, disableSeparation?: boolean }} [options]
 */
export async function enqueueAudioRecoveryJob(file, options = {}) {
  const byteCount = typeof file?.size === 'number' ? file.size : null;
  audioRecoveryLogger.info('Enqueue audio recovery job', {
    byteCount,
    hasProjectId: Boolean(options.projectId),
    disableSeparation: Boolean(options.disableSeparation),
  });
  const formData = new FormData();
  formData.append('file', file, file?.name || 'demo.wav');
  if (options.projectId) {
    formData.append('project_id', String(options.projectId));
  }
  if (options.disableSeparation) {
    formData.append('disable_separation', 'true');
  }
  try {
    const response = await axios.post('/audio-recovery/jobs', formData, {
      headers: { 'Content-Type': undefined },
      transformRequest: [
        (data, headers) => {
          if (typeof FormData !== 'undefined' && data instanceof FormData) {
            if (headers && typeof headers.set === 'function') {
              headers.set('Content-Type', false);
            } else if (headers) {
              delete headers['Content-Type'];
              delete headers['content-type'];
            }
          }
          return data;
        },
      ],
    });
    const job = response.data || {};
    audioRecoveryLogger.info('Audio recovery enqueue response', {
      jobId: job?.id || null,
      status: job?.status || null,
      noteCount: job?.preview?.summary?.note_count ?? null,
      stemCount: job?.preview?.summary?.stem_count ?? null,
    });
    return job;
  } catch (error) {
    const parsed = parseAudioRecoveryError(error);
    audioRecoveryLogger.error('Audio recovery enqueue failed', {
      status: parsed.status,
      code: parsed.code,
    });
    throw new AudioRecoveryApiError(parsed.message, parsed);
  }
}

export async function getAudioRecoveryJob(jobId) {
  audioRecoveryLogger.info('Get audio recovery job', { jobId });
  try {
    return await request('get', `/audio-recovery/jobs/${encodeURIComponent(jobId)}`);
  } catch (error) {
    const parsed = parseAudioRecoveryError(error);
    throw new AudioRecoveryApiError(parsed.message, parsed);
  }
}

export async function deleteAudioRecoveryJob(jobId) {
  audioRecoveryLogger.info('Delete audio recovery job', { jobId });
  try {
    await request('delete', `/audio-recovery/jobs/${encodeURIComponent(jobId)}`);
  } catch (error) {
    const parsed = parseAudioRecoveryError(error);
    throw new AudioRecoveryApiError(parsed.message, parsed);
  }
}

/**
 * POST /audio-recovery/jobs/{id}/bind — durable assets after client V2 Apply.
 * @param {string} jobId
 * @param {{
 *   project_id: string,
 *   preview_fingerprint: string,
 *   event_map: Array<{ provisional_id: string, event_id: string, track_id: string }>,
 * }} body
 */
export async function bindAudioRecoveryJob(jobId, body) {
  audioRecoveryLogger.info('Bind audio recovery job', {
    jobId,
    projectId: body?.project_id || null,
    eventMapCount: Array.isArray(body?.event_map) ? body.event_map.length : 0,
  });
  try {
    const result = await request(
      'post',
      `/audio-recovery/jobs/${encodeURIComponent(jobId)}/bind`,
      body,
    );
    audioRecoveryLogger.info('Audio recovery bind response', {
      jobId,
      sourceAudioAssetId: result?.source_audio_asset_id || null,
      resultAssetId: result?.result_asset_id || null,
      overlayEntryCount: result?.overlay_entry_count ?? null,
    });
    return result;
  } catch (error) {
    const parsed = parseAudioRecoveryError(error);
    audioRecoveryLogger.warn('Audio recovery bind failed', {
      status: parsed.status,
      code: parsed.code,
    });
    throw new AudioRecoveryApiError(parsed.message, parsed);
  }
}

/**
 * GET /audio-recovery/assets/{id} — blob URL for HTMLAudioElement (caller revokes).
 * @param {string} assetId
 * @returns {Promise<{ blobUrl: string, contentType: string|null, byteSize: number|null }>}
 */
export async function fetchAudioRecoveryAssetBlobUrl(assetId) {
  audioRecoveryLogger.info('Fetch recovery asset blob', { assetId });
  try {
    const response = await axios.get(
      `/audio-recovery/assets/${encodeURIComponent(assetId)}`,
      { responseType: 'blob' },
    );
    const blobUrl = URL.createObjectURL(response.data);
    return {
      blobUrl,
      contentType: response.headers?.['content-type'] || null,
      byteSize: response.data?.size ?? null,
    };
  } catch (error) {
    const parsed = parseAudioRecoveryError(error);
    throw new AudioRecoveryApiError(parsed.message, parsed);
  }
}

/**
 * GET /ai/agents — V4 multi-agent discovery (never exposes secrets).
 */
export async function fetchAiAgents(params = {}) {
  const query = {};
  if (params.capability) query.capability = params.capability;
  if (params.status) query.status = params.status;
  const response = await request('get', '/ai/agents', null, { params: query });
  console.debug('[musicApi] AI agent catalog loaded', {
    agentCount: Array.isArray(response?.agents) ? response.agents.length : 0,
  });
  return response;
}

/**
 * POST /ai/agents/workflows/preview — session candidate only; never mutates a project.
 */
export async function previewMultiAgentWorkflow(payload, { signal } = {}) {
  const composition = normalizeApiComposition(payload.composition, {
    context: 'multi-agent-workflow-preview-request',
  });
  validateCanonicalForApi(composition, { action: 'multi-agent workflow preview' });
  const revisionMode = payload.revision_mode || 'off';
  console.info('[musicApi] Multi-agent workflow preview started', {
    workflowId: payload.workflow_id || 'agent_spine_v1',
    maxRevisions: payload.max_revisions ?? 0,
    revisionMode,
  });
  const axiosResponse = await axios.post(
    '/ai/agents/workflows/preview',
    {
      composition,
      brief: payload.brief || null,
      workflow_id: payload.workflow_id || 'agent_spine_v1',
      max_revisions: payload.max_revisions ?? 0,
      revision_mode: revisionMode,
      max_wall_ms: payload.max_wall_ms ?? null,
      max_prompt_tokens: payload.max_prompt_tokens ?? null,
      agent_model_overrides: payload.agent_model_overrides || {},
      selection: payload.selection || {},
      critic_parameters: payload.critic_parameters || {},
    },
    signal ? { signal } : undefined,
  );
  const response = axiosResponse.data || {};
  const candidate = normalizeApiComposition(response.candidate, {
    context: 'multi-agent-workflow-preview-response',
  });
  console.info('[musicApi] Multi-agent workflow preview ready', {
    workflowId: response.workflow_id,
    agentSequenceLen: Array.isArray(response.agent_sequence) ? response.agent_sequence.length : 0,
    recommendation: response.recommendation || null,
    stopReason: response.stop_reason || null,
    revisionMode: response.revision_mode || revisionMode,
    passCount: Array.isArray(response.revision_history) ? response.revision_history.length : 0,
    passCandidateCount: Array.isArray(response.pass_candidates)
      ? response.pass_candidates.length
      : 0,
    mutatesComposition: response.mutates_composition === true,
  });
  const passCandidates = Array.isArray(response.pass_candidates)
    ? response.pass_candidates.map((item) => ({
      pass_index: item.pass_index,
      candidate_fingerprint: item.candidate_fingerprint || '',
      composition: normalizeApiComposition(item.composition, {
        context: 'multi-agent-pass-candidate',
      }),
    })).filter((item) => item.composition)
    : [];
  return {
    ...response,
    candidate,
    pass_candidates: passCandidates,
  };
}
