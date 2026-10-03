import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';

import {
  resetBrowserModelHostForTests,
  resolveEmbed,
} from './browserModelHost.js';
import { clearSymbolicFeaturesMemoForTests } from './symbolicFeaturesV1.js';
import { FALLBACK_REASONS } from './constants.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const fixturePath = join(
  __dirname,
  '../../../../backend/tests/fixtures/embeddings/similar_rhythm_a.json',
);
const manifestPath = join(
  __dirname,
  '../../../public/browser-models/symbolic-features-v1.manifest.json',
);

function makeFetch() {
  const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'));
  return async (url) => {
    if (String(url).includes('symbolic-features-v1.manifest.json')) {
      return {
        ok: true,
        async json() {
          return manifest;
        },
      };
    }
    return { ok: false, status: 404 };
  };
}

test('resolveEmbed succeeds on browser CPU without HTTP', async () => {
  resetBrowserModelHostForTests();
  clearSymbolicFeaturesMemoForTests();
  process.env.VITE_BROWSER_MODELS_ENABLED = 'true';
  process.env.VITE_BROWSER_MODELS_WEBGPU = 'false';
  let httpCalls = 0;
  const composition = JSON.parse(readFileSync(fixturePath, 'utf8'));
  const result = await resolveEmbed(composition, { kind: 'composition' }, {
    fetchImpl: makeFetch(),
    capabilityEnv: { isSecureContext: true, navigator: {} },
    httpEmbed: async () => {
      httpCalls += 1;
      return { vector: [1] };
    },
  });
  assert.equal(result.execution_runtime, 'browser_model');
  assert.equal(result.execution_device, 'browser_cpu');
  assert.equal(result.embedding.dims, 81);
  assert.equal(httpCalls, 0);
});

test('resolveEmbed falls back to HTTP when host throws', async () => {
  resetBrowserModelHostForTests();
  clearSymbolicFeaturesMemoForTests();
  process.env.VITE_BROWSER_MODELS_ENABLED = 'true';
  let httpCalls = 0;
  const composition = JSON.parse(readFileSync(fixturePath, 'utf8'));
  const result = await resolveEmbed(composition, { kind: 'composition' }, {
    fetchImpl: async () => ({ ok: false, status: 500 }),
    httpEmbed: async () => {
      httpCalls += 1;
      return {
        schema_version: 'composition.embedding.v1',
        dims: 81,
        vector: new Array(81).fill(0),
        source_fingerprint: 'a'.repeat(64),
        scope_digest: 'b'.repeat(32),
      };
    },
  });
  assert.equal(result.execution_runtime, 'http');
  assert.equal(httpCalls, 1);
  assert.ok(result.fallback_reason);
});

test('oversized note count forces HTTP', async () => {
  resetBrowserModelHostForTests();
  process.env.VITE_BROWSER_MODELS_ENABLED = 'true';
  const composition = JSON.parse(readFileSync(fixturePath, 'utf8'));
  // Inflate events beyond default max
  const events = [];
  for (let i = 0; i < 20001; i += 1) {
    events.push({
      type: 'note',
      pitch: 'C4',
      start_tick: i,
      duration_ticks: 1,
      velocity: 80,
      id: `e${i}`,
    });
  }
  composition.tracks[0].events = events;
  let httpCalls = 0;
  const result = await resolveEmbed(composition, { kind: 'composition' }, {
    fetchImpl: makeFetch(),
    httpEmbed: async () => {
      httpCalls += 1;
      return { dims: 81, vector: [] };
    },
  });
  assert.equal(result.execution_runtime, 'http');
  assert.equal(result.fallback_reason, FALLBACK_REASONS.INPUT_TOO_LARGE);
  assert.equal(httpCalls, 1);
});
