import assert from 'node:assert/strict';
import test from 'node:test';

import { createPlaybackEngine } from './tonePlaybackEngine.js';
import { compilePlaybackSchedule } from './playbackEvents.js';
import {
  clampSeekSecondsToLoop,
  deriveLoopRangeFromSelection,
  normalizePlaybackLoop,
  reconcilePlaybackLoop,
} from './playbackLoop.js';
import { ticksToPlaybackSeconds } from './playbackPosition.js';
import { readFileSync } from 'node:fs';

const expressiveFixturePath = new URL('./fixtures/composition_v2_expressive.json', import.meta.url);
const EXPRESSIVE_FIXTURE = JSON.parse(readFileSync(expressiveFixturePath, 'utf8'));

const timelineFixturePath = new URL('./fixtures/timeline_mixed_meter_tempo.json', import.meta.url);
const TIMELINE_FIXTURE = JSON.parse(readFileSync(timelineFixturePath, 'utf8'));

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

  class FakeReverb extends FakeGain {
    constructor(options = {}) {
      super(1);
      this.options = options;
      this.generated = false;
    }

    async generate() {
      this.generated = true;
    }
  }

  class FakeLimiter extends FakeGain {
    constructor(threshold = -1) {
      super(1);
      this.threshold = threshold;
    }
  }

  class FakeMeter extends FakeGain {
    constructor(options = {}) {
      super(1);
      this.options = options;
      this._value = 0.25;
    }

    getValue() {
      return this._value;
    }
  }

  return {
    start: async () => {
      state = 'started';
    },
    now: () => 0,
    Gain: FakeGain,
    Panner: FakePanner,
    Synth: FakeSynth,
    PolySynth: FakeSynth,
    MonoSynth: FakeSynth,
    MembraneSynth: FakeSynth,
    Reverb: FakeReverb,
    Limiter: FakeLimiter,
    Meter: FakeMeter,
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
      cancel(eventId) {
        if (eventId == null) {
          cancelled.push(scheduled.length);
          scheduled.length = 0;
          return;
        }
        const index = scheduled.findIndex((item) => item.id === eventId);
        if (index >= 0) {
          scheduled.splice(index, 1);
          cancelled.push(1);
        }
      },
      clear(eventId) {
        this.cancel(eventId);
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

function silentLogger() {
  return { debug() {}, info() {}, warn() {}, error() {} };
}

test('schedules multi-track attack/release events including muted tracks', async () => {
  const Tone = createFakeTone();
  const logs = [];
  const logger = {
    debug: (...args) => logs.push(['debug', ...args]),
    info: (...args) => logs.push(['info', ...args]),
    warn: (...args) => logs.push(['warn', ...args]),
    error: (...args) => logs.push(['error', ...args]),
  };

  const engine = createPlaybackEngine({ Tone, logger });
  const schedule = await engine.prepare({
    loadInstruments: false,
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

  // All tracks scheduled; mute is live uiGain (0), not schedule omission.
  assert.equal(schedule.scheduledCount, 6);
  assert.equal(engine.getTrackNodeCount(), 2);
  assert.equal(engine.getTrackEffectiveGain('bass-1'), 0);
  assert.ok(engine.getTrackEffectiveGain('piano-1') > 0);
  assert.equal(Tone.Transport._scheduled.length, 7);

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

  await engine.prepare({
    loadInstruments: false,
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

test('applies mute/solo overrides without rebuilding composition data', async () => {
  const Tone = createFakeTone();
  const engine = createPlaybackEngine({ Tone, logger: silentLogger() });
  await engine.prepare({
    loadInstruments: false,
    tempo: 120,
    tracks: [
      { id: 'a', instrument: 'piano', volume: 127 },
      { id: 'b', instrument: 'bass', volume: 127 },
    ],
    events: [
      {
        trackId: 'b',
        pitch: 'C2',
        position: 0,
        stopPosition: 0.5,
        velocity: 0.5,
        velocityMidi: 64,
      },
    ],
    trackOverrides: {
      b: { muted: true, volumeMidi: 127 },
    },
  });

  assert.equal(engine.getTrackEffectiveGain('b'), 0);
  assert.ok(engine.getScheduledEventCount() >= 2);

  engine.applyTrackOverrides({
    a: { solo: false, volumeMidi: 127 },
    b: { muted: false, volumeMidi: 127 },
  });

  assert.ok(engine.getTrackEffectiveGain('b') > 0);
  assert.equal(engine.getTrackNodeCount(), 2);
  engine.dispose();
});

test('prepares compiled v2 schedule with trailing silence completion', async () => {
  const Tone = createFakeTone();
  const engine = createPlaybackEngine({ Tone, logger: silentLogger() });
  const schedule = compilePlaybackSchedule(EXPRESSIVE_FIXTURE);
  const result = await engine.prepare({
    loadInstruments: false,
    tracks: EXPRESSIVE_FIXTURE.tracks,
    schedule,
    composition: EXPRESSIVE_FIXTURE,
    tempo: EXPRESSIVE_FIXTURE.tempo,
  });

  assert.ok(result.endPosition > 0);
  assert.equal(result.endPosition, schedule.totalDurationSeconds);
  assert.ok(Tone.Transport._scheduled.some((item) => item.once && item.when === schedule.totalDurationSeconds));
  engine.dispose();
});

test('seek rebuilds schedule from current transport position', async () => {
  const Tone = createFakeTone();
  const engine = createPlaybackEngine({ Tone, logger: silentLogger() });
  const schedule = compilePlaybackSchedule(EXPRESSIVE_FIXTURE);
  await engine.prepare({
    loadInstruments: false,
    tracks: EXPRESSIVE_FIXTURE.tracks,
    schedule,
    composition: EXPRESSIVE_FIXTURE,
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

test('prepare honors nonzero startTick via timeline conversion', async () => {
  const Tone = createFakeTone();
  const engine = createPlaybackEngine({ Tone, logger: silentLogger() });
  const schedule = compilePlaybackSchedule(TIMELINE_FIXTURE);
  const startTick = 1920;
  const expectedSeconds = ticksToPlaybackSeconds(startTick, { composition: TIMELINE_FIXTURE });

  const result = await engine.prepare({
    loadInstruments: false,
    tracks: TIMELINE_FIXTURE.tracks,
    schedule,
    composition: TIMELINE_FIXTURE,
    tempo: TIMELINE_FIXTURE.tempo,
    startTick,
  });

  assert.equal(Tone.Transport.position, expectedSeconds);
  assert.ok(result.scheduledCount >= 0);
  const earlyItems = schedule.items.filter((item) => item.time < expectedSeconds && item.kind !== 'controller');
  const scheduledWhens = Tone.Transport._scheduled.filter((item) => !item.once).map((item) => item.when);
  for (const item of earlyItems) {
    assert.equal(scheduledWhens.includes(item.time), false);
  }
  engine.dispose();
});

test('loop wraps at exact endTick and reschedules from startTick', async () => {
  const Tone = createFakeTone();
  const engine = createPlaybackEngine({ Tone, logger: silentLogger() });
  const composition = {
    schema_version: 'composition.v2',
    tempo: 120,
    key: 'C major',
    ticks_per_quarter: 480,
    time_signature: '4/4',
    bar_count: 1,
    duration_ticks: 1920,
    tracks: [
      {
        id: 't1',
        instrument: 'piano',
        volume: 100,
        events: [
          { type: 'note', id: 'n1', pitch: 'C4', start_tick: 0, duration_ticks: 240, velocity: 80 },
          { type: 'note', id: 'n2', pitch: 'D4', start_tick: 480, duration_ticks: 240, velocity: 80 },
          { type: 'note', id: 'n3', pitch: 'E4', start_tick: 960, duration_ticks: 240, velocity: 80 },
        ],
      },
    ],
  };
  const schedule = compilePlaybackSchedule(composition);
  const loopStart = 0;
  const loopEnd = 960;
  const loopEndSeconds = ticksToPlaybackSeconds(loopEnd, { composition });

  await engine.prepare({
    loadInstruments: false,
    tracks: composition.tracks,
    schedule,
    composition,
    tempo: 120,
    startTick: loopStart,
    loop: { startTick: loopStart, endTick: loopEnd, enabled: true },
  });

  assert.equal(engine.getEndPositionSeconds(), loopEndSeconds);
  assert.ok(!Tone.Transport._scheduled.some((item) => !item.once && item.when >= loopEndSeconds));

  const endCallback = Tone.Transport._scheduled.find((item) => item.once);
  assert.ok(endCallback);
  const beforeCancelCount = Tone.Transport._cancelled.length;
  endCallback.callback();
  assert.ok(Tone.Transport._cancelled.length > beforeCancelCount);
  assert.equal(Tone.Transport.position, ticksToPlaybackSeconds(loopStart, { composition }));
  assert.equal(engine.getEndPositionSeconds(), loopEndSeconds);
  assert.ok(engine.getScheduledEventCount() > 0);

  engine.setLoop({ startTick: loopStart, endTick: loopEnd, enabled: false });
  assert.equal(engine.getLoop()?.enabled, false);
  assert.ok(engine.getEndPositionSeconds() >= loopEndSeconds);

  engine.dispose();
});

test('setLoop while paused rebuilds window without clearing bounds', async () => {
  const Tone = createFakeTone();
  const engine = createPlaybackEngine({ Tone, logger: silentLogger() });
  const schedule = compilePlaybackSchedule(EXPRESSIVE_FIXTURE);
  await engine.prepare({
    loadInstruments: false,
    tracks: EXPRESSIVE_FIXTURE.tracks,
    schedule,
    composition: EXPRESSIVE_FIXTURE,
    tempo: EXPRESSIVE_FIXTURE.tempo,
    loop: { startTick: 0, endTick: 1920, enabled: false },
  });
  engine.pause();
  const result = engine.setLoop({ startTick: 480, endTick: 1920, enabled: true });
  assert.ok(result);
  assert.equal(engine.getLoop()?.startTick, 480);
  assert.equal(engine.getLoop()?.enabled, true);
  engine.dispose();
});

test('normalizePlaybackLoop clamps and rejects invalid ranges', () => {
  assert.equal(normalizePlaybackLoop(null), null);
  assert.equal(normalizePlaybackLoop({ startTick: 10, endTick: 10, enabled: true }), null);
  assert.deepEqual(
    normalizePlaybackLoop({ startTick: 0, endTick: 5000, enabled: true }, { duration_ticks: 1000 }),
    { startTick: 0, endTick: 1000, enabled: true },
  );
  assert.equal(
    reconcilePlaybackLoop({ startTick: 900, endTick: 950, enabled: true }, { duration_ticks: 800 }),
    null,
  );
});

test('clampSeekSecondsToLoop relocates outside enabled window to loop start', () => {
  const loop = { startSeconds: 1, endSeconds: 3, enabled: true };
  assert.equal(clampSeekSecondsToLoop(0.5, loop), 1);
  assert.equal(clampSeekSecondsToLoop(3, loop), 1);
  assert.equal(clampSeekSecondsToLoop(2, loop), 2);
  assert.equal(clampSeekSecondsToLoop(2, { ...loop, enabled: false }), 2);
});

test('seek outside enabled loop relocates to loop start and reconstructs held notes', async () => {
  const Tone = createFakeTone();
  const engine = createPlaybackEngine({ Tone, logger: silentLogger() });
  const composition = {
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
        { pitch: 'E4', start_tick: 480, duration_ticks: 480, velocity: 80 },
      ],
    }],
    harmony: [],
  };
  const schedule = compilePlaybackSchedule(composition);
  await engine.prepare({
    loadInstruments: false,
    tracks: composition.tracks,
    schedule,
    composition,
    tempo: 120,
    loop: { startTick: 0, endTick: 960, enabled: true },
    sourceKey: 'working',
  });
  const loopEndSeconds = ticksToPlaybackSeconds(960, { composition });
  const position = engine.seek(loopEndSeconds + 0.5);
  assert.equal(position, 0);
  assert.ok(engine.getSessionId() >= 1);
  assert.equal(engine.getSourceKey(), 'working');
  engine.dispose();
});

test('deriveLoopRangeFromSelection prefers notes then bars', () => {
  const fromNotes = deriveLoopRangeFromSelection({
    composition: { duration_ticks: 4000 },
    noteRange: { startTick: 100, endTick: 500 },
    startBar: 1,
    endBar: 2,
  });
  assert.equal(fromNotes.source, 'notes');
  assert.equal(fromNotes.startTick, 100);

  const fromBars = deriveLoopRangeFromSelection({
    composition: { duration_ticks: 4000 },
    noteRange: null,
    startBar: 2,
    endBar: 3,
    barRangeResolver: () => ({ startTick: 1920, endTick: 3840 }),
  });
  assert.equal(fromBars.source, 'bars');
  assert.equal(fromBars.startTick, 1920);

  assert.equal(deriveLoopRangeFromSelection({ composition: { duration_ticks: 100 } }), null);
});

test('shared reverb and meters wire; live mute zeros dry and send', async () => {
  const Tone = createFakeTone();
  const engine = createPlaybackEngine({ Tone, logger: silentLogger() });
  await engine.prepare({
    loadInstruments: false,
    tempo: 120,
    tracks: [
      { id: 'piano-1', instrument: 'piano', midi_program: 0, volume: 100, pan: 0 },
      { id: 'bass-1', instrument: 'electric_bass', midi_program: 33, volume: 90, pan: 20 },
    ],
    events: [{
      trackId: 'piano-1',
      pitch: 'C4',
      notes: ['C4'],
      position: 0,
      duration: 0.5,
      stopPosition: 0.5,
      velocity: 0.5,
      velocityMidi: 64,
    }],
    trackOverrides: {
      'piano-1': { reverbSend: 0.4, trimDb: 0 },
      'bass-1': { reverbSend: 0.2 },
    },
  });

  assert.equal(engine.hasSharedReverb(), true);
  assert.ok(engine.getTrackSendGain('piano-1') > 0.3);
  const activity = engine.getActivitySnapshot();
  assert.ok(Object.prototype.hasOwnProperty.call(activity.tracks, 'piano-1'));
  assert.equal(typeof activity.clipped, 'boolean');

  engine.applyTrackOverrides({
    'piano-1': { muted: true, reverbSend: 0.4 },
    'bass-1': { reverbSend: 0.2 },
  });
  assert.equal(engine.getTrackEffectiveGain('piano-1'), 0);
  assert.equal(engine.getTrackSendGain('piano-1'), 0);
  assert.ok(engine.getTrackEffectiveGain('bass-1') > 0);

  engine.stop();
  assert.equal(engine.hasSharedReverb(), false);
  engine.dispose();
});

test('mapPlaybackVelocity softens mid dynamics without rewriting extremes', async () => {
  const { mapPlaybackVelocity } = await import('./tonePlaybackEngine.js');
  assert.equal(mapPlaybackVelocity(0), 0);
  assert.equal(mapPlaybackVelocity(1), 1);
  assert.ok(mapPlaybackVelocity(0.5) < 0.5);
});
