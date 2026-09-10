import assert from 'node:assert/strict';
import test from 'node:test';

import {
  compareCompositions,
  compareTrackEvents,
  musicalEventKey,
} from './compositionVersionComparison.js';

function minimalV2(overrides = {}) {
  return {
    schema_version: 'composition.v2',
    tempo: 100,
    key: 'C major',
    time_signature: '4/4',
    ticks_per_quarter: 480,
    bar_count: 2,
    duration_ticks: 3840,
    sections: [
      {
        id: 'section-1',
        type: 'intro',
        start_bar: 1,
        bar_count: 2,
        start_tick: 0,
        duration_ticks: 3840,
      },
    ],
    tracks: [
      {
        id: 'piano-1',
        name: 'Piano',
        instrument: 'piano',
        role: 'harmony',
        midi_program: 0,
        channel: 1,
        is_drum: false,
        events: [
          {
            type: 'note',
            id: 'n1',
            pitch: 'C4',
            start_tick: 0,
            duration_ticks: 480,
            velocity: 80,
            articulations: [],
            tie: null,
          },
        ],
        dynamic_marks: [],
        sustain_pedals: [],
        automation: [],
      },
    ],
    harmony: [],
    markers: [],
    motifs: [],
    tempo_changes: [],
    time_signature_changes: [],
    key_changes: [],
    ...overrides,
  };
}

test('identical documents and null/null are identical', () => {
  const a = minimalV2();
  const b = structuredClone(a);
  const same = compareCompositions(a, b);
  assert.equal(same.identical, true);
  assert.equal(same.events.changed, 0);
  assert.deepEqual(compareCompositions(null, null).identical, true);
});

test('inputs are not mutated', () => {
  const source = minimalV2();
  const target = minimalV2();
  target.tracks[0].events[0].pitch = 'D4';
  const sourceSnap = JSON.stringify(source);
  const targetSnap = JSON.stringify(target);
  compareCompositions(source, target);
  assert.equal(JSON.stringify(source), sourceSnap);
  assert.equal(JSON.stringify(target), targetSnap);
});

test('pitch/timing changes count as musical changed and affect bars', () => {
  const source = minimalV2();
  const target = minimalV2();
  target.tracks[0].events[0].pitch = 'E4';
  const summary = compareCompositions(source, target);
  assert.equal(summary.identical, false);
  assert.equal(summary.events.changed, 1);
  assert.equal(summary.events.added, 0);
  assert.equal(summary.events.removed, 0);
  assert.ok(summary.affected_ranges.length >= 1);
  assert.ok(summary.affected_track_ids.includes('piano-1'));
});

test('metadata-only tempo change is full-document scope', () => {
  const source = minimalV2();
  const target = minimalV2({ tempo: 120 });
  const summary = compareCompositions(source, target);
  assert.equal(summary.timeline.changed.tempo, true);
  assert.deepEqual(summary.affected_ranges, [{ start_bar: 1, end_bar: 2 }]);
  assert.equal(summary.events.changed, 0);
});

test('track add/remove/reorder/rename/reinstrument', () => {
  const source = minimalV2();
  source.tracks.push({
    id: 'bass-1',
    name: 'Bass',
    instrument: 'bass',
    role: 'bass',
    midi_program: 32,
    channel: 2,
    events: [],
    dynamic_marks: [],
    sustain_pedals: [],
    automation: [],
  });
  const removed = compareCompositions(source, minimalV2());
  assert.deepEqual(removed.tracks.removed, ['bass-1']);

  const added = compareCompositions(minimalV2(), source);
  assert.deepEqual(added.tracks.added, ['bass-1']);

  const reordered = minimalV2();
  reordered.tracks = [
    structuredClone(source.tracks[1]),
    structuredClone(source.tracks[0]),
  ];
  const reorderSummary = compareCompositions(source, reordered);
  assert.equal(reorderSummary.tracks.reordered, true);

  const renamed = minimalV2();
  renamed.tracks[0].name = 'Keys';
  assert.deepEqual(compareCompositions(minimalV2(), renamed).tracks.renamed, ['piano-1']);

  const reinstrumented = minimalV2();
  reinstrumented.tracks[0].instrument = 'epiano';
  reinstrumented.tracks[0].midi_program = 4;
  assert.deepEqual(
    compareCompositions(minimalV2(), reinstrumented).tracks.reinstrumented,
    ['piano-1'],
  );
});

test('harmony-only difference updates counts and full scope', () => {
  const source = minimalV2();
  const target = minimalV2({
    harmony: [{ id: 'h1', start_tick: 0, duration_ticks: 1920, symbol: 'C' }],
  });
  const summary = compareCompositions(source, target);
  assert.equal(summary.counts.harmony.source, 0);
  assert.equal(summary.counts.harmony.target, 1);
  assert.deepEqual(summary.affected_ranges, [{ start_bar: 1, end_bar: 2 }]);
});

test('expressive field counts are reported', () => {
  const source = minimalV2();
  const target = minimalV2();
  target.tracks[0].dynamic_marks = [{ tick: 0, value: 'mf' }];
  const summary = compareCompositions(source, target);
  assert.equal(summary.counts.expressive.source, 0);
  assert.equal(summary.counts.expressive.target, 1);
  assert.equal(summary.identical, false);
});

test('missing/duplicate IDs use musical multiset matching; ID-only is identity churn', () => {
  const left = [
    { type: 'note', id: 'a', pitch: 'C4', start_tick: 0, duration_ticks: 480, velocity: 80 },
  ];
  const right = [
    { type: 'note', id: 'b', pitch: 'C4', start_tick: 0, duration_ticks: 480, velocity: 80 },
  ];
  const churn = compareTrackEvents(left, right);
  assert.equal(churn.changed, 0);
  assert.equal(churn.added, 0);
  assert.equal(churn.removed, 0);
  assert.equal(churn.identityChurn, 1);

  const missing = compareTrackEvents(
    [{ type: 'note', pitch: 'C4', start_tick: 0, duration_ticks: 480, velocity: 80 }],
    [{ type: 'note', pitch: 'C4', start_tick: 0, duration_ticks: 480, velocity: 80 }],
  );
  assert.equal(missing.identityChurn, 0);
  assert.equal(missing.changed, 0);

  assert.equal(
    musicalEventKey({ pitch: 'C4', start_tick: 0, duration_ticks: 480, velocity: 80, id: 'x' }),
    musicalEventKey({ pitch: 'C4', start_tick: 0, duration_ticks: 480, velocity: 80, id: 'y' }),
  );
});

test('variable meter compositions compare without inventing harmony notes', () => {
  const source = minimalV2({
    time_signature: '3/4',
    bar_count: 2,
    duration_ticks: 2880,
    time_signature_changes: [],
    sections: [
      {
        id: 's1',
        type: 'intro',
        start_bar: 1,
        bar_count: 2,
        start_tick: 0,
        duration_ticks: 2880,
      },
    ],
  });
  const target = structuredClone(source);
  target.tracks[0].events[0].start_tick = 960;
  const summary = compareCompositions(source, target);
  assert.equal(summary.valid, true);
  assert.equal(summary.events.changed, 1);
  assert.equal(summary.counts.harmony.source, 0);
  assert.equal(summary.counts.harmony.target, 0);
});

test('null to composition is non-identical full scope', () => {
  const target = minimalV2();
  const summary = compareCompositions(null, target);
  assert.equal(summary.identical, false);
  assert.deepEqual(summary.affected_ranges, [{ start_bar: 1, end_bar: 2 }]);
  assert.ok(summary.tracks.added.includes('piano-1'));
});

test('invalid composition input returns valid=false', () => {
  const summary = compareCompositions('bad', minimalV2());
  assert.equal(summary.valid, false);
  assert.equal(summary.code, 'invalid_composition');
});
