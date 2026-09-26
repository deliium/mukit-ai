import assert from 'node:assert/strict';
import test from 'node:test';

import {
  briefFingerprint,
  canStart,
  enabledActions,
  instructionAllowed,
  musicalBoardLine,
  musicalProgress,
} from './autonomousControl.js';

const STAGE_IDS = [
  'plan',
  'harmony_plan',
  'motif_plan',
  'symbolic',
  'critique',
  'revision',
  'arrangement',
  'expression',
  'render',
];

function stages(overrides) {
  return STAGE_IDS.map((stageId) => ({
    stage_id: stageId,
    status: overrides[stageId] || 'pending',
  }));
}

test('board marks theme done and arrangement current while critique follows its stages', () => {
  const rows = musicalProgress(
    stages({
      plan: 'completed',
      harmony_plan: 'completed',
      motif_plan: 'completed',
      symbolic: 'completed',
      arrangement: 'running',
    }),
    'Theme A',
    { runStatus: 'running' },
  );
  const byId = Object.fromEntries(rows.map((row) => [row.step_id, row]));
  assert.equal(byId.harmony.status, 'done');
  assert.equal(byId.theme.status, 'done');
  assert.equal(byId.theme.label, 'Theme A');
  assert.equal(byId.arrangement.status, 'current');
  assert.equal(byId.critique.status, 'pending');
  assert.equal(musicalBoardLine(byId.arrangement), '→ Arrangement');
  const critiqueDone = musicalProgress(
    stages({
      plan: 'completed',
      harmony_plan: 'completed',
      motif_plan: 'completed',
      symbolic: 'completed',
      critique: 'completed',
      revision: 'skipped',
      arrangement: 'running',
    }),
    'Theme A',
  );
  assert.equal(critiqueDone.find((row) => row.step_id === 'critique').status, 'done');
});

test('brief fingerprint blocks start until the preview matches', () => {
  const brief = {
    duration_seconds: 150,
    motif_label: 'Theme A',
    opening_key: 'F# minor',
    instrumentation: ['piano'],
    narrative: [{ intent: 'resolve', text: 'quiet' }],
  };
  assert.equal(canStart(null, brief), false);
  assert.equal(canStart({ previewedFingerprint: 'other' }, brief), false);
  const matched = { previewedFingerprint: briefFingerprint(brief) };
  assert.equal(canStart(matched, brief), true);
  assert.notEqual(
    briefFingerprint(brief),
    briefFingerprint({ ...brief, motif_label: 'Theme B' }),
  );
});

test('example sentence is allowed and unsafe phrases are refused', () => {
  assert.equal(
    instructionAllowed('Keep the melody, but use a smaller string arrangement.'),
    true,
  );
  assert.equal(instructionAllowed('add drums'), false);
  assert.equal(instructionAllowed('write a new theme'), false);
});

test('arrangement checkpoint offers reject and instruction without restarting the plan', () => {
  const actions = enabledActions({
    status: 'awaiting_approval',
    checkpoint_id: 'arrangement',
    stages: stages({
      plan: 'completed',
      harmony_plan: 'completed',
      motif_plan: 'completed',
      symbolic: 'completed',
      critique: 'completed',
      revision: 'skipped',
      arrangement: 'completed',
    }),
  });
  assert.ok(actions.includes('reject'));
  assert.ok(actions.includes('instruction'));
  assert.equal(actions.includes('restart-plan'), false);
  assert.equal(actions.includes('replay-plan'), false);
});
