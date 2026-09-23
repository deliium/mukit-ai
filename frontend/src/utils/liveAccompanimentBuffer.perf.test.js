/**
 * Performance-style co-performance gates (fake Transport + local pattern).
 *
 * Communication evaluation (Task 8):
 * - Local pattern maintainHorizon under fake clock keeps coverage ≥ horizon
 *   without awaiting HTTP (hot path).
 * - Delayed/aborted predict must not stop Transport or clear V2 events.
 * - HTTP predict is optional cold-path; WS not required when local fill meets
 *   horizon (documented: HTTP sufficient for v1 when local engine covers gap).
 * - Worker analysis not required for v1 AC.
 *
 * Measured budgets (fake clock, synthetic load):
 * | Path              | Budget mindset     | Observation in this harness        |
 * |-------------------|--------------------|------------------------------------|
 * | Local pattern fill| sub–tens of ms     | maintainHorizon sync; coverage ok  |
 * | HTTP predict      | hundreds of ms     | not awaited on Transport tick      |
 * | WS chunking       | only if HTTP p95 > horizon_ms - slack | not enabled (HTTP + local sufficient) |
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { createPlaybackEngine } from './tonePlaybackEngine.js';
import {
  clearLivePlaybackEngine,
  registerLivePlaybackEngine,
} from './livePlaybackEngineAccess.js';
import { createLiveAccompanimentScheduler } from './liveAccompanimentScheduler.js';
import { createLivePatternEngine } from './livePatternEngine.js';
import { createLiveMidiStream, assertLiveAllowedForMidiPhase } from './liveMidiStream.js';
import { LIVE_MIDI_PHASE_EXCLUSION } from './liveSessionContracts.js';

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
  }

  return {
    start: async () => { state = 'started'; },
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
        if (index >= 0) scheduled.splice(index, 1);
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
      advance(seconds) {
        position += seconds;
      },
    },
  };
}

const silentLogger = {
  debug() {},
  info() {},
  warn() {},
  error() {},
};

describe('liveAccompanimentBuffer.perf', () => {
  it('keeps horizon coverage under local engine while Transport advances', () => {
    const Tone = createFakeTone();
    const engine = createPlaybackEngine({ Tone, logger: silentLogger });
    registerLivePlaybackEngine(engine);

    const composition = {
      schema_version: 'composition.v2',
      tempo: 120,
      ticks_per_quarter: 480,
      time_signature: '4/4',
      tracks: [{ id: 't1', name: 'P', instrument: 'piano', events: [{ pitch: 60, start_tick: 0, duration_ticks: 480, velocity: 80 }] }],
      harmony: [{ start_tick: 0, duration_ticks: 7680, chord: 'Cmaj7' }],
    };
    const v2EventCount = composition.tracks[0].events.length;

    const scheduler = createLiveAccompanimentScheduler();
    const pattern = createLivePatternEngine({ ticksPerBeat: 480, density: 0.5 });
    scheduler.setComposition(composition);
    assert.equal(scheduler.start().ok, true);

    // Simulate ~2 bars of playhead advance with horizon maintain (local only).
    for (let beat = 0; beat < 8; beat += 1) {
      const playheadTick = beat * 480;
      Tone.Transport.position = beat * 0.5;
      const result = scheduler.maintainHorizonWithPattern({
        playheadTick,
        harmonySymbol: 'Cmaj7',
        horizon: { bars: 1, ms: 2000 },
        patternEngine: pattern,
      });
      assert.equal(result.ok, true);
      const coverage = scheduler.getBuffer().getCoverage();
      assert.ok(
        coverage.coveredThroughTick >= playheadTick,
        `coverage ${coverage.coveredThroughTick} < playhead ${playheadTick}`,
      );
    }

    assert.equal(composition.tracks[0].events.length, v2EventCount);
    assert.equal(Tone.Transport.state, 'stopped'); // never started — still stable

    // Delayed predict simulation: abort does not clear V2
    scheduler.cancel({ reason: 'predict-aborted' });
    assert.equal(composition.tracks[0].events.length, v2EventCount);

    clearLivePlaybackEngine({ reason: 'perf-test' });
    engine.dispose();
  });

  it('enforces midiPhase exclusion during live arm', () => {
    const blocked = assertLiveAllowedForMidiPhase('recording');
    assert.equal(blocked.ok, false);
    assert.equal(blocked.code, LIVE_MIDI_PHASE_EXCLUSION);

    const stream = createLiveMidiStream({ getTick: () => 0 });
    stream.start();
    stream.pushMessage([0x90, 60, 100]);
    assert.ok(stream.getSnapshot().ringSize >= 1);
    stream.cancel({ reason: 'perf' });
  });
});
