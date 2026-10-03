/**
 * Simulated-device journeys: capture → commit → validate (Task 8).
 */
import assert from 'node:assert/strict';
import test from 'node:test';
import { deleteNotes } from '../compositionEditorOperations.js';
import { makeNoteRef } from '../compositionEditorSelection.js';
import { createMidiPerformanceCapture } from '../midiPerformanceCapture.js';
import { applyMidiTakeToComposition } from '../midiTakeApply.js';
import { validateMusicJson } from '../musicJsonValidation.js';
import { UMP_MIDI2_OPCODE } from './constants.js';
import { buildSimulatedMidi2ChannelVoiceUmp } from './umpTransport.js';
import { degradeVelocityU16ToMidi7 } from './velocity.js';

const compositionBase = {
  schema_version: 'composition.v2',
  tempo: 120,
  key: 'C major',
  time_signature: '4/4',
  ticks_per_quarter: 480,
  bar_count: 2,
  duration_ticks: 3840,
  sections: [
    {
      type: 'verse',
      start_bar: 1,
      bar_count: 2,
      start_tick: 0,
      duration_ticks: 3840,
    },
  ],
  tracks: [
    {
      id: 'melody',
      name: 'Melody',
      instrument: 'Piano',
      role: 'melody',
      midi_program: 0,
      channel: 1,
      expression: 127,
      events: [],
      sustain_pedals: [],
      dynamic_marks: [],
      automation: [],
    },
  ],
  harmony: [],
  motifs: [],
  markers: [],
  tempo_changes: [],
  time_signature_changes: [],
  key_changes: [],
};

test('journey: legacy MIDI 1.0 melody + CC64 commits without note_performances', () => {
  let now = 0;
  const capture = createMidiPerformanceCapture({
    now: () => now,
    composition: compositionBase,
    originTick: 0,
    expressiveEnv: { VITE_MIDI_EXPRESSIVE_ENABLED: 'true' },
  });
  capture.start({ atMs: 0 });
  capture.injectMessage([0x90, 60, 100], { atMs: 0 });
  capture.injectMessage([0xb0, 64, 127], { atMs: 0 });
  now = 500;
  capture.injectMessage([0x80, 60, 0], { atMs: now });
  now = 1000;
  capture.injectMessage([0xb0, 64, 0], { atMs: now });
  const take = capture.stop({ atMs: now });
  assert.equal(take.noteCount, 1);
  assert.equal(take.performanceNoteCount, 0);
  const applied = applyMidiTakeToComposition(compositionBase, {
    trackId: 'melody',
    notes: take.notes,
    sustainPedals: take.sustainPedals,
  });
  assert.equal(applied.ok, true);
  assert.equal(applied.composition.tracks[0].events[0].velocity, 100);
  const perfs = applied.composition.tracks[0].note_performances;
  assert.ok(!perfs || perfs.length === 0);
  assert.equal(validateMusicJson(applied.composition).valid, true);
});

test('journey: MPE multi-channel bend/pressure → metadata + degraded velocity', () => {
  let now = 0;
  const capture = createMidiPerformanceCapture({
    now: () => now,
    composition: compositionBase,
    originTick: 0,
    mpeMappingEnabled: true,
  });
  capture.start({ atMs: 0 });
  capture.injectMessage([0x92, 64, 80], { atMs: 0 });
  now = 100;
  capture.injectMessage([0xe2, 0x00, 0x50], { atMs: now });
  capture.injectMessage([0xd2, 100], { atMs: now });
  now = 400;
  capture.injectMessage([0x82, 64, 0], { atMs: now });
  const take = capture.stop({ atMs: now });
  assert.equal(take.performanceNoteCount, 1);
  const applied = applyMidiTakeToComposition(compositionBase, {
    trackId: 'melody',
    notes: take.notes,
  });
  assert.equal(applied.ok, true);
  const row = applied.composition.tracks[0].note_performances[0];
  assert.equal(row.event_id, applied.noteRefs[0].eventId);
  assert.ok(row.pitch_cents?.length >= 1);
  assert.ok(row.pressure?.length >= 1);
  assert.equal(
    applied.composition.tracks[0].events[0].velocity,
    degradeVelocityU16ToMidi7(row.velocity_u16),
  );
  assert.equal(validateMusicJson(applied.composition).valid, true);
});

test('journey: simulated UMP note-on preserves velocity_u16 and degrades velocity', () => {
  let now = 0;
  const capture = createMidiPerformanceCapture({
    now: () => now,
    composition: compositionBase,
    originTick: 0,
    transport: 'ump_experimental',
  });
  capture.start({ atMs: 0 });
  const words = buildSimulatedMidi2ChannelVoiceUmp({
    opcode: UMP_MIDI2_OPCODE.NOTE_ON,
    channel: 0,
    data1: 60,
    velocityU16: 32000,
  });
  capture.injectMessage([], { atMs: 0, umpWords: words });
  now = 300;
  const off = buildSimulatedMidi2ChannelVoiceUmp({
    opcode: UMP_MIDI2_OPCODE.NOTE_OFF,
    channel: 0,
    data1: 60,
    velocityU16: 0,
  });
  capture.injectMessage([], { atMs: now, umpWords: off });
  const take = capture.stop({ atMs: now });
  assert.equal(take.notes[0].velocity_u16, 32000);
  assert.equal(take.notes[0].velocity, 63);
  const applied = applyMidiTakeToComposition(compositionBase, {
    trackId: 'melody',
    notes: take.notes,
  });
  assert.equal(applied.ok, true);
  assert.equal(applied.composition.tracks[0].events[0].velocity, 63);
  assert.equal(applied.composition.tracks[0].note_performances[0].velocity_u16, 32000);
});

test('journey: expressive disabled ignores pitch bend', () => {
  const capture = createMidiPerformanceCapture({
    now: () => 0,
    composition: compositionBase,
    originTick: 0,
    expressiveEnv: { VITE_MIDI_EXPRESSIVE_ENABLED: '0' },
    mpeMappingEnabled: true,
  });
  capture.start({ atMs: 0 });
  capture.injectMessage([0x90, 60, 90], { atMs: 0 });
  capture.injectMessage([0xe0, 0x00, 0x60], { atMs: 0 });
  const take = capture.stop({ atMs: 200 });
  assert.equal(take.performanceNoteCount, 0);
  assert.ok(take.ignoredCount >= 1);
});

test('journey: metadata strip still validates; editor delete prunes rows', () => {
  const withPerf = {
    ...compositionBase,
    tracks: [
      {
        ...compositionBase.tracks[0],
        events: [
          {
            id: 'e1',
            type: 'note',
            pitch: 'C4',
            start_tick: 0,
            duration_ticks: 480,
            velocity: 90,
            articulations: [],
            tie: null,
          },
          {
            id: 'e2',
            type: 'note',
            pitch: 'E4',
            start_tick: 480,
            duration_ticks: 480,
            velocity: 90,
            articulations: [],
            tie: null,
          },
        ],
        note_performances: [
          { event_id: 'e1', velocity_u16: 90 << 9 },
          { event_id: 'e2', velocity_u16: 90 << 9 },
        ],
      },
    ],
  };
  assert.equal(validateMusicJson(withPerf).valid, true);
  const stripped = {
    ...withPerf,
    tracks: withPerf.tracks.map((t) => {
      const next = { ...t };
      delete next.note_performances;
      return next;
    }),
  };
  assert.equal(validateMusicJson(stripped).valid, true);
  const deleted = deleteNotes(withPerf, [makeNoteRef('melody', 'e1')]);
  assert.equal(deleted.ok, true);
  assert.equal(deleted.composition.tracks[0].note_performances.length, 1);
  assert.equal(deleted.composition.tracks[0].note_performances[0].event_id, 'e2');
  assert.equal(validateMusicJson(deleted.composition).valid, true);
});
