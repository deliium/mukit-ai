import assert from 'node:assert/strict';
import test from 'node:test';

import {
  applyTieChain,
  barDurationTicks,
  buildGridMetrics,
  buildTimelineCues,
  clampNoteTiming,
  countV2FeatureSummary,
  createTrackNote,
  deleteTrackNote,
  ensureCompositionNoteIds,
  midiToPitch,
  pitchToMidi,
  pixelToPitchMidi,
  pixelToTick,
  removeTieChain,
  selectContiguousCompatibleNotes,
  selectPitchRange,
  snapIntervalTicks,
  snapTick,
  toggleNoteArticulation,
  updateTrackNote,
} from './pianoRollEvents.js';
import { migrateV1ToV2 } from './compositionVersion.js';

const BASE_COMPOSITION = migrateV1ToV2({
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
});

const EXPRESSIVE_COMPOSITION = {
  ...BASE_COMPOSITION,
  bar_count: 3,
  duration_ticks: 4800,
  tempo_changes: [{ tick: 1920, bpm: 90 }],
  time_signature_changes: [{ tick: 1920, time_signature: '3/4' }],
  key_changes: [{ tick: 1920, key: 'G major' }],
  markers: [{ tick: 960, kind: 'rehearsal', label: 'A' }],
  sections: [
    {
      id: 'sec-a',
      type: 'intro',
      label: 'Intro',
      start_bar: 1,
      bar_count: 1,
      start_tick: 0,
      duration_ticks: 1920,
    },
    {
      id: 'sec-b',
      type: 'verse',
      label: 'Verse',
      start_bar: 2,
      bar_count: 2,
      start_tick: 1920,
      duration_ticks: 2880,
    },
  ],
  tracks: [
    {
      ...BASE_COMPOSITION.tracks[0],
      dynamic_marks: [{ tick: 480, level: 'mf' }],
      sustain_pedals: [{ start_tick: 0, duration_ticks: 960 }],
      automation: [{ parameter: 'volume', interpolation: 'step', points: [{ tick: 480, value: 100 }] }],
      events: [
        {
          type: 'note',
          id: 'n1',
          pitch: 'C4',
          start_tick: 0,
          duration_ticks: 480,
          velocity: 90,
          articulations: ['tenuto'],
          tie: null,
        },
        {
          type: 'note',
          id: 'n2',
          pitch: 'C4',
          start_tick: 480,
          duration_ticks: 480,
          velocity: 90,
          articulations: [],
          tie: null,
        },
        {
          type: 'note',
          id: 'n3',
          pitch: 'E4',
          start_tick: 0,
          duration_ticks: 480,
          velocity: 88,
          articulations: [],
          tie: null,
        },
      ],
    },
  ],
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

test('mutations preserve V2 root and track expression fields', () => {
  const created = createTrackNote(EXPRESSIVE_COMPOSITION, 'melody-1', {
    pitch: 'G4',
    start_tick: 960,
    duration_ticks: 480,
  });
  assert.equal(created.composition.tempo_changes.length, 1);
  assert.equal(created.composition.tracks[0].dynamic_marks.length, 1);
  assert.equal(created.composition.tracks[0].events[0].articulations[0], 'tenuto');

  const moved = updateTrackNote(created.composition, 'melody-1', created.note.id, { start_tick: 1440 });
  assert.equal(moved.composition.markers[0].label, 'A');
  assert.equal(moved.composition.sections[0].label, 'Intro');

  const deleted = deleteTrackNote(moved.composition, 'melody-1', created.note.id);
  assert.equal(deleted.composition.tracks[0].automation.length, 1);
  assert.equal(deleted.composition.key_changes[0].key, 'G major');
});

test('toggleNoteArticulation rejects conflicting articulations without partial writes', () => {
  const withAccent = toggleNoteArticulation(EXPRESSIVE_COMPOSITION, 'melody-1', 'n1', 'accent');
  assert.deepEqual(withAccent.note.articulations, ['tenuto', 'accent']);

  const conflict = toggleNoteArticulation(withAccent.composition, 'melody-1', 'n1', 'marcato');
  assert.equal(conflict.note, null);
  assert.match(conflict.warning, /marcato/);
  assert.deepEqual(
    withAccent.composition.tracks[0].events.find((event) => event.id === 'n1').articulations,
    ['tenuto', 'accent'],
  );
});

test('applyTieChain writes complete validated chain atomically', () => {
  const tied = applyTieChain(EXPRESSIVE_COMPOSITION, 'melody-1', ['n1', 'n2']);
  assert.equal(tied.notes.length, 2);
  assert.equal(tied.notes[0].tie.type, 'start');
  assert.equal(tied.notes[1].tie.type, 'stop');
  assert.equal(tied.notes[0].tie.group_id, tied.notes[1].tie.group_id);

  const incompatible = applyTieChain(EXPRESSIVE_COMPOSITION, 'melody-1', ['n1', 'n3']);
  assert.equal(incompatible.notes.length, 0);
});

test('removeTieChain clears entire group in one update', () => {
  const tied = applyTieChain(EXPRESSIVE_COMPOSITION, 'melody-1', ['n1', 'n2']);
  const cleared = removeTieChain(tied.composition, 'melody-1', ['n1']);
  assert.equal(cleared.clearedCount, 2);
  assert.equal(cleared.composition.tracks[0].events.every((event) => !event.tie), true);
});

test('deleteTrackNote clears tie metadata from remaining group members', () => {
  const tied = applyTieChain(EXPRESSIVE_COMPOSITION, 'melody-1', ['n1', 'n2']);
  const deleted = deleteTrackNote(tied.composition, 'melody-1', 'n1');
  assert.equal(deleted.deleted.id, 'n1');
  assert.equal(deleted.composition.tracks[0].events.find((event) => event.id === 'n2').tie, null);
});

test('buildTimelineCues exposes tempo meter key section and marker labels', () => {
  const cues = buildTimelineCues(EXPRESSIVE_COMPOSITION);
  assert.ok(cues.some((cue) => cue.kind === 'tempo' && cue.label.includes('90')));
  assert.ok(cues.some((cue) => cue.kind === 'meter' && cue.label === '3/4'));
  assert.ok(cues.some((cue) => cue.kind === 'key' && cue.label === 'G major'));
  assert.ok(cues.some((cue) => cue.kind === 'section' && cue.label === 'Intro'));
  assert.ok(cues.some((cue) => cue.kind === 'rehearsal' && cue.label === 'A'));
});

test('countV2FeatureSummary returns aggregate counts without dumping payloads', () => {
  const tied = applyTieChain(EXPRESSIVE_COMPOSITION, 'melody-1', ['n1', 'n2']);
  const summary = countV2FeatureSummary(tied.composition);
  assert.equal(summary.tempoChanges, 1);
  assert.equal(summary.dynamicMarks, 1);
  assert.equal(summary.tiedNotes, 2);
  assert.equal(summary.articulatedNotes, 1);
});

test('selectContiguousCompatibleNotes requires shared pitch and contiguous boundaries', () => {
  const events = EXPRESSIVE_COMPOSITION.tracks[0].events;
  const ok = selectContiguousCompatibleNotes(events, ['n1', 'n2']);
  assert.equal(ok.notes.length, 2);
  const bad = selectContiguousCompatibleNotes(events, ['n1', 'n3']);
  assert.equal(bad.notes.length, 0);
});
