import assert from 'node:assert/strict';
import { test } from 'node:test';

import { cacheKeyForAsset, loadManifestAsset } from './assetCache.js';
import { FALLBACK_REASONS } from './constants.js';

async function sha256Hex(buffer) {
  const digest = await crypto.subtle.digest('SHA-256', buffer);
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('');
}

function makeMemoryCaches() {
  /** @type {Map<string, Map<string, { arrayBuffer: () => Promise<ArrayBuffer> }>>} */
  const stores = new Map();
  return {
    async open(name) {
      if (!stores.has(name)) {
        stores.set(name, new Map());
      }
      const store = stores.get(name);
      return {
        async match(key) {
          return store.has(key) ? store.get(key) : undefined;
        },
        async put(key, response) {
          const bytes = await response.arrayBuffer();
          store.set(key, {
            async arrayBuffer() {
              return bytes.slice(0);
            },
          });
        },
        async delete(key) {
          return store.delete(key);
        },
      };
    },
  };
}

test('cacheKeyForAsset includes digest', () => {
  assert.equal(
    cacheKeyForAsset('/browser-models/x.bin', 'AbC'),
    '/browser-models/x.bin::abc',
  );
});

test('asset_kind=none skips load', async () => {
  const result = await loadManifestAsset({
    asset_kind: 'none',
    asset_url: '',
    sha256: '',
    model_id: 'browser:symbolic-features-v1',
  }, { cachesImpl: null });
  assert.equal(result.skipped, true);
  assert.equal(result.cacheHit, false);
  assert.equal(result.bytes, null);
});

test('Cache API miss then hit after fetch', async () => {
  const payload = new TextEncoder().encode('browser-model-bytes');
  const digest = await sha256Hex(payload.buffer);
  const cachesImpl = makeMemoryCaches();
  let fetchCount = 0;
  const fetchImpl = async () => {
    fetchCount += 1;
    return {
      ok: true,
      async arrayBuffer() {
        return payload.buffer.slice(0);
      },
    };
  };
  const manifest = {
    asset_kind: 'raw_weights',
    asset_url: '/browser-models/demo.bin',
    sha256: digest,
    size_bytes: payload.byteLength,
  };

  const miss = await loadManifestAsset(manifest, { fetchImpl, cachesImpl });
  assert.equal(miss.cacheHit, false);
  assert.equal(miss.skipped, false);
  assert.equal(fetchCount, 1);
  assert.equal(miss.bytes.byteLength, payload.byteLength);

  const hit = await loadManifestAsset(manifest, { fetchImpl, cachesImpl });
  assert.equal(hit.cacheHit, true);
  assert.equal(fetchCount, 1);
  assert.equal(hit.bytes.byteLength, payload.byteLength);
});

test('integrity mismatch on fetch refuses asset', async () => {
  const payload = new TextEncoder().encode('good-bytes');
  const fetchImpl = async () => ({
    ok: true,
    async arrayBuffer() {
      return payload.buffer.slice(0);
    },
  });
  await assert.rejects(
    () => loadManifestAsset({
      asset_kind: 'onnx',
      asset_url: '/browser-models/bad.bin',
      sha256: '0'.repeat(64),
      size_bytes: payload.byteLength,
    }, { fetchImpl, cachesImpl: null }),
    (err) => err && err.code === FALLBACK_REASONS.ASSET_INTEGRITY,
  );
});
