import assert from 'node:assert/strict';
import test from 'node:test';

import {
  MIX_PLAN_MASTER_TARGETS,
  MIX_PLAN_NOT_A_GUARANTEE,
  clampMixPlanAfter,
  editMixPlanAfter,
  formatMixPlanChange,
  isMixPlanStale,
  mixAssistPreviewRequest,
} from './mixPlanUi.js';

test('master targets include a non-guarantee label', () => {
  assert.equal(MIX_PLAN_NOT_A_GUARANTEE, 'Not a guarantee');
  assert.deepEqual(
    MIX_PLAN_MASTER_TARGETS.map((item) => item.id),
    ['dynamic', 'streaming', 'cinematic', 'demo'],
  );
  assert.match(MIX_PLAN_MASTER_TARGETS.find((item) => item.id === 'streaming').goal, /-14 LUFS/);
});

test('clamp edited after-values inside schema bounds', () => {
  assert.equal(clampMixPlanAfter('gain', -40), -24);
  assert.equal(clampMixPlanAfter('gain', 3.5), 3.5);
  assert.equal(clampMixPlanAfter('pan', 2), 1);
  assert.equal(clampMixPlanAfter('send', -1), 0);
});

test('format a before/after parameter row', () => {
  const text = formatMixPlanChange({
    type: 'gain',
    target: 'bass',
    unit: 'dB',
    before: 0,
    after: -4.5,
  });
  assert.match(text, /gain bass/);
  assert.match(text, /0 → -4.5 dB/);
});

test('soft-stale when the live fingerprint diverges', () => {
  const plan = { source_stem_set_fingerprint: 'abc' };
  assert.equal(isMixPlanStale(plan, { liveFingerprint: 'abc' }), false);
  assert.equal(isMixPlanStale(plan, { liveFingerprint: 'def' }), true);
  assert.equal(isMixPlanStale(plan, { stemSetFingerprint: 'zzz' }), true);
  assert.equal(isMixPlanStale(null, { liveFingerprint: 'def' }), false);
});

test('phrase chips omit observation codes and the section chip omits the phrase', () => {
  const phrase = mixAssistPreviewRequest({
    projectId: 'p',
    stemSetId: 's',
    masterTarget: 'dynamic',
    phrase: 'make bass less dominant',
    reportId: 'report-1',
  });
  assert.equal(phrase.phrase, 'make bass less dominant');
  assert.equal(phrase.report_id, 'report-1');
  assert.equal(phrase.observation_codes, undefined);

  const section = mixAssistPreviewRequest({
    projectId: 'p',
    stemSetId: 's',
    masterTarget: 'streaming',
    phrase: 'make bass less dominant',
    reportId: 'report-1',
    observationCodes: ['section_loudness_flat'],
  });
  assert.deepEqual(section.observation_codes, ['section_loudness_flat']);
  assert.equal(section.phrase, undefined);
  assert.equal(section.report_id, 'report-1');
});

test('editing after does not drop other ops', () => {
  const next = editMixPlanAfter(
    {
      ops: [
        { op_id: 'a', type: 'gain', after: -4.5 },
        { op_id: 'b', type: 'send', after: 0.2 },
      ],
      changes: [
        { op_id: 'a', after: -4.5 },
        { op_id: 'b', after: 0.2 },
      ],
    },
    'a',
    -30,
  );
  assert.equal(next.ops[0].after, -24);
  assert.equal(next.ops[1].after, 0.2);
  assert.equal(next.changes[0].after, -24);
});
