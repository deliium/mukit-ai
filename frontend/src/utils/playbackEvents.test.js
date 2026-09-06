import assert from 'node:assert/strict';
import test from 'node:test';

import { buildCanonicalPlaybackEvents } from './playbackEvents.js';

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

test('builds playback events from canonical ticks preserving polyphony and velocity', () => {
  const events = buildCanonicalPlaybackEvents({
    schema_version: 'composition.v1',
    tempo: 120,
    ticks_per_quarter: 480,
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
    schema_version: 'composition.v1',
    tempo: 120,
    ticks_per_quarter: 480,
    tracks: [{ id: 'piano-1', instrument: 'piano', events: [{ pitch: '', start_tick: 0, duration_ticks: 480, velocity: 64 }] }],
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

  const secondsPerTick = 60 / 96 / 480;
  assert.equal(melody.position, 240 * secondsPerTick);
  assert.equal(melody.duration, 240 * secondsPerTick);
  assert.equal(melody.stopPosition, melody.position + melody.duration);

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
