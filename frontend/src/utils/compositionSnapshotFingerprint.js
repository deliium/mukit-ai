/**
 * composition.snapshot.v1 fingerprint (FE mirror of BE encoding).
 *
 * Must NOT use composition.edit.v1 / fingerprintCompositionOrNull.
 * Algorithm: sha256("composition.snapshot.v1" + NUL + canonical JSON UTF-8).
 */

import { createAppLogger } from './appLogger.js';

const log = createAppLogger('compositionSnapshot');

export const SNAPSHOT_ENCODING_PROFILE = 'composition.snapshot.v1';
export const NULL_SNAPSHOT_FINGERPRINT = `${SNAPSHOT_ENCODING_PROFILE}:null`;

/**
 * Recursively sort object keys; array order preserved (matches BE).
 * @param {unknown} value
 * @returns {unknown}
 */
export function sortKeysDeep(value) {
  if (Array.isArray(value)) {
    return value.map(sortKeysDeep);
  }
  if (value && typeof value === 'object' && !(value instanceof Date)) {
    const out = {};
    for (const key of Object.keys(value).sort()) {
      out[key] = sortKeysDeep(value[key]);
    }
    return out;
  }
  return value;
}

/**
 * @param {unknown} value
 * @returns {string}
 */
export function canonicalSnapshotJsonDumps(value) {
  return JSON.stringify(sortKeysDeep(value));
}

async function sha256Hex(bytes) {
  if (typeof crypto !== 'undefined' && crypto.subtle) {
    const digest = await crypto.subtle.digest('SHA-256', bytes);
    return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('');
  }
  // Node test / non-subtle fallback (Uint8Array — no Buffer global in browser ESLint)
  const { createHash } = await import('node:crypto');
  return createHash('sha256').update(bytes).digest('hex');
}

/**
 * @param {object|null|undefined} composition composition.v2-like or null
 * @returns {Promise<string>}
 */
export async function compositionSnapshotFingerprint(composition) {
  if (composition == null) {
    log.debug('snapshot fingerprint null');
    return NULL_SNAPSHOT_FINGERPRINT;
  }
  const canonical = canonicalSnapshotJsonDumps(composition);
  const enc = new TextEncoder();
  const profile = enc.encode(SNAPSHOT_ENCODING_PROFILE);
  const nul = new Uint8Array([0]);
  const body = enc.encode(canonical);
  const joined = new Uint8Array(profile.length + 1 + body.length);
  joined.set(profile, 0);
  joined.set(nul, profile.length);
  joined.set(body, profile.length + 1);
  const hex = await sha256Hex(joined);
  log.debug('snapshot fingerprint', { fp_prefix: hex.slice(0, 12) });
  return hex;
}

/**
 * Resolve the live fingerprint for stale-render checks.
 * Prefer workingFingerprint after autosave flush when saveStatus is saved.
 *
 * @param {{ composition: object|null, workingFingerprint?: string|null, saveStatus?: string }} args
 * @returns {Promise<string|null>}
 */
export async function resolveLiveSnapshotFingerprint({
  composition,
  workingFingerprint = null,
  saveStatus = null,
} = {}) {
  if (saveStatus === 'saved' && workingFingerprint) {
    return workingFingerprint;
  }
  if (!composition) {
    return workingFingerprint || NULL_SNAPSHOT_FINGERPRINT;
  }
  try {
    return await compositionSnapshotFingerprint(composition);
  } catch (error) {
    log.warn('live snapshot fingerprint failed; falling back', {
      code: error?.name || 'fp_error',
    });
    return workingFingerprint || null;
  }
}

/**
 * @param {string|null|undefined} jobFingerprint
 * @param {string|null|undefined} liveFingerprint
 * @returns {boolean}
 */
export function isNeuralRenderStale(jobFingerprint, liveFingerprint) {
  if (!jobFingerprint || !liveFingerprint) {
    return false;
  }
  return String(jobFingerprint) !== String(liveFingerprint);
}
