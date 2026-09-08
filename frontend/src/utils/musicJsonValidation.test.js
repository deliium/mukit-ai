import assert from 'node:assert/strict';
import test from 'node:test';

import { migrateV1ToV2 } from './compositionVersion.js';
import { isCanonicalComposition, validateMusicJson } from './musicJsonValidation.js';

test('validates canonical v1 composition JSON', () => {
  const result = validateMusicJson(canonicalV1Composition());
  assert.equal(result.valid, true);
  assert.equal(isCanonicalComposition(canonicalV1Composition()), false);
});

test('validates canonical v2 composition JSON', () => {
  const v2 = migrateV1ToV2(canonicalV1Composition());
  const result = validateMusicJson(v2);
  assert.equal(result.valid, true);
  assert.equal(isCanonicalComposition(v2), true);
});

test('rejects invalid canonical velocity', () => {
  const composition = migrateV1ToV2(canonicalV1Composition());
  composition.tracks[0].events[0].velocity = 0;

  const result = validateMusicJson(composition);

  assert.equal(result.valid, false);
  assert.match(result.message, /velocity/);
});

test('rejects overlapping sustain pedals on v2', () => {
  const composition = migrateV1ToV2(canonicalV1Composition());
  composition.tracks[0].sustain_pedals = [
    { start_tick: 0, duration_ticks: 960 },
    { start_tick: 480, duration_ticks: 480 },
  ];
  const result = validateMusicJson(composition);
  assert.equal(result.valid, false);
  assert.match(result.message, /non-overlapping/);
});

test('rejects unsupported schema versions', () => {
  const result = validateMusicJson({ schema_version: 'composition.v9', tempo: 100 });
  assert.equal(result.valid, false);
  assert.match(result.message, /Unsupported schema_version/);
});

test('rejects legacy harmony-only JSON', () => {
  const result = validateMusicJson({
    tempo: 100,
    key: 'C major',
    time_signature: '4/4',
    sections: [{ type: 'intro', bars: 1 }],
    tracks: [{ instrument: 'piano', role: 'harmony' }],
    harmony: [{ bar: 1, chord: 'C' }],
    notes: [],
  });

  assert.equal(result.valid, false);
  assert.match(result.message, /no note events/);
});

function canonicalV1Composition() {
  return {
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
}
