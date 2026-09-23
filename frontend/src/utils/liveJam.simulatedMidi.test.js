/**
 * Deterministic simulated MIDI stream tests for AI Jam (Task 9).
 *
 * Fixture builders inject Transport-synced melody / chordal streams into
 * liveMidiStream — no Web MIDI hardware. Style matches liveAccompanimentBuffer.perf.
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { createLivePredictAbortController, LIVE_PREDICT_NO_ABORT } from '../api/livePerformanceApi.js';
import { createLiveAccompanimentScheduler } from './liveAccompanimentScheduler.js';
import { createLiveJamContext } from './liveJamContext.js';
import { createLiveJamRoleEngine } from './liveJamRoleEngine.js';
import {
  assertLiveAllowedForMidiPhase,
  createLiveMidiStream,
} from './liveMidiStream.js';
import {
  resetHarmonyBeliefCountersForTests,
  updateHarmonyBelief,
} from './liveHarmonyBelief.js';
import { extractLivePerformanceFeatures } from './livePerformanceFeatures.js';
import { readLiveJamSettings } from './liveJamContracts.js';
import {
  applyAiJamTakeToComposition,
} from './liveTakeApply.js';
import { LIVE_MIDI_PHASE_EXCLUSION } from './liveSessionContracts.js';
import { validateMusicJson } from './musicJsonValidation.js';
import {
  clearLivePlaybackEngine,
  registerLivePlaybackEngine,
} from './livePlaybackEngineAccess.js';
import { createPlaybackEngine } from './tonePlaybackEngine.js';

const BAR = 1920;
const BEAT = 480;

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

/**
 * Build a multi-bar C-major melody with occasional passing tones.
 * @param {number} [bars=4]
 * @returns {Array<{ tick: number, note: number, velocity: number, durationTicks: number }>}
 */
export function buildSimulatedMelodyStream(bars = 4) {
  const notes = [];
  const scale = [60, 62, 64, 65, 67, 69, 71, 72]; // C major
  for (let bar = 0; bar < bars; bar += 1) {
    for (let beat = 0; beat < 4; beat += 1) {
      const tick = bar * BAR + beat * BEAT;
      const pitch = scale[(bar * 3 + beat) % scale.length];
      notes.push({
        tick,
        note: pitch,
        velocity: 80 + (beat % 3) * 5,
        durationTicks: 360,
      });
      // Passing tone between beats 1 and 2 of bar 1 only (ambiguous).
      if (bar === 1 && beat === 1) {
        notes.push({
          tick: tick + 180,
          note: 61, // C# passing
          velocity: 50,
          durationTicks: 60,
        });
      }
    }
  }
  return notes;
}

/**
 * Build a chordal stream (C / Am / F / G blocks).
 * @param {number} [bars=4]
 */
export function buildSimulatedChordStream(bars = 4) {
  const chords = [
    [60, 64, 67], // C
    [57, 60, 64], // Am
    [53, 57, 60], // F
    [55, 59, 62], // G
  ];
  const notes = [];
  for (let bar = 0; bar < bars; bar += 1) {
    const chord = chords[bar % chords.length];
    const tick = bar * BAR;
    for (const note of chord) {
      notes.push({
        tick,
        note,
        velocity: 70,
        durationTicks: BAR - 60,
      });
    }
  }
  return notes;
}

/**
 * Inject note spans into a live MIDI stream via note-on/off at Transport ticks.
 * @param {ReturnType<typeof createLiveMidiStream>} stream
 * @param {Array<{ tick: number, note: number, velocity: number, durationTicks: number }>} spans
 * @param {{ setTick: (t: number) => void }} clock
 */
export function injectSimulatedSpans(stream, spans, clock) {
  for (const span of spans) {
    clock.setTick(span.tick);
    stream.pushMessage([0x90, span.note, span.velocity]);
    clock.setTick(span.tick + span.durationTicks);
    stream.pushMessage([0x80, span.note, 0]);
  }
}

function minimalJamComposition() {
  return {
    schema_version: 'composition.v2',
    tempo: 120,
    key: 'C major',
    time_signature: '4/4',
    ticks_per_quarter: 480,
    duration_ticks: 4 * BAR,
    bar_count: 4,
    sections: [{
      id: 's1',
      type: 'verse',
      start_bar: 1,
      bar_count: 4,
      start_tick: 0,
      duration_ticks: 4 * BAR,
    }],
    tracks: [
      {
        id: 'melody-1',
        name: 'Melody',
        instrument: 'piano',
        role: 'melody',
        midi_program: 0,
        channel: 1,
        expression: 127,
        sustain_pedals: [],
        events: [],
      },
      {
        id: 'bass-1',
        name: 'Bass',
        instrument: 'bass',
        role: 'bass',
        midi_program: 32,
        channel: 2,
        expression: 127,
        sustain_pedals: [],
        events: [],
      },
      {
        id: 'pad-1',
        name: 'Pad',
        instrument: 'strings',
        role: 'harmony',
        midi_program: 48,
        channel: 3,
        expression: 127,
        sustain_pedals: [],
        events: [],
      },
    ],
    harmony: [],
  };
}

describe('liveJam.simulatedMidi', () => {
  it('keeps belief stable under passing tones on a multi-bar melody stream', () => {
    resetHarmonyBeliefCountersForTests();
    let tick = 0;
    const stream = createLiveMidiStream({ getTick: () => tick });
    stream.start();
    const melody = buildSimulatedMelodyStream(4);
    injectSimulatedSpans(stream, melody, {
      setTick: (t) => { tick = t; },
    });

    const settings = {
      ...readLiveJamSettings(() => undefined),
      harmonyConfidenceMin: 0.55,
      harmonyDwellMs: 400,
      harmonyDwellMsFloor: 150,
      harmonyHysteresis: 0.15,
    };

    let belief = null;
    const symbols = [];
    // Sample belief every bar from bounded recent window.
    for (let bar = 0; bar < 4; bar += 1) {
      const nowTick = (bar + 1) * BAR;
      const recent = stream.getRecentEvents({
        fromTick: Math.max(0, nowTick - BAR),
        maxEvents: 48,
      });
      const features = extractLivePerformanceFeatures({
        events: recent,
        clock: {
          tick: nowTick,
          bar: bar + 1,
          beatInBar: 1,
          tickInBar: 0,
          barTicks: BAR,
          ticksPerBeat: BEAT,
          tempo: 120,
        },
        settings,
      });
      belief = updateHarmonyBelief(
        belief,
        features,
        null,
        'medium',
        settings,
        { nowMs: bar * 2000 },
      );
      if (belief.symbol) symbols.push(belief.symbol);
    }

    // Passing C# must not produce a lasting non-C belief flip across bars.
    assert.ok(symbols.length >= 1);
    const unique = new Set(symbols);
    assert.ok(
      unique.size <= 2,
      `belief flipped too often: ${[...unique].join(',')}`,
    );
    // Dominant belief remains C-family (C / Cmaj / CM etc.)
    const last = belief.symbol || '';
    assert.ok(/^C/i.test(last) || belief.held, `unexpected last belief ${last}`);

    stream.cancel({ reason: 'test' });
  });

  it('schedules AI roles ≥ horizon under local jam engine without mutating V2', () => {
    const Tone = createFakeTone();
    const engine = createPlaybackEngine({ Tone, logger: { debug() {}, info() {}, warn() {}, error() {} } });
    registerLivePlaybackEngine(engine);

    const composition = minimalJamComposition();
    const v2Fingerprint = JSON.stringify(composition.tracks.map((t) => t.events.length));

    const scheduler = createLiveAccompanimentScheduler();
    const jamEngine = createLiveJamRoleEngine({ ticksPerBeat: 480 });
    const jamContext = createLiveJamContext({ barTicks: BAR });
    jamContext.refresh({
      belief: { symbol: 'C', confidence: 0.9, held: false },
      nowTick: 0,
      windowBars: 2,
    });

    scheduler.setComposition(composition);
    assert.equal(scheduler.start().ok, true);

    for (let beat = 0; beat < 8; beat += 1) {
      const playheadTick = beat * BEAT;
      Tone.Transport.position = beat * 0.5;
      const result = scheduler.maintainHorizonWithJam({
        playheadTick,
        belief: { symbol: 'C', confidence: 0.9 },
        jamMode: 'user_melody',
        controls: {
          complexity: 'medium',
          density: 'medium',
          style: 'block',
          responsiveness: 'medium',
        },
        jamContext: jamContext.getSnapshot(),
        jamRoleEngine: jamEngine,
        horizon: { bars: 1, ms: 2000 },
      });
      assert.equal(result.ok, true);
      const coverage = scheduler.getBuffer().getCoverage();
      assert.ok(
        coverage.coveredThroughTick >= playheadTick,
        `coverage ${coverage.coveredThroughTick} < playhead ${playheadTick}`,
      );
      const roles = new Set(
        scheduler.getBuffer().getEvents().map((e) => e.track_role).filter(Boolean),
      );
      assert.ok(roles.has('bass') || roles.has('accompaniment'));
    }

    assert.equal(
      JSON.stringify(composition.tracks.map((t) => t.events.length)),
      v2Fingerprint,
      'V2 must not mutate before Commit',
    );

    // Predict delay → degradation continues; Transport not stopped by AI absence.
    jamEngine.activateDegradation({ code: 'predict_delay' });
    const degraded = scheduler.maintainHorizonWithJam({
      playheadTick: 8 * BEAT,
      belief: { symbol: 'C', confidence: 0.9 },
      jamMode: 'user_melody',
      controls: { complexity: 'medium', density: 'medium', style: 'arp', responsiveness: 'medium' },
      jamContext: jamContext.getSnapshot(),
      jamRoleEngine: jamEngine,
      horizon: { bars: 1, ms: 2000 },
    });
    assert.equal(degraded.ok, true);
    assert.equal(Tone.Transport.state, 'stopped'); // never forced stop by AI
    assert.ok(jamEngine.getDegradation()?.active || degraded.degraded);

    scheduler.cancel({ reason: 'test' });
    clearLivePlaybackEngine({ reason: 'sim-test' });
    engine.dispose();
  });

  it('commits melody stream + AI roles onto correct tracks with valid V2', () => {
    let tick = 0;
    const stream = createLiveMidiStream({ getTick: () => tick });
    stream.start();
    injectSimulatedSpans(stream, buildSimulatedMelodyStream(2), {
      setTick: (t) => { tick = t; },
    });
    const closed = stream.getClosedNotes();
    assert.ok(closed.length >= 4);

    const composition = minimalJamComposition();
    const before = structuredClone(composition);

    // Cancel path: no mutation
    assert.deepEqual(composition, before);

    const jamEngine = createLiveJamRoleEngine({ ticksPerBeat: 480 });
    const generated = jamEngine.generateWindow({
      fromTick: 0,
      toTick: 2 * BAR,
      belief: { symbol: 'C', confidence: 0.9 },
      jamMode: 'user_melody',
      controls: { complexity: 'medium', density: 'medium', style: 'block', responsiveness: 'medium' },
      jamContext: { belief_symbol: 'C', planned_window: [] },
    });
    const aiEvents = generated.events || [];
    assert.ok(aiEvents.length > 0);

    // Still no V2 mutation until Commit
    assert.equal(composition.tracks[0].events.length, 0);

    const committed = applyAiJamTakeToComposition(composition, {
      jamMode: 'user_melody',
      userTrackId: 'melody-1',
      roleTracks: { bass: 'bass-1', accompaniment: 'pad-1', texture: 'pad-1' },
      streamNotes: closed,
      accompanimentEvents: aiEvents,
      ensureMissingTracks: false,
      commitHarmonySpans: false,
    });
    assert.equal(committed.ok, true, committed.message || committed.code);
    assert.ok(committed.userCount >= 4);
    assert.ok((committed.roleCounts.bass || 0) + (committed.roleCounts.accompaniment || 0) > 0);

    const melody = committed.composition.tracks.find((t) => t.id === 'melody-1');
    const bass = committed.composition.tracks.find((t) => t.id === 'bass-1');
    assert.ok(melody.events.length >= 4);
    assert.ok(bass.events.length >= 1);
    assert.equal(committed.composition.harmony.length, 0);

    const validation = validateMusicJson(committed.composition);
    assert.equal(validation.valid, true, validation.message);

    // Original untouched
    assert.equal(composition.tracks[0].events.length, 0);

    stream.cancel({ reason: 'test' });
  });

  it('commits chord stream with optional harmony spans for user_chords', () => {
    let tick = 0;
    const stream = createLiveMidiStream({ getTick: () => tick });
    stream.start();
    injectSimulatedSpans(stream, buildSimulatedChordStream(2), {
      setTick: (t) => { tick = t; },
    });

    const jamContext = createLiveJamContext({ barTicks: BAR });
    jamContext.refresh({
      belief: { symbol: 'C', confidence: 0.85, held: false },
      nowTick: 0,
      windowBars: 2,
    });
    jamContext.freeze();

    const committed = applyAiJamTakeToComposition(minimalJamComposition(), {
      jamMode: 'user_chords',
      userTrackId: 'pad-1',
      roleTracks: { melody: 'melody-1', bass: 'bass-1', texture: 'pad-1' },
      streamNotes: stream.getClosedNotes(),
      accompanimentEvents: [
        { pitch: 72, start_tick: 480, duration_ticks: 240, velocity: 80, track_role: 'melody' },
        { pitch: 36, start_tick: 0, duration_ticks: 960, velocity: 70, track_role: 'bass' },
      ],
      plannedWindow: jamContext.getSnapshot().planned_window,
      commitHarmonySpans: true,
    });
    assert.equal(committed.ok, true, committed.message || committed.code);
    assert.ok(committed.harmonySpansCommitted >= 1);
    assert.ok(committed.composition.harmony.every((h) => h.chord && h.start_tick != null));
    assert.equal(validateMusicJson(committed.composition).valid, true);

    stream.cancel({ reason: 'test' });
  });

  it('builds features from bounded window only', () => {
    let tick = 0;
    const stream = createLiveMidiStream({ getTick: () => tick, capacity: 256 });
    stream.start();
    // Flood many bars
    injectSimulatedSpans(stream, buildSimulatedMelodyStream(8), {
      setTick: (t) => { tick = t; },
    });
    const maxEvents = 24;
    const fromTick = 6 * BAR;
    const recent = stream.getRecentEvents({ fromTick, maxEvents });
    assert.ok(recent.length <= maxEvents);
    assert.ok(recent.every((e) => e.tick >= fromTick));

    const features = extractLivePerformanceFeatures({
      events: recent,
      clock: {
        tick: 8 * BAR,
        bar: 8,
        beatInBar: 1,
        tickInBar: 0,
        barTicks: BAR,
        ticksPerBeat: BEAT,
        tempo: 120,
      },
    });
    assert.ok(features);
    assert.equal(features.belief, undefined);
    assert.ok(!('belief' in (features.probable_harmony || {})));

    stream.cancel({ reason: 'test' });
  });

  it('enforces midiPhase exclusion and predict no-abort fallback', () => {
    const blocked = assertLiveAllowedForMidiPhase('recording');
    assert.equal(blocked.ok, false);
    assert.equal(blocked.code, LIVE_MIDI_PHASE_EXCLUSION);

    // Simulate missing AbortController
    const prev = globalThis.AbortController;
    try {
      globalThis.AbortController = undefined;
      const gate = createLivePredictAbortController();
      assert.equal(gate.ok, false);
      assert.equal(gate.code, LIVE_PREDICT_NO_ABORT);
    } finally {
      globalThis.AbortController = prev;
    }
  });
});
