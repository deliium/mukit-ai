import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  LIVE_PREDICT_NO_ABORT,
  createLivePredictAbortController,
} from './livePerformanceApi.js';

describe('createLivePredictAbortController', () => {
  it('returns a controller when AbortController is available', () => {
    const result = createLivePredictAbortController();
    assert.equal(result.ok, true);
    assert.equal(result.code, null);
    assert.ok(result.controller);
    assert.equal(typeof result.controller.abort, 'function');
    assert.ok(result.controller.signal);
  });

  it('no-ops without throwing when AbortController is missing', () => {
    const original = globalThis.AbortController;
    try {
      // Simulate broken / non-browser env (eslint-safe path).
      delete globalThis.AbortController;
      assert.equal(typeof globalThis.AbortController, 'undefined');

      let threw = false;
      let result;
      try {
        result = createLivePredictAbortController();
      } catch {
        threw = true;
      }
      assert.equal(threw, false);
      assert.equal(result.ok, false);
      assert.equal(result.controller, null);
      assert.equal(result.code, LIVE_PREDICT_NO_ABORT);
    } finally {
      globalThis.AbortController = original;
    }
  });
});
