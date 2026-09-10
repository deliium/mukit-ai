import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  articulationGateTicks,
  articulationVelocity,
  automationSampleInterval,
  buildCanonicalPlaybackEvents,
  compilePlaybackSchedule,
  combinedExpression,
  controllerStateAtTick,
  logicalNotesSoundingAtTick,
  sustainReleaseTick,
} from './playbackEvents.js';
import { tickToSeconds, compileTimeline } from './compositionTimeline.js';

const FIDELITY_FIXTURE = {
  schema_version: 'composition.v1',
  tempo: 96,
  key: 'G minor',
  time_signature: '6/8',
  ticks_per_quarter: 480,
  bar_count: 2,
  duration_ticks: 2880,
  tracks: [
    {
      id: 'melody-1',
      name: 'Melody',
      instrument: 'flute',
      role: 'melody',
      midi_program: 73,
      channel: 1,
      is_drum: false,
      volume: 110,
      pan: -10,
      events: [
        { type: 'note', pitch: 'G5', start_tick: 240, duration_ticks: 240, velocity: 96 },
        { type: 'note', pitch: 'Bb5', start_tick: 720, duration_ticks: 480, velocity: 88 },
        { type: 'note', pitch: 'D5', start_tick: 1200, duration_ticks: 720, velocity: 100 },
      ],
    },
    {
      id: 'harmony-2',
      name: 'Harmony',
      instrument: 'piano',
      role: 'harmony',
      midi_program: 0,
      channel: 2,
      is_drum: false,
      volume: 90,
      pan: 0,
      events: [
        { type: 'note', pitch: 'G3', start_tick: 0, duration_ticks: 1440, velocity: 70 },
        { type: 'note', pitch: 'Bb3', start_tick: 0, duration_ticks: 1440, velocity: 72 },
        { type: 'note', pitch: 'D4', start_tick: 0, duration_ticks: 720, velocity: 74 },
        { type: 'note', pitch: 'F4', start_tick: 1440, duration_ticks: 960, velocity: 78 },
        { type: 'note', pitch: 'A4', start_tick: 1680, duration_ticks: 720, velocity: 82 },
      ],
    },
    {
      id: 'bass-3',
      name: 'Bass',
      instrument: 'electric_bass',
      role: 'bass',
      midi_program: 33,
      channel: 3,
      is_drum: false,
      volume: 100,
      pan: 12,
      events: [
        { type: 'note', pitch: 'G2', start_tick: 0, duration_ticks: 720, velocity: 101 },
        { type: 'note', pitch: 'D2', start_tick: 720, duration_ticks: 720, velocity: 95 },
        { type: 'note', pitch: 'G2', start_tick: 1440, duration_ticks: 1440, velocity: 105 },
      ],
    },
    {
      id: 'pad-4',
      name: 'Pad',
      instrument: 'strings',
      role: 'pad',
      midi_program: 48,
      channel: 4,
      is_drum: false,
      volume: 60,
      pan: -20,
      events: [],
    },
  ],
  harmony: [
    { bar: 1, chord: 'Gm' },
    { bar: 2, chord: 'D' },
  ],
};

const expressiveFixturePath = new URL('./fixtures/composition_v2_expressive.json', import.meta.url);
const EXPRESSIVE_FIXTURE = JSON.parse(readFileSync(expressiveFixturePath, 'utf8'));

test('builds playback events from canonical ticks preserving polyphony and velocity', () => {
  const events = buildCanonicalPlaybackEvents({
    schema_version: 'composition.v2',
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
        volume: 100,
        pan: 0,
        events: [
          { pitch: 'C4', start_tick: 0, duration_ticks: 480, velocity: 64 },
          { pitch: 'E4', start_tick: 0, duration_ticks: 480, velocity: 127 },
          { pitch: 'G4', start_tick: 480, duration_ticks: 240, velocity: 80 },
        ],
      },
    ],
    harmony: [],
  });

  assert.equal(events.length, 3);
  assert.equal(events[0].position, 0);
  assert.equal(events[1].position, 0);
  assert.equal(events[0].duration, 0.5);
  assert.equal(events[2].position, 0.5);
  assert.equal(events[1].velocity, 1);
  assert.equal(events[1].velocityMidi, 127);
  assert.equal(events[0].pitch, 'C4');
  assert.equal(events[0].trackId, 'piano-1');
  assert.equal(events[0].midiProgram, 0);
});

test('skips invalid canonical playback events', () => {
  const events = buildCanonicalPlaybackEvents({
    schema_version: 'composition.v2',
    tempo: 120,
    key: 'C major',
    time_signature: '4/4',
    ticks_per_quarter: 480,
    bar_count: 1,
    duration_ticks: 1920,
    sections: [{ type: 'intro', start_bar: 1, bar_count: 1, start_tick: 0, duration_ticks: 1920 }],
    tracks: [{ id: 'piano-1', instrument: 'piano', events: [{ pitch: '', start_tick: 0, duration_ticks: 480, velocity: 64 }] }],
    harmony: [],
  });

  assert.deepEqual(events, []);
});

test('fidelity fixture preserves multi-track parity fields and ignores harmony', () => {
  const events = buildCanonicalPlaybackEvents(FIDELITY_FIXTURE);
  assert.equal(events.length, 11);

  const simultaneous = events.filter((event) => event.startTick === 0);
  assert.equal(simultaneous.length, 4);
  assert.ok(simultaneous.every((event) => event.position === 0));

  const melody = events.find((event) => event.trackId === 'melody-1' && event.pitch === 'G5');
  assert.equal(melody.startTick, 240);
  assert.equal(melody.durationTicks, 240);
  assert.equal(melody.velocityMidi, 96);
  assert.equal(melody.instrument, 'flute');
  assert.equal(melody.midiProgram, 73);
  assert.equal(melody.channel, 1);
  assert.equal(melody.trackVolume, 110);
  assert.equal(melody.pan, -10);

  const timeline = compileTimeline(FIDELITY_FIXTURE);
  assert.equal(melody.position, tickToSeconds(timeline, 240));
  assert.equal(melody.duration, tickToSeconds(timeline, 480) - tickToSeconds(timeline, 240));
  assert.equal(melody.stopPosition, tickToSeconds(timeline, 480));

  assert.ok(events.every((event) => !Object.hasOwn(event, 'chord')));
  assert.equal(events.some((event) => event.trackId === 'pad-4'), false);

  for (let index = 1; index < events.length; index += 1) {
    const previous = events[index - 1];
    const current = events[index];
    assert.ok(
      previous.position < current.position
      || (previous.position === current.position && previous.trackId <= current.trackId),
    );
  }
});

test('does not invent events from harmony-only canonical tracks', () => {
  const events = buildCanonicalPlaybackEvents({
    ...FIDELITY_FIXTURE,
    tracks: FIDELITY_FIXTURE.tracks.map((track) => ({ ...track, events: [] })),
  });
  assert.deepEqual(events, []);
});

test('articulation helpers mirror backend transforms', () => {
  assert.equal(articulationGateTicks(480, ['staccato']), 240);
  assert.equal(articulationGateTicks(480, ['staccatissimo']), 120);
  assert.equal(articulationGateTicks(480, ['marcato']), 360);
  assert.equal(articulationGateTicks(480, ['tenuto']), 480);
  assert.equal(articulationGateTicks(1, ['staccato']), 1);
  assert.equal(articulationVelocity(80, ['accent']), 92);
  assert.equal(articulationVelocity(80, ['marcato']), 100);
  assert.equal(combinedExpression(127, 96), 96);
  assert.equal(automationSampleInterval(480), 30);
});

test('collapses tie chains and applies staccato gate in expressive fixture', () => {
  const schedule = compilePlaybackSchedule(EXPRESSIVE_FIXTURE);
  assert.ok(schedule);
  const melody = schedule.logicalNotes.filter((note) => note.trackId === 'melody-1');

  const staccato = melody.find((note) => note.pitch === 'E4');
  assert.equal(staccato.durationTicks, 240);
  assert.equal(staccato.startTick, 480);

  const tied = melody.find((note) => note.pitch === 'G4');
  assert.equal(tied.startTick, 960);
  assert.equal(tied.durationTicks, 960);
  assert.equal(tied.velocityMidi, 86);
});

test('defers release for notes attacked during sustain pedal span', () => {
  const schedule = compilePlaybackSchedule(EXPRESSIVE_FIXTURE);
  const bass = schedule.logicalNotes.find((note) => note.trackId === 'melody-1' && note.pitch === 'C4');
  assert.equal(bass.startTick, 0);
  assert.equal(bass.durationTicks, 480);
  assert.equal(bass.releaseTick, 1920);
  assert.ok(bass.stopPosition > tickToSeconds(schedule.timeline, 480));
});

test('uses piecewise tempo for timing at tempo boundary', () => {
  const schedule = compilePlaybackSchedule(EXPRESSIVE_FIXTURE);
  const timeline = schedule.timeline;
  const noteAfterTempoChange = schedule.logicalNotes.find((note) => note.pitch === 'C5');
  assert.equal(noteAfterTempoChange.startTick, 3840);
  assert.equal(tickToSeconds(timeline, 3840), 4.8);
  assert.equal(schedule.totalDurationSeconds, 10.8);
});

test('schedule includes controller automation items', () => {
  const schedule = compilePlaybackSchedule(EXPRESSIVE_FIXTURE);
  const controllers = schedule.items.filter((item) => item.kind === 'controller');
  assert.ok(controllers.some((item) => item.parameter === 'expression'));
  assert.ok(controllers.some((item) => item.parameter === 'volume'));
});

test('same-tick ordering places releases before attacks', () => {
  const schedule = compilePlaybackSchedule({
    schema_version: 'composition.v2',
    tempo: 120,
    key: 'C major',
    time_signature: '4/4',
    ticks_per_quarter: 480,
    bar_count: 1,
    duration_ticks: 1920,
    sections: [{ type: 'intro', start_bar: 1, bar_count: 1, start_tick: 0, duration_ticks: 1920 }],
    tracks: [{
      id: 't1',
      instrument: 'piano',
      role: 'harmony',
      midi_program: 0,
      channel: 1,
      volume: 100,
      events: [
        { pitch: 'C4', start_tick: 0, duration_ticks: 480, velocity: 80 },
        { pitch: 'D4', start_tick: 480, duration_ticks: 480, velocity: 80 },
      ],
    }],
    harmony: [],
  });

  const atBoundary = schedule.items.filter((item) => item.tick === 480 && item.kind !== 'controller');
  assert.equal(atBoundary[0].kind, 'release');
  assert.equal(atBoundary[1].kind, 'attack');
});

test('assigns stable noteId so overlapping same-pitch notes are distinct', () => {
  const schedule = compilePlaybackSchedule({
    schema_version: 'composition.v2',
    tempo: 120,
    key: 'C major',
    time_signature: '4/4',
    ticks_per_quarter: 480,
    bar_count: 1,
    duration_ticks: 1920,
    sections: [{ type: 'intro', start_bar: 1, bar_count: 1, start_tick: 0, duration_ticks: 1920 }],
    tracks: [{
      id: 't1',
      instrument: 'piano',
      role: 'melody',
      midi_program: 0,
      channel: 1,
      volume: 100,
      events: [
        { pitch: 'C4', start_tick: 0, duration_ticks: 960, velocity: 80 },
        { pitch: 'C4', start_tick: 240, duration_ticks: 960, velocity: 90 },
      ],
    }],
    harmony: [],
  });

  assert.equal(schedule.logicalNotes.length, 2);
  assert.notEqual(schedule.logicalNotes[0].noteId, schedule.logicalNotes[1].noteId);
  const attacks = schedule.items.filter((item) => item.kind === 'attack');
  const releases = schedule.items.filter((item) => item.kind === 'release');
  assert.equal(new Set(attacks.map((item) => item.noteId)).size, 2);
  assert.deepEqual(
    attacks.map((item) => item.noteId).sort(),
    releases.map((item) => item.noteId).sort(),
  );
  const sounding = logicalNotesSoundingAtTick(schedule, 300);
  assert.equal(sounding.length, 2);
});

test('defers release when note-off falls in pedal even if attack was before pedal-down', () => {
  assert.equal(sustainReleaseTick(0, 480, [{ start_tick: 240, duration_ticks: 720 }]), 960);
  assert.equal(sustainReleaseTick(0, 240, [{ start_tick: 240, duration_ticks: 720 }]), 960);
  // Half-open: note-off exactly at pedal end is not held.
  assert.equal(sustainReleaseTick(0, 960, [{ start_tick: 240, duration_ticks: 720 }]), 960);
  // Attack during pedal still held to pedal-up.
  assert.equal(sustainReleaseTick(300, 400, [{ start_tick: 240, duration_ticks: 720 }]), 960);

  const schedule = compilePlaybackSchedule({
    schema_version: 'composition.v2',
    tempo: 120,
    key: 'C major',
    time_signature: '4/4',
    ticks_per_quarter: 480,
    bar_count: 1,
    duration_ticks: 1920,
    sections: [{ type: 'intro', start_bar: 1, bar_count: 1, start_tick: 0, duration_ticks: 1920 }],
    tracks: [{
      id: 't1',
      instrument: 'piano',
      role: 'melody',
      midi_program: 0,
      channel: 1,
      volume: 100,
      sustain_pedals: [{ start_tick: 240, duration_ticks: 720 }],
      events: [
        { pitch: 'C4', start_tick: 0, duration_ticks: 480, velocity: 80 },
      ],
    }],
    harmony: [],
  });
  assert.equal(schedule.logicalNotes[0].releaseTick, 960);
});

test('exposes linear controller segments and state-at-tick reconstruction', () => {
  const schedule = compilePlaybackSchedule({
    schema_version: 'composition.v2',
    tempo: 120,
    key: 'C major',
    time_signature: '4/4',
    ticks_per_quarter: 480,
    bar_count: 1,
    duration_ticks: 1920,
    sections: [{ type: 'intro', start_bar: 1, bar_count: 1, start_tick: 0, duration_ticks: 1920 }],
    tracks: [{
      id: 't1',
      instrument: 'piano',
      role: 'melody',
      midi_program: 0,
      channel: 1,
      volume: 64,
      pan: 0,
      expression: 127,
      automation: [
        {
          parameter: 'volume',
          interpolation: 'linear',
          points: [
            { tick: 0, value: 64 },
            { tick: 480, value: 127 },
          ],
        },
      ],
      events: [
        { pitch: 'C4', start_tick: 0, duration_ticks: 480, velocity: 80 },
      ],
    }],
    harmony: [],
  });

  assert.ok(schedule.controllerSegments.some((segment) => (
    segment.parameter === 'volume' && segment.interpolation === 'linear'
  )));
  const mid = controllerStateAtTick(schedule, 't1', 240);
  assert.ok(mid.volume > 64 && mid.volume < 127);
  assert.equal(schedule.summary.controllerSegmentCount > 0, true);
});
