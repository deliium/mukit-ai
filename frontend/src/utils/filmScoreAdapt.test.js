import assert from 'node:assert/strict';
import test from 'node:test';

import { useMusicStore } from '../store/musicStore.js';
import { autosaveBodyFromState } from './filmScorePlan.js';
import {
  baselineFromPicture,
  canCommitFilmAdapt,
  countLine,
  hitLine,
  operationLine,
} from './filmScoreAdapt.js';

const VECTOR_A = {
  op_id: 'edit_0000000a',
  strategy: 'phrase_contract',
  start_bar: 9,
  end_bar: 11,
  bars_delta: -3,
};

const VECTOR_C = {
  op_id: 'edit_0000000c',
  strategy: 'local_tempo',
  start_bar: 9,
  end_bar: 16,
  bars_delta: 0,
  tempo_bpm: 128,
};

test('vector A summary names the contracted bars', () => {
  const line = operationLine(VECTOR_A);
  assert.match(line, /phrase_contract/);
  assert.match(line, /bars 9-11/);
  assert.match(line, /delta -3/);
  assert.equal(
    countLine({
      events_unchanged: 8,
      events_shifted: 21,
      events_removed: 3,
      events_added: 0,
    }),
    'unchanged 8 shifted 21 removed 3 added 0',
  );
});

test('vector C summary names the local tempo repair', () => {
  const line = operationLine(VECTOR_C);
  assert.match(line, /local_tempo/);
  assert.match(line, /delta 0/);
  assert.equal(hitLine({ cue_id: 'hit_bbbb0002', change: 'moved', status: 'aligned' }), 'hit_bbbb0002 moved aligned');
  assert.equal(VECTOR_C.tempo_bpm, 128);
});

test('commit stays disabled until a candidate fingerprint exists', () => {
  assert.equal(canCommitFilmAdapt(null), false);
  assert.equal(canCommitFilmAdapt({ candidate_fingerprint: null }), false);
  assert.equal(canCommitFilmAdapt({ candidate_fingerprint: 'fp-1234567890abcd' }), true);
});

test('remember drops label and instruction', () => {
  const baseline = baselineFromPicture(
    { duration_seconds: 64 },
    {
      frame_rate_numerator: 24,
      frame_rate_denominator: 1,
      video_origin_seconds: 0,
      musical_origin_tick: 0,
      hit_points: [
        {
          id: 'hit_aaaa0001',
          label: 'Open',
          instruction: 'hit hard',
          kind: 'hit_point',
          importance: 'critical',
          video_seconds: 6,
          tolerance_frames: 0,
        },
      ],
    },
  );
  assert.equal(baseline.cues[0].label, undefined);
  assert.equal(baseline.cues[0].instruction, undefined);
  assert.equal(baseline.duration_seconds, 64);
  assert.equal(baselineFromPicture(null, { hit_points: [] }), null);
});

test('adaptation session slices stay off the composition autosave body', () => {
  const composition = { schema_version: 'composition.v2', bar_count: 32, tracks: [] };
  useMusicStore.setState({
    editedMusicJson: composition,
    filmAdaptPreview: { proposal: { operations: [{ strategy: 'phrase_contract' }] } },
    filmAdaptBaseline: { duration_seconds: 64, cues: [{ id: 'hit_aaaa0001' }] },
  });
  const body = autosaveBodyFromState(useMusicStore.getState());
  assert.equal(body.composition, composition);
  assert.equal(Object.hasOwn(body, 'filmAdaptPreview'), false);
  assert.equal(Object.hasOwn(body, 'filmAdaptBaseline'), false);
  assert.equal(JSON.stringify(body).includes('phrase_contract'), false);
  useMusicStore.setState({
    filmAdaptPreview: null,
    filmAdaptBaseline: null,
    editedMusicJson: null,
  });
});
