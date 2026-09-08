import assert from 'node:assert/strict';
import test from 'node:test';

import { createPlaybackEngine } from './tonePlaybackEngine.js';
import { compilePlaybackSchedule } from './playbackEvents.js';
import { readFileSync } from 'node:fs';

const expressiveFixturePath = new URL('./fixtures/composition_v2_expressive.json', import.meta.url);
const EXPRESSIVE_FIXTURE = JSON.parse(readFileSync(expressiveFixturePath, 'utf8'));

function createFakeTone() {
  const scheduled = [];
  const cancelled = [];
  let position = 0;
  let state = 'stopped';
  let nextId = 1;

  class FakeParam {
    constructor(value = 0) {
      this.value = value;
      this.ramps = [];
    }

    linearRampTo(value, duration) {
      this.ramps.push({ value, duration });
      this.value = value;
    }
  }

  class FakeGain {
    constructor(value = 1) {
      this.gain = new FakeParam(value);
      this.disposed = false;
      this.connections = [];
    }

    connect(node) {
      this.connections.push(node);
      return node;
    }

    toDestination() {
      return this;
    }

    dispose() {
      this.disposed = true;
    }
  }

  class FakePanner extends FakeGain {
    constructor(value = 0) {
      super();
      this.pan = new FakeParam(value);
      this.value = value;
    }
  }

  class FakeSynth extends FakeGain {
    constructor(options = {}) {
      super();
      this.options = options;
      this.triggers = [];
      this.attacks = [];
      this.releases = [];
      this.maxPolyphony = options.maxPolyphony;
    }

    triggerAttack(note, time, velocity) {
      this.attacks.push({ note, time, velocity });
    }

    triggerRelease(note, time) {
      this.releases.push({ note, time });
    }

    triggerAttackRelease(notes, duration, time, velocity) {
      this.triggers.push({ notes, duration, time, velocity });
    }
  }

  return {
    start: async () => {
      state = 'started';
    },
    Gain: FakeGain,
    Panner: FakePanner,
    Synth: FakeSynth,
    PolySynth: FakeSynth,
    MonoSynth: FakeSynth,
    MembraneSynth: FakeSynth,
    Transport: {
      bpm: { value: 120 },
      get state() {
        return state;
      },
      get seconds() {
        return position;
      },
      set position(value) {
        position = typeof value === 'number' ? value : 0;
      },
      get position() {
        return position;
      },
      schedule(callback, when) {
        const id = nextId;
        nextId += 1;
        scheduled.push({ id, callback, when, once: false });
        return id;
      },
      scheduleOnce(callback, when) {
        const id = nextId;
        nextId += 1;
        scheduled.push({ id, callback, when, once: true });
        return id;
      },
      cancel() {
        cancelled.push(scheduled.length);
        scheduled.length = 0;
      },
      start() {
        state = 'started';
      },
      pause() {
        state = 'paused';
      },
      stop() {
        state = 'stopped';
      },
      _scheduled: scheduled,
      _cancelled: cancelled,
    },
  };
}

test('schedules multi-track attack/release events and cleans up on stop/seek', async () => {
  const Tone = createFakeTone();
  const logs = [];
  const logger = {
    debug: (...args) => logs.push(['debug', ...args]),
    info: (...args) => logs.push(['info', ...args]),
    warn: (...args) => logs.push(['warn', ...args]),
    error: (...args) => logs.push(['error', ...args]),
  };

  const engine = createPlaybackEngine({ Tone, logger });
  const schedule = engine.prepare({
    tempo: 120,
    tracks: [
      { id: 'piano-1', instrument: 'piano', midi_program: 0, volume: 100, pan: 0 },
      { id: 'bass-1', instrument: 'electric_bass', midi_program: 33, volume: 80, pan: 10 },
    ],
    events: [
      {
        trackId: 'piano-1',
        pitch: 'C4',
        notes: ['C4'],
        position: 0,
        duration: 0.5,
        stopPosition: 0.5,
        velocity: 0.5,
        velocityMidi: 64,
      },
      {
        trackId: 'piano-1',
        pitch: 'E4',
        notes: ['E4'],
        position: 0,
        duration: 0.5,
        stopPosition: 0.5,
        velocity: 1,
        velocityMidi: 127,
      },
      {
        trackId: 'bass-1',
        pitch: 'C2',
        notes: ['C2'],
        position: 0.5,
        duration: 0.5,
        stopPosition: 1,
        velocity: 0.8,
        velocityMidi: 102,
      },
    ],
    trackOverrides: {
      'bass-1': { muted: true, volumeMidi: 80 },
    },
  });

  assert.equal(schedule.scheduledCount, 4);
  assert.equal(engine.getTrackNodeCount(), 2);
  assert.equal(Tone.Transport._scheduled.length, 5);

  await engine.start();
  assert.equal(Tone.Transport.state, 'started');

  engine.pause();
  assert.equal(Tone.Transport.state, 'paused');
  engine.resume();
  assert.equal(Tone.Transport.state, 'started');

  engine.seek(0.25);
  assert.equal(Tone.Transport.position, 0.25);
  assert.ok(engine.getScheduledEventCount() > 0);

  engine.stop({ seekToStart: true });
  assert.equal(engine.getTrackNodeCount(), 0);
  assert.equal(engine.getScheduledEventCount(), 0);
  assert.equal(Tone.Transport.position, 0);
  assert.ok(Tone.Transport._cancelled.length >= 1);

  engine.prepare({
    tempo: 100,
    tracks: [{ id: 'piano-1', instrument: 'piano', midi_program: 0, volume: 100 }],
    events: [{
      trackId: 'piano-1',
      pitch: 'C4',
      notes: ['C4'],
      position: 0,
      duration: 0.25,
      stopPosition: 0.25,
      velocity: 0.5,
      velocityMidi: 64,
    }],
  });
  assert.equal(engine.getScheduledEventCount(), 2);
  engine.dispose();
  assert.equal(engine.getTrackNodeCount(), 0);
});

test('applies mute/solo overrides without rebuilding composition data', () => {
  const Tone = createFakeTone();
  const engine = createPlaybackEngine({ Tone, logger: { debug() {}, info() {}, warn() {}, error() {} } });
  engine.prepare({
    tempo: 120,
    tracks: [
      { id: 'a', instrument: 'piano', volume: 127 },
      { id: 'b', instrument: 'bass', volume: 127 },
    ],
    events: [],
  });

  engine.applyTrackOverrides({
    a: { solo: true, volumeMidi: 127 },
    b: { solo: false, volumeMidi: 127 },
  });

  assert.equal(engine.getTrackNodeCount(), 2);
  engine.dispose();
});

test('prepares compiled v2 schedule with trailing silence completion', () => {
  const Tone = createFakeTone();
  const engine = createPlaybackEngine({ Tone, logger: { debug() {}, info() {}, warn() {}, error() {} } });
  const schedule = compilePlaybackSchedule(EXPRESSIVE_FIXTURE);
  const result = engine.prepare({
    tracks: EXPRESSIVE_FIXTURE.tracks,
    schedule,
    tempo: EXPRESSIVE_FIXTURE.tempo,
  });

  assert.ok(result.endPosition > 0);
  assert.equal(result.endPosition, schedule.totalDurationSeconds);
  assert.ok(Tone.Transport._scheduled.some((item) => item.once && item.when === schedule.totalDurationSeconds));
  engine.dispose();
});

test('seek rebuilds schedule from current transport position', () => {
  const Tone = createFakeTone();
  const engine = createPlaybackEngine({ Tone, logger: { debug() {}, info() {}, warn() {}, error() {} } });
  const schedule = compilePlaybackSchedule(EXPRESSIVE_FIXTURE);
  engine.prepare({
    tracks: EXPRESSIVE_FIXTURE.tracks,
    schedule,
    tempo: EXPRESSIVE_FIXTURE.tempo,
  });
  const before = Tone.Transport._scheduled.length;
  Tone.Transport.position = 1.5;
  engine.seek(1.5);
  assert.equal(Tone.Transport.position, 1.5);
  assert.ok(Tone.Transport._scheduled.length > 0);
  assert.notEqual(before, Tone.Transport._scheduled.length);
  engine.dispose();
});
