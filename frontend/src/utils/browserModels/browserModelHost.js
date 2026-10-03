/**
 * BrowserModelHost — resolve / execute / fallback for ship-1 embed.
 * Never writes composition events. Never logs full vectors.
 */

import { createAppLogger } from '../appLogger.js';
import { loadManifestAsset } from './assetCache.js';
import { probeBrowserCapability } from './capabilityProbe.js';
import {
  BROWSER_MODEL_MAX_NOTE_COUNT_DEFAULT,
  BROWSER_SYMBOLIC_FEATURES_MODEL_ID,
  DEFAULT_MANIFEST_URL,
  FALLBACK_REASONS,
} from './constants.js';
import { parseBrowserModelManifest } from './manifest.js';
import {
  countCompositionNotes,
  embedCompositionScopeBrowser,
} from './symbolicFeaturesV1.js';

const log = createAppLogger('browserModels');

let firstLocalSuccessLogged = false;
/** @type {object | null} */
let cachedManifest = null;
/** @type {object | null} */
let cachedCapability = null;

function readViteFlag(name, defaultTrue = true) {
  try {
    const viteEnv = typeof import.meta !== 'undefined' ? import.meta.env : undefined;
    if (viteEnv && viteEnv[name] != null && String(viteEnv[name]).length > 0) {
      const text = String(viteEnv[name]).trim().toLowerCase();
      if (['0', 'false', 'no', 'off'].includes(text)) return false;
      if (['1', 'true', 'yes', 'on'].includes(text)) return true;
    }
  } catch {
    // ignore
  }
  if (typeof process !== 'undefined' && process.env && process.env[name] != null) {
    const text = String(process.env[name]).trim().toLowerCase();
    if (['0', 'false', 'no', 'off'].includes(text)) return false;
    if (['1', 'true', 'yes', 'on'].includes(text)) return true;
  }
  return defaultTrue;
}

export function isBrowserModelsEnabled() {
  return readViteFlag('VITE_BROWSER_MODELS_ENABLED', true);
}

export function isBrowserWebgpuEnabled() {
  return readViteFlag('VITE_BROWSER_MODELS_WEBGPU', true);
}

/**
 * @param {{ fetchImpl?: typeof fetch, capabilityEnv?: object, forceReload?: boolean }} [opts]
 */
export async function loadShip1Manifest(opts = {}) {
  if (cachedManifest && !opts.forceReload) {
    return cachedManifest;
  }
  const fetchImpl = opts.fetchImpl || (typeof fetch === 'function' ? fetch.bind(globalThis) : null);
  if (!fetchImpl) {
    throw Object.assign(new Error('fetch unavailable'), { code: FALLBACK_REASONS.HOST_ERROR });
  }
  const response = await fetchImpl(DEFAULT_MANIFEST_URL);
  if (!response.ok) {
    throw Object.assign(new Error(`manifest HTTP ${response.status}`), {
      code: FALLBACK_REASONS.HOST_ERROR,
    });
  }
  const raw = await response.json();
  const parsed = parseBrowserModelManifest(raw);
  if (!parsed.ok) {
    throw Object.assign(new Error(parsed.message), { code: parsed.code });
  }
  await loadManifestAsset(parsed.manifest, { fetchImpl: opts.fetchImpl });
  cachedManifest = parsed.manifest;
  return cachedManifest;
}

/**
 * @param {object} [capabilityEnv]
 */
export async function getCapabilitySnapshot(capabilityEnv) {
  if (cachedCapability && !capabilityEnv) {
    return cachedCapability;
  }
  cachedCapability = await probeBrowserCapability(capabilityEnv || {});
  return cachedCapability;
}

/**
 * Execute ship-1 embed locally or signal HTTP fallback.
 *
 * @param {object} composition
 * @param {object} scope
 * @param {{
 *   fetchImpl?: typeof fetch,
 *   capabilityEnv?: object,
 *   httpEmbed?: Function,
 *   modelId?: string | null,
 * }} [opts]
 * @returns {Promise<{
 *   embedding: object,
 *   execution_runtime: 'browser_model' | 'http',
 *   execution_device: 'webgpu' | 'browser_cpu' | 'server_cpu',
 *   fallback_reason?: string,
 * }>}
 */
export async function resolveEmbed(composition, scope, opts = {}) {
  const httpEmbed = opts.httpEmbed;
  if (!isBrowserModelsEnabled()) {
    log.info('Browser models disabled; HTTP embed', {
      reason: FALLBACK_REASONS.DISABLED,
    });
    if (typeof httpEmbed !== 'function') {
      throw Object.assign(new Error('HTTP embed required'), { code: FALLBACK_REASONS.DISABLED });
    }
    const embedding = await httpEmbed(composition, scope, opts.modelId);
    return {
      embedding,
      execution_runtime: 'http',
      execution_device: 'server_cpu',
      fallback_reason: FALLBACK_REASONS.DISABLED,
    };
  }

  const noteCount = countCompositionNotes(composition);
  const maxNotes = BROWSER_MODEL_MAX_NOTE_COUNT_DEFAULT;
  if (noteCount > maxNotes) {
    log.warn('Input too large for browser embed; HTTP fallback', {
      reason: FALLBACK_REASONS.INPUT_TOO_LARGE,
      note_count: noteCount,
      max_note_count: maxNotes,
    });
    if (typeof httpEmbed !== 'function') {
      throw Object.assign(new Error('input too large'), { code: FALLBACK_REASONS.INPUT_TOO_LARGE });
    }
    const embedding = await httpEmbed(composition, scope, opts.modelId);
    return {
      embedding,
      execution_runtime: 'http',
      execution_device: 'server_cpu',
      fallback_reason: FALLBACK_REASONS.INPUT_TOO_LARGE,
    };
  }

  try {
    await loadShip1Manifest({ fetchImpl: opts.fetchImpl });
    const capability = await getCapabilitySnapshot(opts.capabilityEnv);
    log.debug('BrowserModelHost resolve embed', {
      model_id: BROWSER_SYMBOLIC_FEATURES_MODEL_ID,
      note_count: noteCount,
      webgpu_ready: capability.webgpu_ready,
      webgpu_flag: isBrowserWebgpuEnabled(),
    });

    const embedding = await embedCompositionScopeBrowser(composition, scope);

    // WebGPU is optional for ship-1 card path (extract stays CPU).
    // Batched cosine is available via `scoreBatchWithWebgpu` for in-request batches.
    let executionDevice = 'browser_cpu';
    let fallbackReason;
    if (isBrowserWebgpuEnabled() && !capability.webgpu_ready) {
      fallbackReason = FALLBACK_REASONS.WEBGPU_UNAVAILABLE;
    }

    if (!firstLocalSuccessLogged) {
      firstLocalSuccessLogged = true;
      log.info('First browser-local embed success', {
        model_id: BROWSER_SYMBOLIC_FEATURES_MODEL_ID,
        execution_device: executionDevice,
        dims: embedding.dims,
        note_count: embedding.note_count,
      });
    }

    return {
      embedding,
      execution_runtime: 'browser_model',
      execution_device: executionDevice,
      ...(fallbackReason ? { fallback_reason: fallbackReason } : {}),
      capability,
    };
  } catch (err) {
    const reason = err?.code || FALLBACK_REASONS.HOST_ERROR;
    log.warn('Browser embed failed; HTTP fallback', {
      reason,
      error_type: err?.name || 'Error',
    });
    if (typeof httpEmbed !== 'function') {
      throw err;
    }
    const embedding = await httpEmbed(composition, scope, opts.modelId);
    return {
      embedding,
      execution_runtime: 'http',
      execution_device: 'server_cpu',
      fallback_reason: reason,
    };
  }
}

/**
 * Optional in-request section batch scoring (WebGPU cosine when enabled).
 * @param {number[]} queryVector
 * @param {number[][]} matrix
 */
export async function scoreBatchWithWebgpu(queryVector, matrix) {
  if (!isBrowserWebgpuEnabled()) {
    const { batchedCosineCpu } = await import('./symbolicFeaturesV1.js');
    return {
      scores: batchedCosineCpu(queryVector, matrix),
      device: 'browser_cpu',
      fallback_reason: FALLBACK_REASONS.WEBGPU_UNAVAILABLE,
    };
  }
  const { batchedCosineSimilarity } = await import('./webgpu/batchedCosine.js');
  return batchedCosineSimilarity(queryVector, matrix);
}

/** Test helpers */
export function resetBrowserModelHostForTests() {
  firstLocalSuccessLogged = false;
  cachedManifest = null;
  cachedCapability = null;
}
