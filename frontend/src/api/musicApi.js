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

export async function exportMusicXml(composition) {
  return exportComposition(composition, {
    endpoint: '/export/musicxml',
    format: 'musicxml',
    fallbackFilename: 'composition.musicxml',
    expectedType: 'application/vnd.recordare.musicxml+xml',
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

export async function exportMidi(composition) {
  return exportComposition(composition, {
    endpoint: '/export/midi',
    format: 'midi',
    fallbackFilename: 'composition.mid',
    expectedType: 'audio/midi',
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

async function exportComposition(composition, { endpoint, format, fallbackFilename, expectedType }) {
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
    });
    downloadBlob(blob, filename);
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
    const detail = error.response?.data?.detail || error.message || 'Unknown request failure';
    console.error('[musicApi] Request failed', {
      method,
      endpoint,
      status: error.response?.status,
      detail,
    });
    throw new Error(detail);
  }
}
