import assert from 'node:assert/strict';
import test from 'node:test';

import { applyRealizationToScheduleComposition } from './scheduleApply.js';

test('applyRealizationToScheduleComposition adjusts timing/velocity without pitch', () => {
  const composition = {
    schema_version: 'composition.v2',
    tracks: [
      {
        id: 't1',
        volume: 100,
        events: [
          {
            id: 'n1',
            pitch: 'C4',
            start_tick: 0,
            duration_ticks: 480,
            velocity: 80,
          },
        ],
        sustain_pedals: [],
      },
    ],
    harmony: [{ start_tick: 0, duration_ticks: 480, chord: 'C' }],
  };
  const realization = {
    notes: [
      {
        event_id: 'n1',
        track_id: 't1',
        tick_delta: 12,
        duration_delta: -40,
        velocity: 95,
      },
    ],
    track_gains: [{ track_id: 't1', gain: 0.5 }],
    sustain_spans: [{ track_id: 't1', start_tick: 0, end_tick: 240 }],
  };
  const out = applyRealizationToScheduleComposition(composition, realization);
  assert.equal(out.tracks[0].events[0].pitch, 'C4');
  assert.equal(out.tracks[0].events[0].start_tick, 12);
  assert.equal(out.tracks[0].events[0].duration_ticks, 440);
  assert.equal(out.tracks[0].events[0].velocity, 95);
  assert.equal(out.tracks[0].volume, 64);
  assert.equal(out.harmony[0].chord, 'C');
  assert.equal(composition.tracks[0].events[0].start_tick, 0);
});

test('applyRealizationToScheduleComposition no-ops without realization', () => {
  const composition = { tracks: [] };
  assert.equal(applyRealizationToScheduleComposition(composition, null), composition);
});
