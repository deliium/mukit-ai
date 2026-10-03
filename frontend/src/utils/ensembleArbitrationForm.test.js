import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  buildEnsemblePreviewRequest,
  clampEnsembleModelIds,
  clampEnsembleTopN,
  normalizeEnsembleSelectionMode,
} from './ensembleArbitrationForm.js';

describe('ensembleArbitrationForm', () => {
  it('clamps model arity and rejects duplicates', () => {
    const ids = clampEnsembleModelIds(
      ['fake:symbolic-tiny', 'fake:symbolic-tiny', 'fake:symbolic-dense', 'x', 'y'],
      3,
    );
    assert.deepEqual(ids, ['fake:symbolic-tiny', 'fake:symbolic-dense', 'x']);
  });

  it('clamps top_n and selection mode', () => {
    assert.equal(clampEnsembleTopN(0, 3), 1);
    assert.equal(clampEnsembleTopN(9, 3), 3);
    assert.equal(normalizeEnsembleSelectionMode('top_n'), 'top_n');
    assert.equal(normalizeEnsembleSelectionMode('nope'), 'human');
  });

  it('builds preview request and refuses single model', () => {
    const refused = buildEnsemblePreviewRequest({
      modelIds: ['fake:symbolic-tiny'],
      prompt: { duration_bars: 8, key: 'C major', instruments: ['piano'] },
    });
    assert.equal(refused.ok, false);
    assert.equal(refused.code, 'ensemble_model_limit');

    const ok = buildEnsemblePreviewRequest({
      modelIds: ['fake:symbolic-tiny', 'fake:symbolic-sparse', 'fake:symbolic-dense'],
      selectionMode: 'human',
      prompt: {
        duration_bars: 8,
        key: 'C major',
        time_signature: '4/4',
        tempo_min: 100,
        tempo_max: 120,
        instruments: ['piano', 'bass'],
      },
    });
    assert.equal(ok.ok, true);
    assert.equal(ok.request.plan.schema_version, 'composition.plan.v1');
    assert.equal(ok.request.policy.model_ids.length, 3);
    assert.equal(ok.request.constraints.duration_bars, 8);
  });
});
