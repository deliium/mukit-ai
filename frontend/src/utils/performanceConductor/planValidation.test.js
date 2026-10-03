import assert from 'node:assert/strict';
import test from 'node:test';

import { validatePerformancePlanBody } from './planValidation.js';

test('validatePerformancePlanBody accepts catalog-shaped plan', () => {
  const result = validatePerformancePlanBody({
    schema_version: 'performance.plan.v1',
    name: 'Intimate',
    preset_id: 'intimate',
    source_composition_fingerprint: 'a'.repeat(32),
    dimensions: {
      tempo_rubato: { depth: 0.2, rate: 0.5, phrase_anchor: 'bar' },
      dynamics: {
        curve_strength: 0.2,
        contrast: 0.2,
        velocity_floor: 40,
        velocity_ceiling: 100,
      },
      phrasing: { breath_gap_ticks: 0, phrase_arc: 0 },
      articulation: { legato_bias: 0, staccato_bias: 0 },
      pedaling: { style: 'literal', depth: 0 },
      microtiming: { swing: 0, humanize: 0 },
      accent: { downbeat: 0, offbeat: 0 },
      orchestral_balance: { role_gains: { harmony: 0.9 }, track_gains: {} },
    },
  });
  assert.equal(result.ok, true);
});

test('validatePerformancePlanBody refuses harmony arrays', () => {
  const result = validatePerformancePlanBody({
    schema_version: 'performance.plan.v1',
    name: 'Bad',
    preset_id: 'custom',
    source_composition_fingerprint: 'a'.repeat(32),
    dimensions: {},
    harmony: [{ bar: 1, chord: 'C' }],
  });
  assert.equal(result.ok, false);
  assert.equal(result.code, 'plan_embeds_harmony');
});
