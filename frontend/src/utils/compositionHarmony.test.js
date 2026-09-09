import assert from 'node:assert/strict';
import test from 'node:test';

import {
  applyHarmonyAdd,
  applyHarmonyReplace,
  applyHarmonyRemove,
  applyHarmonyMove,
  applyHarmonyResize,
  diffHarmonySpans,
  selectionTicksFromBars,
  verifyReharmonizationCandidate,
  HarmonyTimelineClientError,
} from './compositionHarmony.js';

const BAR = 1920;

function composition(overrides = {}) {
  return {
    schema_version: 'composition.v2',
    tempo: 100,
    key: 'C major',
    time_signature: '4/4',
    ticks_per_quarter: 480,
    bar_count: 16,
    duration_ticks: 16 * BAR,
    sections: [],
    tracks: [
      {
        id: 'melody',
        role: 'melody',
        events: [{ type: 'note', id: 'm1', pitch: 'C4', start_tick: 8 * BAR, duration_ticks: 480, velocity: 80 }],
      },
      {
        id: 'bass',
        role: 'bass',
        events: [{ type: 'note', id: 'b1', pitch: 'C2', start_tick: 8 * BAR, duration_ticks: BAR, velocity: 70 }],
      },
    ],
    harmony: [
      { start_tick: 0, duration_ticks: 8 * BAR, chord: 'C' },
      { start_tick: 8 * BAR, duration_ticks: 4 * BAR, chord: 'G' },
      { start_tick: 12 * BAR, duration_ticks: 4 * BAR, chord: 'C' },
    ],
    tempo_changes: [],
    time_signature_changes: [],
    key_changes: [],
    ...overrides,
  };
}

test('selects bars 9-12 tick range', () => {
  const range = selectionTicksFromBars(composition(), 9, 12);
  assert.deepEqual(range, { startTick: 8 * BAR, endTick: 12 * BAR });
});

test('adds into a gap and rejects overlap', () => {
  const base = composition({
    harmony: [{ start_tick: 0, duration_ticks: BAR, chord: 'C' }],
  });
  const added = applyHarmonyAdd(base, { start_tick: BAR, duration_ticks: BAR, chord: 'G' });
  assert.equal(added.harmony.length, 2);
  assert.throws(
    () => applyHarmonyAdd(added, { start_tick: 0, duration_ticks: BAR, chord: 'Am' }),
    HarmonyTimelineClientError,
  );
});

test('replace/remove/move/resize preserve spelling merge rules', () => {
  const base = composition({
    harmony: [
      { start_tick: 0, duration_ticks: BAR, chord: 'C' },
      { start_tick: 2 * BAR, duration_ticks: BAR, chord: 'G' },
    ],
  });
  const replaced = applyHarmonyReplace(base, {
    start_tick: BAR,
    duration_ticks: BAR,
    spans: [{ start_tick: BAR, duration_ticks: BAR, chord: 'C' }],
  });
  assert.deepEqual(replaced.harmony, [
    { start_tick: 0, duration_ticks: 2 * BAR, chord: 'C' },
    { start_tick: 2 * BAR, duration_ticks: BAR, chord: 'G' },
  ]);

  const removed = applyHarmonyRemove(replaced, { start_tick: BAR, duration_ticks: BAR });
  assert.deepEqual(removed.harmony[0], { start_tick: 0, duration_ticks: BAR, chord: 'C' });

  const moved = applyHarmonyMove(base, { source_start_tick: 2 * BAR, new_start_tick: 3 * BAR });
  assert.deepEqual(
    moved.harmony.map((s) => s.start_tick),
    [0, 3 * BAR],
  );

  const resized = applyHarmonyResize(base, {
    source_start_tick: 0,
    edge: 'end',
    new_tick: BAR / 2,
  });
  assert.equal(resized.harmony[0].duration_ticks, BAR / 2);
});

test('diffs and verifies bars 9-12 candidate preservation', () => {
  const base = composition();
  const candidate = {
    ...base,
    harmony: [
      { start_tick: 0, duration_ticks: 8 * BAR, chord: 'C' },
      { start_tick: 8 * BAR, duration_ticks: 4 * BAR, chord: 'D7(b9)' },
      { start_tick: 12 * BAR, duration_ticks: 4 * BAR, chord: 'C' },
    ],
    tracks: [
      base.tracks[0],
      {
        ...base.tracks[1],
        events: [{ type: 'note', id: 'b1', pitch: 'D2', start_tick: 8 * BAR, duration_ticks: BAR, velocity: 70 }],
      },
    ],
  };
  const diff = diffHarmonySpans(base.harmony, candidate.harmony);
  assert.equal(diff.added[0].chord, 'D7(b9)');
  const result = verifyReharmonizationCandidate({
    baseComposition: base,
    candidateComposition: candidate,
    startTick: 8 * BAR,
    endTick: 12 * BAR,
    authorizedTrackIds: ['bass'],
    preserveMelody: true,
    baseFingerprint: 'abc',
    responseBaseFingerprint: 'abc',
    proposalFingerprint: 'def',
    responseProposalFingerprint: 'def',
  });
  assert.equal(result.ok, true);
});
