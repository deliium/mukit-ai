import assert from 'node:assert/strict';
import test from 'node:test';
import { migrateV1ToV2 } from './compositionVersion.js';
import {
  applyMidiTakeToComposition,
  extendCompositionToTick,
  MIDI_TAKE_ERROR_CODES,
} from './midiTakeApply.js';

function baseComposition() {
  return migrateV1ToV2({
    schema_version: 'composition.v1',
    tempo: 100,
    key: 'C major',
    time_signature: '4/4',
    ticks_per_quarter: 480,
    bar_count: 2,
    duration_ticks: 3840,
    sections: [
      { id: 'verse', type: 'verse', start_bar: 1, bar_count: 2, start_tick: 0, duration_ticks: 3840 },
    ],
    tracks: [
      {
        id: 'melody-1',
        name: 'Melody',
        instrument: 'piano',
        role: 'melody',
        midi_program: 0,
        channel: 1,
        is_drum: false,
        volume: 100,
        events: [],
      },
    ],
    harmony: [],
  });
}

test('extendCompositionToTick is no-op when already long enough', () => {
  const composition = baseComposition();
  const result = extendCompositionToTick(composition, 1000);
  assert.equal(result.barsAdded, 0);
  assert.equal(result.nextDuration, 3840);
});

test('extendCompositionToTick adds complete bars past the end', () => {
  const composition = baseComposition();
  // Need tick 4000 → one more bar (1920) → 5760
  const result = extendCompositionToTick(composition, 4000);
  assert.equal(result.error, undefined);
  assert.equal(result.barsAdded, 1);
  assert.equal(result.nextDuration, 5760);
  assert.equal(result.composition.bar_count, 3);
  assert.equal(result.composition.sections[0].bar_count, 3);
  assert.equal(result.composition.sections[0].duration_ticks, 5760);
});

test('applyMidiTakeToComposition appends notes and extends timeline', () => {
  const composition = baseComposition();
  const result = applyMidiTakeToComposition(composition, {
    trackId: 'melody-1',
    notes: [
      { pitch: 'C4', start_tick: 0, duration_ticks: 480, velocity: 90 },
      { pitch: 'E4', start_tick: 4000, duration_ticks: 240, velocity: 88 },
    ],
  });
  assert.equal(result.ok, true);
  assert.ok(result.barsAdded >= 1);
  assert.equal(result.composition.tracks[0].events.length, 2);
  assert.equal(result.noteRefs.length, 2);
  assert.ok(result.composition.duration_ticks >= 4240);
});

test('applyMidiTakeToComposition rejects locked tracks', () => {
  const composition = baseComposition();
  const result = applyMidiTakeToComposition(composition, {
    trackId: 'melody-1',
    lockedTrackIds: ['melody-1'],
    notes: [{ pitch: 'C4', start_tick: 0, duration_ticks: 480, velocity: 90 }],
  });
  assert.equal(result.ok, false);
  assert.equal(result.code, MIDI_TAKE_ERROR_CODES.TRACK_LOCKED);
});

test('applyMidiTakeToComposition merges sustain pedals', () => {
  const composition = baseComposition();
  const result = applyMidiTakeToComposition(composition, {
    trackId: 'melody-1',
    notes: [{ pitch: 'C4', start_tick: 0, duration_ticks: 480, velocity: 80 }],
    sustainPedals: [{ start_tick: 0, duration_ticks: 960 }],
  });
  assert.equal(result.ok, true);
  assert.equal(result.composition.tracks[0].sustain_pedals.length, 1);
});
