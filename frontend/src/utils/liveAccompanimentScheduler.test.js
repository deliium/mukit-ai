import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { createPlaybackEngine } from './tonePlaybackEngine.js';
import {
  clearLivePlaybackEngine,
  getLivePlaybackEngine,
  registerLivePlaybackEngine,
  requireLivePlaybackEngine,
} from './livePlaybackEngineAccess.js';
import { createLiveAccompanimentBuffer } from './liveAccompanimentBuffer.js';
import { createLiveAccompanimentScheduler } from './liveAccompanimentScheduler.js';
import { LIVE_ENGINE_UNAVAILABLE } from './liveSessionContracts.js';

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

describe('livePlaybackEngineAccess + accompaniment buffer/scheduler', () => {
  it('registers shared engine and fails closed when missing', () => {
    clearLivePlaybackEngine({ reason: 'test-reset', clearLive: false });
    assert.equal(requireLivePlaybackEngine().ok, false);
    assert.equal(requireLivePlaybackEngine().code, LIVE_ENGINE_UNAVAILABLE);

    const Tone = createFakeTone();
    const engine = createPlaybackEngine({ Tone, logger: silentLogger });
    registerLivePlaybackEngine(engine);
    assert.equal(getLivePlaybackEngine(), engine);
    assert.equal(requireLivePlaybackEngine().ok, true);

    clearLivePlaybackEngine({ reason: 'test-done' });
    assert.equal(getLivePlaybackEngine(), null);
    engine.dispose();
  });

  it('keeps live schedule IDs separate from V2 owned clears', () => {
    const Tone = createFakeTone();
    const engine = createPlaybackEngine({ Tone, logger: silentLogger });
    registerLivePlaybackEngine(engine);

    const liveId = engine.scheduleLiveAt(() => {}, 1.5);
    assert.ok(liveId != null);
    assert.equal(engine.getLiveScheduledEventCount(), 1);

    engine.clearLiveScheduledEvents({ afterSeconds: 1.0 });
    assert.equal(engine.getLiveScheduledEventCount(), 0);

    clearLivePlaybackEngine({ reason: 'test' });
    engine.dispose();
  });

  it('buffers chunks and schedules on shared engine without mutating V2', () => {
    const Tone = createFakeTone();
    const engine = createPlaybackEngine({ Tone, logger: silentLogger });
    registerLivePlaybackEngine(engine);

    const composition = {
      schema_version: 'composition.v2',
      tempo: 120,
      ticks_per_quarter: 480,
      time_signature: '4/4',
      tracks: [{ id: 't1', name: 'P', instrument: 'piano', events: [] }],
      harmony: [],
    };

    const scheduler = createLiveAccompanimentScheduler({
      onTrigger: () => {},
    });
    scheduler.setComposition(composition);
    assert.equal(scheduler.start().ok, true);

    const result = scheduler.ingestEvents(
      [{ pitch: 60, start_tick: 480, duration_ticks: 240, velocity: 90 }],
      { source: 'local_pattern', session_id: 's1' },
    );
    assert.equal(result.ok, true);
    assert.ok(result.scheduled >= 1);
    assert.equal(engine.getLiveScheduledEventCount(), 1);
    assert.equal(composition.tracks[0].events.length, 0);

    scheduler.cancel({ reason: 'test' });
    assert.equal(engine.getLiveScheduledEventCount(), 0);

    clearLivePlaybackEngine({ reason: 'test' });
    engine.dispose();
  });

  it('buffer tracks horizon coverage', () => {
    const buffer = createLiveAccompanimentBuffer({ maxEvents: 32 });
    buffer.insertEvents([
      { pitch: 60, start_tick: 0, duration_ticks: 480, velocity: 80 },
      { pitch: 64, start_tick: 480, duration_ticks: 480, velocity: 80 },
    ]);
    assert.equal(buffer.getCoveredThroughTick(), 960);
    const gap = buffer.uncoveredHorizon(0, 1920);
    assert.equal(gap.needsFill, true);
    assert.equal(gap.gapStart, 960);
  });
});
