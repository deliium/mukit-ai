import assert from 'node:assert/strict';
import test from 'node:test';

import {
  classifyCompositionVersion,
  migrateV1ToV2,
  prepareCompositionForStore,
  CompositionVersionError,
  SCHEMA_VERSION_V2,
} from './compositionVersion.js';

test('classifies v1, v2, legacy, and unsupported versions', () => {
  assert.equal(classifyCompositionVersion({ schema_version: 'composition.v1', tracks: [] }), 'v1');
  assert.equal(classifyCompositionVersion({ schema_version: 'composition.v2', tracks: [] }), 'v2');
  assert.equal(classifyCompositionVersion({ schema_version: 'composition.v3' }), 'unsupported');
  assert.equal(classifyCompositionVersion({ notes: [], harmony: [] }), 'legacy');
});

test('migrateV1ToV2 preserves notes and adds V2 defaults', () => {
  const v1 = {
    schema_version: 'composition.v1',
    tempo: 120,
    key: 'C major',
    time_signature: '4/4',
    ticks_per_quarter: 480,
    bar_count: 1,
    duration_ticks: 1920,
    sections: [{ type: 'intro', start_bar: 1, bar_count: 1, start_tick: 0, duration_ticks: 1920 }],
    tracks: [
      {
        id: 'piano-1',
        name: 'Piano',
        instrument: 'piano',
        role: 'harmony',
        midi_program: 0,
        channel: 1,
        events: [{ type: 'note', pitch: 'C4', start_tick: 0, duration_ticks: 480, velocity: 90 }],
      },
    ],
    harmony: [],
  };

  const v2 = migrateV1ToV2(v1);
  assert.equal(v2.schema_version, SCHEMA_VERSION_V2);
  assert.deepEqual(v2.tempo_changes, []);
  assert.deepEqual(v2.markers, []);
  assert.equal(v2.tracks[0].expression, 127);
  assert.deepEqual(v2.tracks[0].dynamic_marks, []);
  assert.deepEqual(v2.tracks[0].events[0].articulations, []);
  assert.equal(v2.tracks[0].events[0].tie, null);
  assert.equal(v2.tracks[0].events[0].pitch, 'C4');
  assert.equal(v1.schema_version, 'composition.v1');
});

test('prepareCompositionForStore rejects unsupported and legacy payloads', () => {
  assert.throws(
    () => prepareCompositionForStore({ schema_version: 'composition.v9' }),
    CompositionVersionError,
  );
  assert.throws(
    () => prepareCompositionForStore({ tempo: 100, notes: [], harmony: [{ bar: 1, chord: 'C' }] }),
    /Legacy JSON requires backend normalization/,
  );
});
