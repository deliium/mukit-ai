import assert from 'node:assert/strict';
import { test } from 'node:test';

import { batchedCosineSimilarity } from './batchedCosine.js';
import { batchedCosineCpu } from '../symbolicFeaturesV1.js';
import { FALLBACK_REASONS } from '../constants.js';

function l2(vec) {
  const n = Math.sqrt(vec.reduce((s, v) => s + v * v, 0)) || 1;
  return vec.map((v) => v / n);
}

test('WebGPU forced-off path uses CPU cosine via missing gpu', async () => {
  const query = l2([1, 0, 0, 0]);
  const matrix = [l2([1, 0, 0, 0]), l2([0, 1, 0, 0])];
  const result = await batchedCosineSimilarity(query, matrix, { gpu: null });
  assert.equal(result.device, 'browser_cpu');
  assert.equal(result.fallback_reason, FALLBACK_REASONS.WEBGPU_UNAVAILABLE);
  assert.deepEqual(result.scores, batchedCosineCpu(query, matrix));
  assert.ok(Math.abs(result.scores[0] - 1) < 1e-6);
  assert.ok(Math.abs(result.scores[1]) < 1e-6);
});

test('stubbed navigator.gpu path produces correct scores', async () => {
  const query = l2([1, 2, 3, 4]);
  const matrix = [l2([1, 2, 3, 4]), l2([4, 3, 2, 1])];
  const expected = batchedCosineCpu(query, matrix);

  // Minimal stub: fail device creation → CPU fallback with same scores.
  const gpu = {
    async requestAdapter() {
      return {
        async requestDevice() {
          throw new Error('stub device unavailable');
        },
      };
    },
  };
  const result = await batchedCosineSimilarity(query, matrix, { gpu });
  assert.equal(result.device, 'browser_cpu');
  assert.equal(result.fallback_reason, FALLBACK_REASONS.WEBGPU_INIT_FAILED);
  for (let i = 0; i < expected.length; i += 1) {
    assert.ok(Math.abs(result.scores[i] - expected[i]) < 1e-6);
  }
});
