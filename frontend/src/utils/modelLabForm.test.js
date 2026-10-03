import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  buildCreatePayload,
  clampLabBatch,
  clampLabSteps,
  createEligible,
  registerEligible,
} from './modelLabForm.js';

describe('modelLabForm', () => {
  it('clamps steps and batch to Lab ceilings', () => {
    assert.equal(clampLabSteps(999, 64), 64);
    assert.equal(clampLabSteps(0, 64), 1);
    assert.equal(clampLabBatch(100, 8), 8);
  });

  it('createEligible requires enabled status and valid name', () => {
    const form = {
      displayName: 'TinyLab-v1',
      datasetVersionId: 'lab_fixture_tiny_v1',
      tokenizerPreset: 'core',
      architecturePreset: 'tiny_lab',
      steps: 8,
      batchSize: 2,
    };
    assert.equal(createEligible(form, { enabled: false, max_steps: 64, max_batch: 8 }), false);
    assert.equal(createEligible(form, { enabled: true, max_steps: 64, max_batch: 8 }), true);
    assert.equal(createEligible({ ...form, displayName: '' }, { enabled: true }), false);
  });

  it('registerEligible only when complete with a checkpoint step', () => {
    assert.equal(registerEligible({ status: 'complete' }, 4), true);
    assert.equal(registerEligible({ status: 'running' }, 4), false);
    assert.equal(registerEligible({ status: 'complete', registry_model_id: 'lab:x' }, 4), false);
  });

  it('buildCreatePayload freezes eval/listening off by default', () => {
    const payload = buildCreatePayload(
      {
        displayName: 'TinyLab-v1',
        datasetVersionId: 'v1',
        tokenizerPreset: 'core',
        architecturePreset: 'tiny_lab',
        seed: 42,
        steps: 8,
        batchSize: 2,
      },
      { max_steps: 64, max_batch: 8 },
    );
    assert.equal(payload.eval_enabled, false);
    assert.equal(payload.listening_enabled, false);
    assert.equal(payload.seed, 42);
  });
});
