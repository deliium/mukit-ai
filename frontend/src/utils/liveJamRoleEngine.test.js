import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  createLiveJamRoleEngine,
  JAM_ROLE_OCTAVE_BASE,
} from './liveJamRoleEngine.js';
import { createLiveAccompanimentScheduler } from './liveAccompanimentScheduler.js';
import {
  clearLivePlaybackEngine,
  registerLivePlaybackEngine,
} from './livePlaybackEngineAccess.js';
import { createPlaybackEngine } from './tonePlaybackEngine.js';
import { LIVE_DEGRADED_PATTERN_CONTINUE } from './liveSessionContracts.js';

function createFakeTone() {
  const scheduled = [];
  let nextId = 1;
  let position = 0;
  let state = 'stopped';

  class FakeParam {
    constructor(value = 0) {
      this.value = value;
    }

    linearRampTo(value) {
      this.value = value;
    }
  }

  class FakeGain {
    constructor(value = 1) {
      this.gain = new FakeParam(value);
      this.connections = [];
    }

    connect(node) {
      this.connections.push(node);
      return node;
    }

    toDestination() {
      return this;
    }

    dispose() {}
  }

  class FakeSynth extends FakeGain {
    triggerAttack() {}

    triggerRelease() {}

    triggerAttackRelease() {}
  }

  return {
    start: async () => {
      state = 'started';
    },
    now: () => 0,
    Gain: FakeGain,
    Panner: class extends FakeGain {
      constructor() {
        super();
        this.pan = new FakeParam(0);
      }
    },
    Synth: FakeSynth,
    PolySynth: FakeSynth,
    MonoSynth: FakeSynth,
    MembraneSynth: FakeSynth,
    Reverb: class extends FakeGain {
      async generate() {}
    },
    Limiter: FakeGain,
    Meter: class extends FakeGain {
      getValue() {
        return 0;
      }
    },
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
        scheduled.push({ id, callback, when });
        return id;
      },
      scheduleOnce(callback, when) {
        return this.schedule(callback, when);
      },
      cancel(eventId) {
        const index = scheduled.findIndex((item) => item.id === eventId);
        if (index >= 0) {
          scheduled.splice(index, 1);
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
    },
  };
}

const silentLogger = {
  debug() {},
  info() {},
  warn() {},
  error() {},
};

describe('liveJamRoleEngine', () => {
  it('emits AI roles only for user_melody with belief symbol', () => {
    const engine = createLiveJamRoleEngine({ ticksPerBeat: 480 });
    const result = engine.generateWindow({
      fromTick: 0,
      toTick: 1920,
      belief: { symbol: 'Cmaj7', confidence: 0.9, held: false },
      jamMode: 'user_melody',
      controls: {
        complexity: 'low',
        density: 'medium',
        style: 'block',
        responsiveness: 'medium',
      },
    });
    assert.ok(result.events.length > 0);
    const roles = new Set(result.events.map((e) => e.track_role));
    assert.ok(roles.has('bass'));
    assert.ok(roles.has('accompaniment'));
    assert.equal(roles.has('melody'), false);
    assert.equal(roles.has('texture'), false);
    for (const ev of result.events) {
      if (ev.track_role === 'bass') {
        assert.ok(ev.pitch >= JAM_ROLE_OCTAVE_BASE.bass);
        assert.ok(ev.pitch < JAM_ROLE_OCTAVE_BASE.bass + 24);
      }
    }
  });

  it('includes texture when complexity ≥ medium for user_melody', () => {
    const engine = createLiveJamRoleEngine({ ticksPerBeat: 480 });
    const result = engine.generateWindow({
      fromTick: 0,
      toTick: 1920,
      belief: { symbol: 'Am', confidence: 0.8 },
      jamMode: 'user_melody',
      controls: { complexity: 'medium', density: 'high', style: 'arp' },
    });
    const roles = new Set(result.events.map((e) => e.track_role));
    assert.ok(roles.has('texture'));
  });

  it('emits melody/bass/texture for user_chords', () => {
    const engine = createLiveJamRoleEngine({ ticksPerBeat: 480 });
    const result = engine.generateWindow({
      fromTick: 0,
      toTick: 1920,
      belief: { symbol: 'G7', confidence: 0.75 },
      jamMode: 'user_chords',
      controls: { complexity: 'high', density: 'medium', style: 'alberti' },
    });
    const roles = new Set(result.events.map((e) => e.track_role));
    assert.ok(roles.has('melody'));
    assert.ok(roles.has('bass'));
    assert.ok(roles.has('texture'));
    assert.equal(roles.has('accompaniment'), false);
  });

  it('respects roleMask subset', () => {
    const engine = createLiveJamRoleEngine({ ticksPerBeat: 480 });
    const result = engine.generateWindow({
      fromTick: 0,
      toTick: 960,
      belief: { symbol: 'Dm', confidence: 0.7 },
      jamMode: 'user_melody',
      controls: { complexity: 'high', density: 'medium', style: 'pad' },
      roleMask: ['bass'],
    });
    assert.ok(result.events.length > 0);
    assert.ok(result.events.every((e) => e.track_role === 'bass'));
  });

  it('activates LIVE_DEGRADED_PATTERN_CONTINUE when degraded', () => {
    const engine = createLiveJamRoleEngine({ ticksPerBeat: 480 });
    const result = engine.generateWindow({
      fromTick: 0,
      toTick: 960,
      belief: { symbol: 'F', confidence: 0.6 },
      jamMode: 'user_melody',
      controls: { complexity: 'medium', density: 'low', style: 'block' },
      degraded: true,
    });
    assert.equal(result.degradation.active, true);
    assert.equal(result.degradation.code, LIVE_DEGRADED_PATTERN_CONTINUE);
    assert.ok(result.events.length > 0);
  });

  it('holds last voicing when belief symbol unparseable', () => {
    const engine = createLiveJamRoleEngine({ ticksPerBeat: 480 });
    const first = engine.generateWindow({
      fromTick: 0,
      toTick: 480,
      belief: { symbol: 'C', confidence: 0.9 },
      jamMode: 'user_melody',
      controls: { complexity: 'low', density: 'medium', style: 'block' },
    });
    assert.ok(first.events.length > 0);
    const bassPitch = first.events.find((e) => e.track_role === 'bass')?.pitch;
    const held = engine.generateWindow({
      fromTick: 480,
      toTick: 960,
      belief: { symbol: '???', confidence: 0.1 },
      jamMode: 'user_melody',
      controls: { complexity: 'low', density: 'medium', style: 'block' },
    });
    assert.ok(held.events.length > 0);
    const heldBass = held.events.find((e) => e.track_role === 'bass');
    assert.ok(heldBass);
    assert.equal(heldBass.pitch, bassPitch);
    assert.equal(engine.getLastKind('bass'), 'hold');
  });
});

describe('maintainHorizonWithJam', () => {
  it('fills uncovered horizon with multi-role jam events', () => {
    clearLivePlaybackEngine({ reason: 'test-reset', clearLive: false });
    const Tone = createFakeTone();
    const playback = createPlaybackEngine({ Tone, logger: silentLogger });
    registerLivePlaybackEngine(playback);

    const composition = {
      schema_version: 'composition.v2',
      tempo: 120,
      ticks_per_quarter: 480,
      time_signature: '4/4',
      tracks: [{ id: 't1', name: 'P', instrument: 'piano', events: [] }],
      harmony: [],
    };

    const scheduler = createLiveAccompanimentScheduler({ onTrigger: () => {} });
    scheduler.setComposition(composition);
    assert.equal(scheduler.start().ok, true);

    const jamEngine = createLiveJamRoleEngine({ ticksPerBeat: 480 });
    const result = scheduler.maintainHorizonWithJam({
      playheadTick: 0,
      belief: { symbol: 'Cmaj7', confidence: 0.9 },
      jamMode: 'user_melody',
      controls: { complexity: 'medium', density: 'medium', style: 'arp' },
      jamRoleEngine: jamEngine,
      horizon: { bars: 1, ms: 2000 },
    });
    assert.equal(result.ok, true);
    assert.ok(result.filled > 0);
    const buf = scheduler.getBuffer();
    assert.ok(buf.getCoveredThroughTick() > 0);
    const coverage = buf.getCoverage();
    assert.ok(coverage.eventCount > 0);

    // Role tags on generated window
    const gen = jamEngine.generateWindow({
      fromTick: 0,
      toTick: 960,
      belief: { symbol: 'Cmaj7' },
      jamMode: 'user_melody',
      controls: { complexity: 'medium', density: 'medium', style: 'arp' },
    });
    const roles = new Set(gen.events.map((e) => e.track_role));
    assert.ok(roles.has('bass'));
    assert.ok(roles.has('accompaniment'));

    // Second maintain with playhead past coverage should not need a large fill
    const covered = buf.getCoveredThroughTick();
    const second = scheduler.maintainHorizonWithJam({
      playheadTick: Math.max(0, covered - 1),
      belief: { symbol: 'Cmaj7', confidence: 0.9 },
      jamMode: 'user_melody',
      controls: { complexity: 'medium', density: 'medium', style: 'arp' },
      jamRoleEngine: jamEngine,
      horizon: { bars: 1, ms: 2000 },
    });
    assert.equal(second.ok, true);

    scheduler.cancel({ reason: 'test' });
    clearLivePlaybackEngine({ reason: 'test-done' });
    playback.dispose();
  });
});
