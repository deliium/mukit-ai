import assert from 'node:assert/strict';
import test from 'node:test';

import { buildCanonicalPlaybackEvents } from './playbackEvents.js';

test('builds playback events from canonical ticks preserving polyphony and velocity', () => {
  const events = buildCanonicalPlaybackEvents({
    schema_version: 'composition.v1',
    tempo: 120,
    ticks_per_quarter: 480,
    tracks: [
      {
        id: 'piano-1',
        instrument: 'piano',
        events: [
          { pitch: 'C4', start_tick: 0, duration_ticks: 480, velocity: 64 },
          { pitch: 'E4', start_tick: 0, duration_ticks: 480, velocity: 127 },
          { pitch: 'G4', start_tick: 480, duration_ticks: 240, velocity: 80 },
        ],
      },
    ],
  });

  assert.equal(events.length, 3);
  assert.equal(events[0].position, 0);
  assert.equal(events[1].position, 0);
  assert.equal(events[0].duration, 0.5);
  assert.equal(events[2].position, 0.5);
  assert.equal(events[1].velocity, 1);
});

test('skips invalid canonical playback events', () => {
  const events = buildCanonicalPlaybackEvents({
    schema_version: 'composition.v1',
    tempo: 120,
    ticks_per_quarter: 480,
    tracks: [{ id: 'piano-1', instrument: 'piano', events: [{ pitch: '', start_tick: 0, duration_ticks: 480, velocity: 64 }] }],
  });

  assert.deepEqual(events, []);
});
