import assert from 'node:assert/strict';
import test from 'node:test';

import { validateMusicJson } from './musicJsonValidation.js';

test('validates canonical composition JSON', () => {
  const result = validateMusicJson(canonicalComposition());

  assert.equal(result.valid, true);
});

test('rejects invalid canonical velocity', () => {
  const composition = canonicalComposition();
  composition.tracks[0].events[0].velocity = 0;

  const result = validateMusicJson(composition);

  assert.equal(result.valid, false);
  assert.match(result.message, /velocity/);
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

function canonicalComposition() {
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
