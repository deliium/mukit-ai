import assert from 'node:assert/strict';
import test from 'node:test';

import * as context from './adaptiveMusicalContext.js';

test('locked mapping uses the flap band and exports no step', () => {
  const rule = context.LOCKED_CONTEXT_MAPPING.state_rules[0];
  assert.equal(rule.enter, 0.65);
  assert.equal(rule.exit, 0.35);
  assert.equal(rule.min_dwell_samples, 3);
  assert.equal(context.LOCKED_CONTEXT_MAPPING.baseline_state_id, 'state-exploration');
  assert.equal(rule.target_state_id, 'state-combat');
  assert.equal(context.STREAM_A.length, 20);
  assert.deepEqual(context.STREAM_B, [0.2, 0.7, 0.7, 0.7]);
  assert.equal(context.dangerSample(0.49).values.danger, 0.49);
  assert.equal(context.stepMusicalContext, undefined);
  assert.equal(context.step_musical_context, undefined);
});
