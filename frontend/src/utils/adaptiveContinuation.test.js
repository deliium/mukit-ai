import assert from 'node:assert/strict';
import test from 'node:test';

import { continuationEventsToSchedule } from './adaptiveContinuation.js';

const note = (start) => ({ start_tick: start, duration_ticks: 480, pitch: 60 });

test('a reuse-loop buffer and an inaudible model buffer schedule nothing', () => {
  const reuse = continuationEventsToSchedule(
    { audible: false, source: 'fallback', fallback_kind: 'reuse_loop' },
    { events: [note(9600)] },
    0,
  );
  const outside = continuationEventsToSchedule(
    { audible: false, source: 'model', fallback_kind: 'reuse_loop' },
    { events: [note(9600), note(11520)] },
    0,
  );
  assert.deepEqual(reuse, []);
  assert.deepEqual(outside, []);
});

test('an audible model buffer keeps ticks at or after the playhead', () => {
  const kept = continuationEventsToSchedule(
    { audible: true, source: 'model', fallback_kind: 'accompaniment' },
    { events: [note(4800), note(9600), note(11520)] },
    9600,
  );
  assert.deepEqual(kept.map((event) => event.start_tick), [9600, 11520]);
});
