import assert from 'node:assert/strict';
import test from 'node:test';

import {
  barDurationTicks,
  buildGridMetrics,
  clampNoteTiming,
  createTrackNote,
  deleteTrackNote,
  ensureCompositionNoteIds,
  midiToPitch,
  pitchToMidi,
  pixelToPitchMidi,
  pixelToTick,
  selectPitchRange,
  snapIntervalTicks,
  snapTick,
  updateTrackNote,
} from './pianoRollEvents.js';

const BASE_COMPOSITION = {
  schema_version: 'composition.v1',
  tempo: 100,
  key: 'C major',
  time_signature: '4/4',
  ticks_per_quarter: 480,
  bar_count: 2,
  duration_ticks: 3840,
  sections: [
    { type: 'intro', start_bar: 1, bar_count: 2, start_tick: 0, duration_ticks: 3840 },
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
      pan: 0,
      events: [
        { type: 'note', id: 'n1', pitch: 'C4', start_tick: 0, duration_ticks: 480, velocity: 90 },
        { type: 'note', id: 'n2', pitch: 'E4', start_tick: 0, duration_ticks: 480, velocity: 88 },
      ],
    },
  ],
  harmony: [{ bar: 1, chord: 'C' }],
};

test('pitchToMidi and midiToPitch round-trip common pitches', () => {
  assert.equal(pitchToMidi('C4').midi, 60);
  assert.equal(pitchToMidi('A4').midi, 69);
  assert.equal(pitchToMidi('Bb3').midi, 58);
  assert.equal(midiToPitch(60).pitch, 'C4');
  assert.equal(midiToPitch(61).pitch, 'C#4');
  assert.equal(pitchToMidi('invalid').midi, null);
  assert.match(pitchToMidi('invalid').warning, /invalid pitch/);
});

test('snap intervals for 480 TPQ cover 1/4 1/8 1/16', () => {
  assert.equal(snapIntervalTicks('1/4', 480).snapTicks, 480);
  assert.equal(snapIntervalTicks('1/8', 480).snapTicks, 240);
  assert.equal(snapIntervalTicks('1/16', 480).snapTicks, 120);
  assert.equal(snapIntervalTicks('1/3', 480).snapTicks, null);
});

test('bar duration ticks support 4/4 3/4 and 6/8', () => {
  assert.equal(barDurationTicks('4/4', 480).barTicks, 1920);
  assert.equal(barDurationTicks('3/4', 480).barTicks, 1440);
  assert.equal(barDurationTicks('6/8', 480).barTicks, 1440);
  assert.equal(barDurationTicks('5/7', 480).barTicks, null);
});

test('snapTick and pixel conversions', () => {
  assert.equal(snapTick(250, 240).tick, 240);
  assert.equal(snapTick(370, 240).tick, 480);
  assert.equal(snapTick(4000, 240, { maxTick: 3840 }).tick, 3840);
  assert.equal(pixelToTick(100, 0.05).tick, 2000);
  assert.equal(pixelToPitchMidi(0, { minMidi: 60, maxMidi: 72, rowHeight: 10 }).midi, 72);
  assert.equal(pixelToPitchMidi(25, { minMidi: 60, maxMidi: 72, rowHeight: 10 }).midi, 70);
});

test('selectPitchRange and grid metrics', () => {
  const range = selectPitchRange(BASE_COMPOSITION.tracks[0].events);
  assert.ok(range.minMidi <= 60);
  assert.ok(range.maxMidi >= 64);

  const metrics = buildGridMetrics(BASE_COMPOSITION, { snapValue: '1/8', pixelsPerTick: 0.1 });
  assert.equal(metrics.barTicks, 1920);
  assert.equal(metrics.snapTicks, 240);
  assert.equal(metrics.totalWidth, 384);
  assert.ok(!metrics.warning);

  const sixEight = buildGridMetrics({
    ...BASE_COMPOSITION,
    time_signature: '6/8',
    bar_count: 2,
    duration_ticks: 2880,
  }, { snapValue: '1/16' });
  assert.equal(sixEight.barTicks, 1440);
  assert.equal(sixEight.snapTicks, 120);
});

test('create update delete preserve polyphony and other fields', () => {
  const created = createTrackNote(BASE_COMPOSITION, 'melody-1', {
    pitch: 'G4',
    start_tick: 0,
    duration_ticks: 240,
    velocity: 95,
    staff: 1,
  });
  assert.equal(created.composition.tracks[0].events.length, 3);
  assert.equal(created.note.pitch, 'G4');
  assert.equal(created.note.staff, 1);
  assert.ok(created.note.id);
  // Original overlapping notes remain
  assert.equal(created.composition.tracks[0].events.filter((event) => event.start_tick === 0).length, 3);

  const moved = updateTrackNote(created.composition, 'melody-1', created.note.id, {
    start_tick: 480,
    midi: 67,
  });
  assert.equal(moved.note.pitch, 'G4');
  assert.equal(moved.note.start_tick, 480);
  assert.equal(moved.note.staff, 1);

  const resized = updateTrackNote(moved.composition, 'melody-1', created.note.id, {
    duration_ticks: 960,
  });
  assert.equal(resized.note.duration_ticks, 960);
  assert.equal(resized.note.pitch, 'G4');

  const deleted = deleteTrackNote(resized.composition, 'melody-1', created.note.id);
  assert.equal(deleted.deleted.id, created.note.id);
  assert.equal(deleted.composition.tracks[0].events.length, 2);
  assert.deepEqual(
    deleted.composition.tracks[0].events.map((event) => event.id),
    ['n1', 'n2'],
  );
});

test('clampNoteTiming prevents zero duration and composition overflow', () => {
  const overflow = clampNoteTiming({
    startTick: 3600,
    durationTicks: 960,
    compositionDurationTicks: 3840,
  });
  assert.equal(overflow.startTick, 3600);
  assert.equal(overflow.durationTicks, 240);
  assert.equal(overflow.clamped, true);

  const zero = clampNoteTiming({
    startTick: 0,
    durationTicks: 0,
    compositionDurationTicks: 3840,
  });
  assert.equal(zero.durationTicks, 1);
});

test('ensureCompositionNoteIds assigns missing ids without rewriting existing ones', () => {
  const withMissing = {
    ...BASE_COMPOSITION,
    tracks: [
      {
        ...BASE_COMPOSITION.tracks[0],
        events: [
          { type: 'note', pitch: 'C4', start_tick: 0, duration_ticks: 480, velocity: 90 },
          { type: 'note', id: 'keep-me', pitch: 'D4', start_tick: 480, duration_ticks: 480, velocity: 90 },
        ],
      },
    ],
  };
  const result = ensureCompositionNoteIds(withMissing);
  assert.equal(result.generatedCount, 1);
  assert.ok(result.composition.tracks[0].events[0].id);
  assert.equal(result.composition.tracks[0].events[1].id, 'keep-me');
});
