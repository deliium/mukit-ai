/**
 * Validate and normalize `browser.model.manifest.v1`.
 * Refuses forbidden private-tree path prefixes.
 */

import { createAppLogger } from '../appLogger.js';
import {
  BROWSER_MODEL_ASSET_MAX_BYTES,
  BROWSER_MODEL_SCHEMA_MANIFEST,
  FALLBACK_REASONS,
  FORBIDDEN_ASSET_PREFIXES,
} from './constants.js';

const log = createAppLogger('browserModels');

const ASSET_KINDS = new Set(['none', 'onnx', 'raw_weights']);

/**
 * @param {string} url
 * @returns {boolean}
 */
export function isForbiddenAssetUrl(url) {
  if (typeof url !== 'string' || !url.trim()) {
    return false;
  }
  const text = url.trim();
  if (text.startsWith('/') && !text.startsWith('/browser-models/')) {
    // Absolute site paths outside allowlist
    if (FORBIDDEN_ASSET_PREFIXES.some((prefix) => text.includes(prefix.replace(/^\//, ''))
      || text.startsWith(prefix))) {
      return true;
    }
    return !text.startsWith('/browser-models/');
  }
  if (/^[a-zA-Z]:[\\/]/.test(text) || text.startsWith('/') && text.includes('models/llm')) {
    return true;
  }
  return FORBIDDEN_ASSET_PREFIXES.some((prefix) => text.includes(prefix));
}

/**
 * @param {unknown} raw
 * @returns {{ ok: true, manifest: object } | { ok: false, code: string, message: string }}
 */
export function parseBrowserModelManifest(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return { ok: false, code: FALLBACK_REASONS.MANIFEST_REFUSED, message: 'manifest not an object' };
  }
  const schema = raw.schema_version;
  if (schema !== BROWSER_MODEL_SCHEMA_MANIFEST) {
    return {
      ok: false,
      code: FALLBACK_REASONS.MANIFEST_REFUSED,
      message: `unexpected schema_version=${String(schema)}`,
    };
  }
  const modelId = typeof raw.model_id === 'string' ? raw.model_id.trim() : '';
  const profileId = typeof raw.profile_id === 'string' ? raw.profile_id.trim() : '';
  if (!modelId || !profileId) {
    return { ok: false, code: FALLBACK_REASONS.MANIFEST_REFUSED, message: 'model_id/profile_id required' };
  }
  const assetKind = typeof raw.asset_kind === 'string' ? raw.asset_kind.trim() : '';
  if (!ASSET_KINDS.has(assetKind)) {
    return { ok: false, code: FALLBACK_REASONS.MANIFEST_REFUSED, message: 'invalid asset_kind' };
  }
  const assetUrl = typeof raw.asset_url === 'string' ? raw.asset_url.trim() : '';
  if (assetKind !== 'none') {
    if (!assetUrl.startsWith('/browser-models/')) {
      log.warn('Manifest asset_url outside allowlist', {
        model_id: modelId,
        reason: FALLBACK_REASONS.MANIFEST_REFUSED,
      });
      return {
        ok: false,
        code: FALLBACK_REASONS.MANIFEST_REFUSED,
        message: 'asset_url must be under /browser-models/',
      };
    }
    if (isForbiddenAssetUrl(assetUrl)) {
      log.warn('Manifest refused forbidden asset path', {
        model_id: modelId,
        reason: FALLBACK_REASONS.MANIFEST_REFUSED,
      });
      return {
        ok: false,
        code: FALLBACK_REASONS.MANIFEST_REFUSED,
        message: 'forbidden asset path prefix',
      };
    }
  } else if (assetUrl && isForbiddenAssetUrl(assetUrl)) {
    return {
      ok: false,
      code: FALLBACK_REASONS.MANIFEST_REFUSED,
      message: 'forbidden asset path prefix',
    };
  }

  const sizeBytes = Number(raw.size_bytes) || 0;
  if (sizeBytes < 0 || sizeBytes > BROWSER_MODEL_ASSET_MAX_BYTES) {
    return {
      ok: false,
      code: FALLBACK_REASONS.MANIFEST_REFUSED,
      message: 'size_bytes out of cap',
    };
  }

  const ops = Array.isArray(raw.ops)
    ? raw.ops.filter((op) => typeof op === 'string').map((op) => op.trim())
    : [];

  const manifest = {
    schema_version: BROWSER_MODEL_SCHEMA_MANIFEST,
    model_id: modelId,
    profile_id: profileId,
    algorithm_version: typeof raw.algorithm_version === 'string' ? raw.algorithm_version : null,
    ops,
    asset_kind: assetKind,
    asset_url: assetUrl,
    sha256: typeof raw.sha256 === 'string' ? raw.sha256.trim().toLowerCase() : '',
    size_bytes: sizeBytes,
    input_spec: raw.input_spec && typeof raw.input_spec === 'object' ? raw.input_spec : {},
    output_spec: raw.output_spec && typeof raw.output_spec === 'object' ? raw.output_spec : {},
    webgpu_optional: Boolean(raw.webgpu_optional),
    webgpu_sub_path: typeof raw.webgpu_sub_path === 'string' ? raw.webgpu_sub_path : null,
  };
  log.debug('Parsed browser model manifest', {
    model_id: manifest.model_id,
    asset_kind: manifest.asset_kind,
    ops: manifest.ops,
    size_bytes: manifest.size_bytes,
    digest_prefix: manifest.sha256 ? manifest.sha256.slice(0, 12) : null,
  });
  return { ok: true, manifest };
}
