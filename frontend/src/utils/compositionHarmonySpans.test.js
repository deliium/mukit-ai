import assert from 'node:assert/strict';
import test from 'node:test';

import {
  classifyHarmonyListShape,
  legacyHarmonyPointsToSpans,
  normalizeCompositionHarmonySpans,
  HARMONY_SHAPE_CANONICAL,
  HARMONY_SHAPE_LEGACY,
  HarmonySpanNormalizationError,
} from './compositionHarmonySpans.js';
import { migrateV1ToV2, prepareCompositionForStore } from './compositionVersion.js';

test('classifies legacy and canonical harmony shapes', () => {
  assert.equal(classifyHarmonyListShape([{ bar: 1, chord: 'C' }]), HARMONY_SHAPE_LEGACY);
  assert.equal(
    classifyHarmonyListShape([{ start_tick: 0, duration_ticks: 1920, chord: 'C' }]),
    HARMONY_SHAPE_CANONICAL,
  );
});

test('legacyHarmonyPointsToSpans collapses duplicates and preserves leading gap', () => {
  const { spans, stats } = legacyHarmonyPointsToSpans(
    [
      { bar: 1, chord: 'C' },
      { bar: 1, chord: 'Am' },
      { bar: 3, chord: 'G' },
    ],
    {
      boundaries: [0, 1920, 3840, 5760, 7680],
      durationTicks: 7680,
      barCount: 4,
    },
  );
  assert.deepEqual(spans, [
    { start_tick: 0, duration_ticks: 3840, chord: 'Am' },
    { start_tick: 3840, duration_ticks: 3840, chord: 'G' },
  ]);
  assert.equal(stats.duplicateCollapsedCount, 1);
});

test('migrateV1ToV2 converts legacy harmony points to spans', () => {
  const v2 = migrateV1ToV2({
    schema_version: 'composition.v1',
    tempo: 120,
    key: 'C major',
    time_signature: '4/4',
    ticks_per_quarter: 480,
    bar_count: 2,
    duration_ticks: 3840,
    sections: [{ type: 'intro', start_bar: 1, bar_count: 2, start_tick: 0, duration_ticks: 3840 }],
    tracks: [
      {
        id: 'piano-1',
        name: 'Piano',
        instrument: 'piano',
        role: 'harmony',
        midi_program: 0,
        channel: 1,
        events: [],
      },
    ],
    harmony: [
      { bar: 1, chord: 'C' },
      { bar: 2, chord: 'G' },
    ],
  });
  assert.deepEqual(v2.harmony, [
    { start_tick: 0, duration_ticks: 1920, chord: 'C' },
    { start_tick: 1920, duration_ticks: 1920, chord: 'G' },
  ]);
});

test('prepareCompositionForStore normalizes legacy V2 harmony and rejects mixed shapes', () => {
  const normalized = prepareCompositionForStore({
    schema_version: 'composition.v2',
    tempo: 100,
    key: 'C major',
    time_signature: '4/4',
    ticks_per_quarter: 480,
    bar_count: 2,
    duration_ticks: 3840,
    sections: [{ type: 'intro', start_bar: 1, bar_count: 2, start_tick: 0, duration_ticks: 3840 }],
    tracks: [
      {
        id: 'piano-1',
        name: 'Piano',
        instrument: 'piano',
        role: 'harmony',
        midi_program: 0,
        channel: 1,
        events: [],
      },
    ],
    harmony: [{ bar: 1, chord: 'Dm' }],
    tempo_changes: [],
    time_signature_changes: [],
    key_changes: [],
    markers: [],
  });
  assert.deepEqual(normalized.harmony, [
    { start_tick: 0, duration_ticks: 3840, chord: 'Dm' },
  ]);

  assert.throws(
    () => normalizeCompositionHarmonySpans({
      time_signature: '4/4',
      ticks_per_quarter: 480,
      bar_count: 2,
      duration_ticks: 3840,
      time_signature_changes: [],
      harmony: [
        { bar: 1, chord: 'C' },
        { start_tick: 1920, duration_ticks: 1920, chord: 'G' },
      ],
    }),
    HarmonySpanNormalizationError,
  );
});
