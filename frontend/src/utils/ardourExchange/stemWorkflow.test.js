import assert from 'node:assert/strict';
import test from 'node:test';

import { runArdourStemWorkflow, STEM_WORKFLOW_HONESTY } from './stemWorkflow.js';

const WORKING = {
  schema_version: 'composition.v2',
  title: 'Idea',
  tempo: 120,
  time_signature: '4/4',
  ticks_per_quarter: 480,
  key: 'C major',
  bar_count: 8,
  tracks: [{ id: 't1', name: 'Idea', role: 'melody', events: [] }],
  harmony: [],
};

test('runArdourStemWorkflow polls fake neural stem-set then prepares with stem_id', async () => {
  let polls = 0;
  const result = await runArdourStemWorkflow({
    composition: WORKING,
    pollMs: 1,
    timeoutMs: 1000,
    enqueueStemSet: async () => ({ id: 'ss_fake_01', status: 'queued', stems: [] }),
    getStemSet: async () => {
      polls += 1;
      if (polls < 2) {
        return {
          id: 'ss_fake_01',
          status: 'running',
          stems: [{ id: 'stem_a', status: 'running' }],
        };
      }
      return {
        id: 'ss_fake_01',
        status: 'complete',
        stems: [{ id: 'stem_a', status: 'complete' }],
      };
    },
    prepareExchange: async (body) => {
      assert.equal(body.stem_id, 'stem_a');
      assert.equal(body.use_preview_alignment, true);
      assert.equal(body.composition.schema_version, 'composition.v2');
      return { package_id: 'aex_prepared0000001', has_audio: true };
    },
    sleep: async () => {},
  });
  assert.equal(result.ok, true);
  assert.equal(result.stem_id, 'stem_a');
  assert.equal(result.prepare.package_id, 'aex_prepared0000001');
  assert.equal(result.honesty, STEM_WORKFLOW_HONESTY);
  assert.ok(polls >= 2);
});

test('runArdourStemWorkflow returns clear error when neural enqueue fails', async () => {
  const result = await runArdourStemWorkflow({
    composition: WORKING,
    enqueueStemSet: async () => {
      const err = new Error('Neural audio disabled');
      err.code = 'neural_audio_disabled';
      throw err;
    },
    prepareExchange: async () => {
      throw new Error('should not prepare');
    },
  });
  assert.equal(result.ok, false);
  assert.match(result.error, /disabled/i);
  assert.equal(result.honesty, STEM_WORKFLOW_HONESTY);
});

test('runArdourStemWorkflow times out when no stem completes', async () => {
  const result = await runArdourStemWorkflow({
    composition: WORKING,
    pollMs: 1,
    timeoutMs: 5,
    enqueueStemSet: async () => ({
      id: 'ss_slow',
      status: 'running',
      stems: [{ id: 'stem_b', status: 'running' }],
    }),
    getStemSet: async () => ({
      id: 'ss_slow',
      status: 'running',
      stems: [{ id: 'stem_b', status: 'running' }],
    }),
    prepareExchange: async () => {
      throw new Error('should not prepare');
    },
    sleep: async () => {},
  });
  assert.equal(result.ok, false);
  assert.match(result.error, /Timed out/i);
});
