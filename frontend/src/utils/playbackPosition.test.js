import assert from 'node:assert/strict';
import test from 'node:test';

import { buildLegacyPlaybackEvents } from './legacyPlaybackEvents.js';
import { secondsToPlaybackPosition, compositionRevisionKey } from './playbackPosition.js';

test('legacy builder refuses composition.v1 payloads', () => {
  const events = buildLegacyPlaybackEvents({
    schema_version: 'composition.v1',
    tempo: 120,
    notes: [{ bar: 1, beat: 1, pitch: 'C4', duration: 1 }],
  });
  assert.deepEqual(events, []);
});

test('legacy builder can use top-level notes without inventing from empty harmony', () => {
  const events = buildLegacyPlaybackEvents({
    tempo: 120,
    time_signature: '4/4',
    notes: [
      { bar: 1, beat: 1, pitch: 'C4', duration: 1 },
      { bar: 1, beat: 2, pitch: 'E4', duration: 1 },
    ],
    harmony: [{ bar: 1, chord: 'C' }],
  });
  assert.equal(events.length, 2);
  assert.equal(events[0].notes[0], 'C4');
  assert.equal(events[0].position, 0);
  assert.equal(events[1].position, 0.5);
});

test('computes bar position from transport seconds', () => {
  const position = secondsToPlaybackPosition(1.25, {
    tempo: 120,
    ticksPerQuarter: 480,
    timeSignature: '4/4',
  });
  assert.equal(position.bar, 1);
  assert.ok(position.tick > 0);

  const bar2 = secondsToPlaybackPosition(2.0, {
    tempo: 120,
    ticksPerQuarter: 480,
    timeSignature: '4/4',
  });
  assert.equal(bar2.bar, 2);
});

test('composition revision key changes when events change', () => {
  const first = compositionRevisionKey({
    schema_version: 'composition.v1',
    tempo: 120,
    tracks: [{ id: 'a', events: [{ pitch: 'C4', start_tick: 0, duration_ticks: 480, velocity: 80 }] }],
  });
  const second = compositionRevisionKey({
    schema_version: 'composition.v1',
    tempo: 120,
    tracks: [{ id: 'a', events: [{ pitch: 'D4', start_tick: 0, duration_ticks: 480, velocity: 80 }] }],
  });
  assert.notEqual(first, second);
});
