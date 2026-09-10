/**
 * Versioned, allowlisted browser playback asset manifest helpers.
 *
 * Composition-provided URLs are never accepted. Only same-origin paths under
 * an allowlisted pack prefix are resolved. Provenance/license metadata is
 * validated structurally; license file contents are never logged.
 */

import { createAppLogger } from './appLogger.js';

const logger = createAppLogger('browserPlaybackAssets');

export const BROWSER_PLAYBACK_MANIFEST_SCHEMA = 'browser.playback.assets.v1';

/** Default pack size budgets (bytes / decoded estimate). */
export const ASSET_BUDGETS = Object.freeze({
  maxPackBytes: 12 * 1024 * 1024,
  maxFileBytes: 2 * 1024 * 1024,
  maxDecodedBytesEstimate: 48 * 1024 * 1024,
  maxConcurrentDecodes: 3,
  allowedFormats: Object.freeze(['audio/ogg', 'audio/mpeg', 'audio/wav', 'audio/webm']),
  allowedExtensions: Object.freeze(['.ogg', '.mp3', '.wav', '.webm']),
});

const ALLOWED_SCHEMAS = new Set([BROWSER_PLAYBACK_MANIFEST_SCHEMA]);

/**
 * Validate a browser playback asset manifest.
 * @param {unknown} raw
 * @param {{ budgets?: typeof ASSET_BUDGETS }} [options]
 * @returns {{
 *   ok: boolean,
 *   manifest?: object,
 *   errors: string[],
 *   warnings: string[],
 * }}
 */
export function validateBrowserPlaybackManifest(raw, options = {}) {
  const budgets = { ...ASSET_BUDGETS, ...(options.budgets || {}) };
  const errors = [];
  const warnings = [];

  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    errors.push('manifest_not_object');
    logger.error('Manifest validation failed', { code: 'manifest_not_object' });
    return { ok: false, errors, warnings };
  }

  const schema = String(raw.schema || '');
  if (!ALLOWED_SCHEMAS.has(schema)) {
    errors.push('unsupported_schema');
  }

  const packId = typeof raw.packId === 'string' ? raw.packId.trim() : '';
  if (!packId || !/^[a-z0-9][a-z0-9._-]{0,63}$/i.test(packId)) {
    errors.push('invalid_pack_id');
  }

  const version = typeof raw.version === 'string' ? raw.version.trim() : '';
  if (!version) {
    errors.push('missing_pack_version');
  }

  if (!Array.isArray(raw.instruments) || raw.instruments.length === 0) {
    errors.push('instruments_required');
  }

  const instruments = [];
  let totalBytes = 0;

  if (Array.isArray(raw.instruments)) {
    raw.instruments.forEach((entry, index) => {
      const result = validateInstrumentEntry(entry, budgets, packId, index);
      errors.push(...result.errors);
      warnings.push(...result.warnings);
      if (result.instrument) {
        instruments.push(result.instrument);
        totalBytes += result.instrument.totalBytes;
      }
    });
  }

  if (totalBytes > budgets.maxPackBytes) {
    errors.push('pack_byte_budget_exceeded');
  }

  if (errors.length) {
    logger.error('Manifest validation failed', {
      packId: packId || null,
      errorCount: errors.length,
      codes: errors.slice(0, 12),
    });
    return { ok: false, errors, warnings };
  }

  const manifest = {
    schema,
    packId,
    version,
    basePath: normalizePackBasePath(raw.basePath, packId),
    budgets: {
      maxPackBytes: budgets.maxPackBytes,
      maxFileBytes: budgets.maxFileBytes,
      maxDecodedBytesEstimate: budgets.maxDecodedBytesEstimate,
      maxConcurrentDecodes: budgets.maxConcurrentDecodes,
    },
    instruments,
    totalBytes,
    provenance: normalizeProvenance(raw.provenance),
  };

  logger.debug('Manifest validated', {
    packId: manifest.packId,
    version: manifest.version,
    instrumentCount: instruments.length,
    totalBytes: manifest.totalBytes,
  });

  return { ok: true, manifest, errors, warnings };
}

/**
 * Resolve a sample path to a same-origin URL under the pack base.
 * Rejects absolute http(s) URLs, protocol-relative URLs, and path traversal.
 *
 * @param {object} manifest validated manifest
 * @param {string} relativePath
 * @param {{ origin?: string }} [options]
 * @returns {{ ok: boolean, url?: string, cacheKey?: string, reason?: string }}
 */
export function resolveLocalAssetUrl(manifest, relativePath, options = {}) {
  if (!manifest?.packId || !manifest?.basePath) {
    return { ok: false, reason: 'missing_manifest' };
  }
  const rel = String(relativePath || '').replace(/^\/+/, '');
  if (!rel || rel.includes('..') || rel.includes('\\') || rel.includes('://')) {
    logger.warn('Rejected asset path', { reason: 'unsafe_relative_path', packId: manifest.packId });
    return { ok: false, reason: 'unsafe_relative_path' };
  }
  if (!ASSET_BUDGETS.allowedExtensions.some((ext) => rel.toLowerCase().endsWith(ext))) {
    logger.warn('Rejected asset path', { reason: 'unsupported_format', packId: manifest.packId });
    return { ok: false, reason: 'unsupported_format' };
  }

  const path = `${manifest.basePath.replace(/\/?$/, '/')}${rel}`;
  if (!path.startsWith(`/audio/${manifest.packId}/`)) {
    return { ok: false, reason: 'outside_pack_prefix' };
  }

  const origin = typeof options.origin === 'string' && options.origin
    ? options.origin.replace(/\/$/, '')
    : '';
  const url = origin ? `${origin}${path}` : path;
  const cacheKey = buildAssetCacheKey(manifest.packId, manifest.version, rel);

  logger.debug('Resolved local asset URL', {
    packId: manifest.packId,
    cacheKey,
    sameOrigin: !origin || true,
  });

  return { ok: true, url, cacheKey };
}

/**
 * Deterministic instrument profile mapping from a composition track.
 * @param {object} track
 * @param {object} manifest validated manifest
 * @param {{ fallbackPresetId?: string }} [options]
 * @returns {{
 *   profileId: string,
 *   sampled: boolean,
 *   fallbackPresetId: string,
 *   reason: string,
 *   sampleRefs: object[],
 * }}
 */
export function mapTrackToAssetProfile(track, manifest, options = {}) {
  const fallbackPresetId = options.fallbackPresetId
    || inferFallbackPresetId(track)
    || 'piano_keyboard';

  if (!manifest?.instruments?.length) {
    logger.warn('No manifest instruments; synth fallback', {
      trackId: track?.id,
      fallbackPresetId,
      reason: 'missing_manifest_instruments',
    });
    return {
      profileId: fallbackPresetId,
      sampled: false,
      fallbackPresetId,
      reason: 'missing_manifest_instruments',
      sampleRefs: [],
    };
  }

  const instrumentName = String(track?.instrument || '').toLowerCase();
  const role = String(track?.role || '').toLowerCase();
  const program = Number.isFinite(Number(track?.midi_program))
    ? Math.max(0, Math.min(127, Number(track.midi_program)))
    : null;
  const isDrum = Boolean(track?.is_drum) || Number(track?.channel) === 10;

  const byId = (predicate, reason) => {
    const match = manifest.instruments.find(predicate);
    if (!match) {
      return null;
    }
    return {
      profileId: match.profileId,
      sampled: true,
      fallbackPresetId: match.fallbackPresetId || fallbackPresetId,
      reason,
      sampleRefs: match.samples || [],
    };
  };

  if (isDrum) {
    const drum = byId((entry) => entry.roleHints?.includes('drums') || entry.profileId.includes('drum'), 'drums_identity');
    if (drum) {
      return drum;
    }
  }

  if (instrumentName) {
    const byName = byId(
      (entry) => (entry.instrumentNames || []).some((name) => instrumentName.includes(String(name).toLowerCase())),
      'instrument_name',
    );
    if (byName) {
      return byName;
    }
  }

  if (program !== null) {
    const byProgram = byId(
      (entry) => Array.isArray(entry.gmPrograms) && entry.gmPrograms.includes(program),
      'gm_program',
    );
    if (byProgram) {
      return byProgram;
    }
  }

  if (role) {
    const byRole = byId(
      (entry) => (entry.roleHints || []).some((hint) => role.includes(String(hint).toLowerCase())),
      'role_hint_fallback',
    );
    if (byRole) {
      return byRole;
    }
  }

  logger.warn('Unsupported track mapping; synth fallback', {
    trackId: track?.id,
    fallbackPresetId,
    reason: 'no_profile_match',
  });

  return {
    profileId: fallbackPresetId,
    sampled: false,
    fallbackPresetId,
    reason: 'no_profile_match',
    sampleRefs: [],
  };
}

/**
 * @param {string} packId
 * @param {string} version
 * @param {string} relativePath
 * @returns {string}
 */
export function buildAssetCacheKey(packId, version, relativePath) {
  return `bpav1:${packId}@${version}:${relativePath}`;
}

function validateInstrumentEntry(entry, budgets, packId, index) {
  const errors = [];
  const warnings = [];
  if (!entry || typeof entry !== 'object') {
    errors.push(`instrument_${index}_invalid`);
    return { errors, warnings, instrument: null };
  }

  const profileId = typeof entry.profileId === 'string' ? entry.profileId.trim() : '';
  if (!profileId) {
    errors.push(`instrument_${index}_missing_profile_id`);
  }

  const fallbackPresetId = typeof entry.fallbackPresetId === 'string'
    ? entry.fallbackPresetId.trim()
    : '';
  if (!fallbackPresetId) {
    errors.push(`instrument_${index}_missing_fallback_preset`);
  }

  if (!Array.isArray(entry.samples) || entry.samples.length === 0) {
    errors.push(`instrument_${index}_samples_required`);
  }

  const samples = [];
  let totalBytes = 0;
  if (Array.isArray(entry.samples)) {
    entry.samples.forEach((sample, sampleIndex) => {
      const validated = validateSampleEntry(sample, budgets, index, sampleIndex);
      errors.push(...validated.errors);
      warnings.push(...validated.warnings);
      if (validated.sample) {
        samples.push(validated.sample);
        totalBytes += validated.sample.bytes;
      }
    });
  }

  const provenance = normalizeProvenance(entry.provenance);
  if (!provenance?.license || !provenance?.sourceUrl || !provenance?.sourceHash) {
    errors.push(`instrument_${index}_provenance_incomplete`);
  }

  if (errors.length) {
    return { errors, warnings, instrument: null };
  }

  return {
    errors,
    warnings,
    instrument: {
      profileId,
      fallbackPresetId,
      instrumentNames: Array.isArray(entry.instrumentNames)
        ? entry.instrumentNames.map((name) => String(name).toLowerCase())
        : [],
      gmPrograms: Array.isArray(entry.gmPrograms)
        ? entry.gmPrograms.map((value) => Number(value)).filter((value) => Number.isFinite(value))
        : [],
      roleHints: Array.isArray(entry.roleHints)
        ? entry.roleHints.map((hint) => String(hint).toLowerCase())
        : [],
      samples,
      totalBytes,
      provenance,
    },
  };
}

function validateSampleEntry(sample, budgets, instrumentIndex, sampleIndex) {
  const errors = [];
  const warnings = [];
  if (!sample || typeof sample !== 'object') {
    errors.push(`sample_${instrumentIndex}_${sampleIndex}_invalid`);
    return { errors, warnings, sample: null };
  }

  const path = typeof sample.path === 'string' ? sample.path.trim().replace(/^\/+/, '') : '';
  if (!path || path.includes('..') || path.includes('://')) {
    errors.push(`sample_${instrumentIndex}_${sampleIndex}_unsafe_path`);
  }

  const bytes = Number(sample.bytes);
  if (!Number.isFinite(bytes) || bytes <= 0) {
    errors.push(`sample_${instrumentIndex}_${sampleIndex}_invalid_bytes`);
  } else if (bytes > budgets.maxFileBytes) {
    errors.push(`sample_${instrumentIndex}_${sampleIndex}_file_budget_exceeded`);
  }

  const digest = typeof sample.digest === 'string' ? sample.digest.trim().toLowerCase() : '';
  if (!/^sha256:[a-f0-9]{64}$/.test(digest)) {
    errors.push(`sample_${instrumentIndex}_${sampleIndex}_invalid_digest`);
  }

  const format = typeof sample.format === 'string' ? sample.format.trim().toLowerCase() : '';
  if (!budgets.allowedFormats.includes(format)) {
    errors.push(`sample_${instrumentIndex}_${sampleIndex}_unsupported_format`);
  }

  if (errors.length) {
    return { errors, warnings, sample: null };
  }

  return {
    errors,
    warnings,
    sample: {
      path,
      bytes,
      digest,
      format,
      pitch: typeof sample.pitch === 'string' ? sample.pitch : null,
      velocityMin: Number.isFinite(Number(sample.velocityMin)) ? Number(sample.velocityMin) : 0,
      velocityMax: Number.isFinite(Number(sample.velocityMax)) ? Number(sample.velocityMax) : 127,
      loopStart: Number.isFinite(Number(sample.loopStart)) ? Number(sample.loopStart) : null,
      loopEnd: Number.isFinite(Number(sample.loopEnd)) ? Number(sample.loopEnd) : null,
    },
  };
}

function normalizePackBasePath(rawBasePath, packId) {
  const fallback = `/audio/${packId}/`;
  if (typeof rawBasePath !== 'string' || !rawBasePath.trim()) {
    return fallback;
  }
  let path = rawBasePath.trim();
  if (path.includes('://') || path.includes('..') || path.startsWith('//')) {
    return fallback;
  }
  if (!path.startsWith('/')) {
    path = `/${path}`;
  }
  if (!path.endsWith('/')) {
    path = `${path}/`;
  }
  if (!path.startsWith(`/audio/${packId}/`)) {
    return fallback;
  }
  return path;
}

function normalizeProvenance(raw) {
  if (!raw || typeof raw !== 'object') {
    return null;
  }
  return {
    sourceUrl: typeof raw.sourceUrl === 'string' ? raw.sourceUrl.trim() : '',
    creator: typeof raw.creator === 'string' ? raw.creator.trim() : '',
    license: typeof raw.license === 'string' ? raw.license.trim() : '',
    sourceHash: typeof raw.sourceHash === 'string' ? raw.sourceHash.trim() : '',
    outputHash: typeof raw.outputHash === 'string' ? raw.outputHash.trim() : '',
    conversion: typeof raw.conversion === 'string' ? raw.conversion.trim() : '',
    attribution: typeof raw.attribution === 'string' ? raw.attribution.trim() : '',
    redistributionApproved: Boolean(raw.redistributionApproved),
  };
}

function inferFallbackPresetId(track) {
  const instrument = String(track?.instrument || '').toLowerCase();
  const program = Number(track?.midi_program);
  if (track?.is_drum || Number(track?.channel) === 10 || instrument.includes('drum')) {
    return 'drums_basic';
  }
  if (instrument.includes('bass') || (program >= 32 && program <= 39)) {
    return 'bass_synth';
  }
  if (/(string|pad|cello|violin)/.test(instrument) || (program >= 40 && program <= 55)) {
    return 'strings_pad';
  }
  if (/(guitar|pluck)/.test(instrument) || (program >= 24 && program <= 31)) {
    return 'guitar_pluck';
  }
  if (/(brass|trumpet|trombone)/.test(instrument) || (program >= 56 && program <= 63)) {
    return 'brass';
  }
  if (/(mallet|marimba|vibraphone)/.test(instrument) || (program >= 8 && program <= 15)) {
    return 'mallet';
  }
  if (/(lead|flute|sax|oboe|clarinet|synth)/.test(instrument)) {
    return 'woodwind_lead';
  }
  return 'piano_keyboard';
}
