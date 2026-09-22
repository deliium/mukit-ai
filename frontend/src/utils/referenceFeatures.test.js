import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  normalizeDimensionMask,
  toggleDimensionMask,
  withDimensionMask,
  dimensionLabel,
  saveReferenceFeaturesSession,
  loadReferenceFeaturesSession,
} from './referenceFeatures.js';

describe('referenceFeatures dimension masks', () => {
  it('maps AC labels for density and texture', () => {
    assert.equal(dimensionLabel('density'), 'Rhythmic density');
    assert.equal(dimensionLabel('texture'), 'Orchestration texture');
  });

  it('normalizes density+texture mask', () => {
    const result = normalizeDimensionMask(['density', 'texture', 'density']);
    assert.equal(result.ok, true);
    assert.deepEqual(result.dimensions, ['density', 'texture']);
  });

  it('rejects empty mask for conditioning', () => {
    const result = normalizeDimensionMask([]);
    assert.equal(result.ok, false);
    assert.equal(result.code, 'reference_feature_mask_empty');
  });

  it('allows null for legacy whole-summary', () => {
    const result = normalizeDimensionMask(null);
    assert.equal(result.ok, true);
    assert.equal(result.dimensions, null);
  });

  it('toggles dimensions immutably', () => {
    const next = toggleDimensionMask(['density'], 'texture', true);
    assert.deepEqual(next, ['density', 'texture']);
    const off = toggleDimensionMask(next, 'density', false);
    assert.deepEqual(off, ['texture']);
  });

  it('attaches mask onto style_reference', () => {
    const wrapped = withDimensionMask(
      { project_id: 'p1', scope: { kind: 'composition' }, mode: 'prompt_features' },
      ['density', 'texture'],
    );
    assert.equal(wrapped.ok, true);
    assert.deepEqual(wrapped.styleReference.dimensions, ['density', 'texture']);
  });

  it('round-trips session mask via storage stub', () => {
    const mem = new Map();
    const storage = {
      getItem: (key) => (mem.has(key) ? mem.get(key) : null),
      setItem: (key, value) => {
        mem.set(key, String(value));
      },
    };
    saveReferenceFeaturesSession(
      { enabled: true, dimensions: ['density', 'texture'] },
      storage,
    );
    const loaded = loadReferenceFeaturesSession(storage);
    assert.equal(loaded.enabled, true);
    assert.deepEqual(loaded.dimensions, ['density', 'texture']);
  });
});
