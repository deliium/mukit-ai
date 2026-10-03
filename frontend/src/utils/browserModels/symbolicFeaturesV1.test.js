import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';

import {
  PARITY_MAX_ABS_COMPONENT_ERROR,
  PARITY_MIN_COSINE_SIMILARITY,
} from './constants.js';
import { embedScopeDigest } from './embedScopeDigest.js';
import {
  clearSymbolicFeaturesMemoForTests,
  cosineSimilarityCpu,
  embedCompositionScopeBrowser,
} from './symbolicFeaturesV1.js';
import { compositionSourceFingerprint } from '../compositionAnalysis.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const golden = JSON.parse(
  readFileSync(join(__dirname, 'fixtures/symbolicFeaturesV1.golden.json'), 'utf8'),
);
const fixturesDir = join(__dirname, '../../../../backend/tests/fixtures/embeddings');

function maxAbsError(a, b) {
  let max = 0;
  for (let i = 0; i < a.length; i += 1) {
    max = Math.max(max, Math.abs(a[i] - b[i]));
  }
  return max;
}

for (const [name, expected] of Object.entries(golden.fixtures)) {
  test(`golden parity: ${name}`, async () => {
    clearSymbolicFeaturesMemoForTests();
    const fixtureName = name.replace(/_section0$/, '');
    const composition = JSON.parse(
      readFileSync(join(fixturesDir, `${fixtureName}.json`), 'utf8'),
    );
    const card = await embedCompositionScopeBrowser(composition, expected.scope);
    const fingerprint = await compositionSourceFingerprint(composition);
    const digest = await embedScopeDigest(expected.scope);

    assert.equal(fingerprint, expected.source_fingerprint);
    assert.equal(digest, expected.scope_digest);
    assert.equal(card.source_fingerprint, expected.source_fingerprint);
    assert.equal(card.scope_digest, expected.scope_digest);
    assert.equal(card.dims, 81);
    assert.equal(card.profile_id, 'symbolic.features.v1');
    assert.equal(card.algorithm_version, 'symbolic.features.v1.algo.1');
    assert.equal(card.model_id, 'browser:symbolic-features-v1');
    assert.equal(card.projection_id, 'none');
    assert.equal(card.artist_label_used, false);

    const absErr = maxAbsError(card.vector, expected.vector);
    const cosine = cosineSimilarityCpu(card.vector, expected.vector);
    assert.ok(
      absErr <= PARITY_MAX_ABS_COMPONENT_ERROR || cosine >= PARITY_MIN_COSINE_SIMILARITY,
      `parity failed abs=${absErr} cosine=${cosine}`,
    );
  });
}
