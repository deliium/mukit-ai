/**
 * Cache API preferred + IndexedDB fallback for public browser model assets.
 * Keys include sha256 so integrity mismatches bust cache. Never stores vectors.
 */

import { createAppLogger } from '../appLogger.js';
import { BROWSER_MODEL_ASSET_MAX_BYTES, FALLBACK_REASONS } from './constants.js';

const log = createAppLogger('browserModels');
const CACHE_NAME = 'mukit-browser-models-v1';
const IDB_NAME = 'mukit-browser-models';
const IDB_STORE = 'assets';

/**
 * @param {string} url
 * @param {string} sha256
 */
export function cacheKeyForAsset(url, sha256) {
  return `${String(url)}::${String(sha256 || '').toLowerCase()}`;
}

async function sha256Hex(buffer) {
  const digest = await crypto.subtle.digest('SHA-256', buffer);
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('');
}

function openIdb() {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === 'undefined') {
      reject(new Error('indexedDB unavailable'));
      return;
    }
    const req = indexedDB.open(IDB_NAME, 1);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(IDB_STORE)) {
        db.createObjectStore(IDB_STORE);
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error || new Error('idb open failed'));
  });
}

async function idbGet(key) {
  const db = await openIdb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(IDB_STORE, 'readonly');
    const store = tx.objectStore(IDB_STORE);
    const req = store.get(key);
    req.onsuccess = () => resolve(req.result || null);
    req.onerror = () => reject(req.error || new Error('idb get failed'));
  });
}

async function idbPut(key, value) {
  const db = await openIdb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(IDB_STORE, 'readwrite');
    const store = tx.objectStore(IDB_STORE);
    const req = store.put(value, key);
    req.onsuccess = () => resolve();
    req.onerror = () => reject(req.error || new Error('idb put failed'));
  });
}

/**
 * Load asset bytes with digest verification.
 * @param {{ asset_url: string, sha256: string, size_bytes?: number, asset_kind: string }} manifest
 * @param {{ fetchImpl?: typeof fetch, cachesImpl?: CacheStorage | null }} [deps]
 * @returns {Promise<{ bytes: ArrayBuffer | null, cacheHit: boolean, skipped: boolean }>}
 */
export async function loadManifestAsset(manifest, deps = {}) {
  if (!manifest || manifest.asset_kind === 'none' || !manifest.asset_url) {
    log.debug('Asset load skipped (asset_kind=none)', {
      model_id: manifest?.model_id,
    });
    return { bytes: null, cacheHit: false, skipped: true };
  }

  const fetchImpl = deps.fetchImpl || (typeof fetch === 'function' ? fetch.bind(globalThis) : null);
  if (!fetchImpl) {
    throw Object.assign(new Error('fetch unavailable'), { code: FALLBACK_REASONS.HOST_ERROR });
  }

  const key = cacheKeyForAsset(manifest.asset_url, manifest.sha256);
  const expected = String(manifest.sha256 || '').toLowerCase();
  const cachesImpl = deps.cachesImpl !== undefined
    ? deps.cachesImpl
    : (typeof caches !== 'undefined' ? caches : null);

  if (cachesImpl && typeof cachesImpl.open === 'function') {
    try {
      const cache = await cachesImpl.open(CACHE_NAME);
      const hit = await cache.match(key);
      if (hit) {
        const buf = await hit.arrayBuffer();
        if (expected) {
          const actual = await sha256Hex(buf);
          if (actual !== expected) {
            log.warn('Cache integrity mismatch', {
              reason: FALLBACK_REASONS.ASSET_INTEGRITY,
              digest_prefix: expected.slice(0, 12),
            });
            await cache.delete(key);
          } else {
            log.debug('Asset cache hit (Cache API)', {
              digest_prefix: expected.slice(0, 12),
              size_bytes: buf.byteLength,
            });
            return { bytes: buf, cacheHit: true, skipped: false };
          }
        } else {
          log.debug('Asset cache hit (Cache API, no digest)', { size_bytes: buf.byteLength });
          return { bytes: buf, cacheHit: true, skipped: false };
        }
      }
    } catch (err) {
      log.debug('Cache API miss/error', { error_type: err?.name || 'Error' });
    }
  } else {
    try {
      const cached = await idbGet(key);
      if (cached instanceof ArrayBuffer) {
        if (expected) {
          const actual = await sha256Hex(cached);
          if (actual !== expected) {
            log.warn('IndexedDB integrity mismatch', {
              reason: FALLBACK_REASONS.ASSET_INTEGRITY,
              digest_prefix: expected.slice(0, 12),
            });
          } else {
            log.debug('Asset cache hit (IndexedDB)', {
              digest_prefix: expected.slice(0, 12),
              size_bytes: cached.byteLength,
            });
            return { bytes: cached, cacheHit: true, skipped: false };
          }
        } else {
          return { bytes: cached, cacheHit: true, skipped: false };
        }
      }
    } catch (err) {
      log.debug('IndexedDB miss/error', { error_type: err?.name || 'Error' });
    }
  }

  log.debug('Asset cache miss; fetching', {
    url_prefix: String(manifest.asset_url).slice(0, 48),
    digest_prefix: expected ? expected.slice(0, 12) : null,
  });
  const response = await fetchImpl(manifest.asset_url);
  if (!response.ok) {
    throw Object.assign(new Error(`asset fetch HTTP ${response.status}`), {
      code: FALLBACK_REASONS.HOST_ERROR,
    });
  }
  const bytes = await response.arrayBuffer();
  if (bytes.byteLength > BROWSER_MODEL_ASSET_MAX_BYTES) {
    throw Object.assign(new Error('asset exceeds size cap'), {
      code: FALLBACK_REASONS.MANIFEST_REFUSED,
    });
  }
  if (expected) {
    const actual = await sha256Hex(bytes);
    if (actual !== expected) {
      log.warn('Fetched asset integrity mismatch', {
        reason: FALLBACK_REASONS.ASSET_INTEGRITY,
        digest_prefix: expected.slice(0, 12),
      });
      throw Object.assign(new Error('asset digest mismatch'), {
        code: FALLBACK_REASONS.ASSET_INTEGRITY,
      });
    }
  }

  if (cachesImpl && typeof cachesImpl.open === 'function') {
    try {
      const cache = await cachesImpl.open(CACHE_NAME);
      await cache.put(key, new Response(bytes.slice(0)));
    } catch {
      // ignore cache write failures
    }
  } else {
    try {
      await idbPut(key, bytes);
    } catch {
      // ignore
    }
  }

  return { bytes, cacheHit: false, skipped: false };
}
