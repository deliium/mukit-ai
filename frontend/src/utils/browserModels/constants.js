/**
 * Locked BrowserModel / ship-1 parity constants.
 * Keep aligned with docs/browser-webgpu-inference.md and benchmarks.fixture.json.
 */

export const BROWSER_MODEL_SCHEMA_CAPABILITY = 'browser.capability.v1';
export const BROWSER_MODEL_SCHEMA_MANIFEST = 'browser.model.manifest.v1';

export const BROWSER_SYMBOLIC_FEATURES_MODEL_ID = 'browser:symbolic-features-v1';
export const SYMBOLIC_FEATURES_PROFILE_ID = 'symbolic.features.v1';
export const SYMBOLIC_FEATURES_ALGORITHM_VERSION = 'symbolic.features.v1.algo.1';
export const SYMBOLIC_FEATURES_DIMS = 81;
export const SYMBOLIC_FEATURES_PROJECTION_NONE = 'none';
export const SYMBOLIC_FEATURES_DISTANCE_METRIC = 'cosine';

export const BROWSER_MODEL_MAX_NOTE_COUNT_DEFAULT = 20000;
export const BROWSER_MODEL_ASSET_MAX_BYTES = 32 * 1024 * 1024;

/** Max abs component error vs backend golden (or use cosine gate). */
export const PARITY_MAX_ABS_COMPONENT_ERROR = 1e-5;
/** Cosine similarity floor vs backend golden. */
export const PARITY_MIN_COSINE_SIMILARITY = 1 - 1e-9;

export const FALLBACK_REASONS = Object.freeze({
  DISABLED: 'browser_models_disabled',
  WEBGPU_UNAVAILABLE: 'webgpu_unavailable',
  WEBGPU_INIT_FAILED: 'webgpu_init_failed',
  ASSET_INTEGRITY: 'asset_integrity',
  PARITY_MISMATCH: 'parity_mismatch',
  INPUT_TOO_LARGE: 'input_too_large',
  HOST_ERROR: 'host_error',
  MANIFEST_REFUSED: 'manifest_refused',
  TWIN_EMPTY_SCOPE: 'twin_empty_scope',
});

export const FORBIDDEN_ASSET_PREFIXES = Object.freeze([
  '/models/llm/',
  '/models/neural-audio/',
  '/models/audio-recovery/',
  'models/llm/',
  'models/neural-audio/',
  'models/audio-recovery/',
  'file://',
  'DATASET_ROOT',
  'PERSONAL_COMPOSER_ROOT',
]);

export const DEFAULT_MANIFEST_URL = '/browser-models/symbolic-features-v1.manifest.json';
