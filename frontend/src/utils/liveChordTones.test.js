import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  chordTonePitchClasses,
  parseLiveChordSymbol,
  pitchClassesToMidi,
  resetLiveChordUnparseableCountForTests,
} from './liveChordTones.js';
import { createLivePatternEngine } from './livePatternEngine.js';
import { LIVE_DEGRADED_PATTERN_CONTINUE } from './liveSessionContracts.js';

describe('liveChordTones', () => {
  it('parses common triad and seventh symbols', () => {
    resetLiveChordUnparseableCountForTests();
    assert.deepEqual(chordTonePitchClasses('C'), [0, 4, 7]);
    assert.deepEqual(chordTonePitchClasses('Am'), [0, 4, 9]);
    assert.deepEqual(chordTonePitchClasses('G7'), [2, 5, 7, 11]);
    assert.deepEqual(chordTonePitchClasses('Dm7'), [0, 2, 5, 9]);
    assert.equal(parseLiveChordSymbol('Cmaj7').parseable, true);
    assert.equal(parseLiveChordSymbol('???').parseable, false);
    assert.equal(chordTonePitchClasses('nope').length, 0);
  });

  it('maps pitch classes to midi', () => {
    assert.deepEqual(pitchClassesToMidi([0, 4, 7], { octaveBase: 60 }), [60, 64, 67]);
  });
});

describe('livePatternEngine', () => {
  it('generates deterministic events from active harmony', () => {
    const engine = createLivePatternEngine({ ticksPerBeat: 480, density: 0.4 });
    const first = engine.generateWindow({
      fromTick: 0,
      toTick: 1920,
      harmonySymbol: 'Cmaj7',
    });
    assert.ok(first.events.length > 0);
    assert.ok(first.events.every((ev) => ev.pitch >= 0 && ev.pitch <= 127));
    assert.equal(first.degradation.active, false);

    const second = engine.generateWindow({
      fromTick: 0,
      toTick: 1920,
      harmonySymbol: 'Cmaj7',
    });
    assert.deepEqual(
      first.events.map((e) => [e.pitch, e.start_tick]),
      second.events.map((e) => [e.pitch, e.start_tick]),
    );
  });

  it('degrades with pattern continue without empty silence when voicing known', () => {
    const engine = createLivePatternEngine({ ticksPerBeat: 480, density: 0.6 });
    engine.generateWindow({
      fromTick: 0,
      toTick: 960,
      harmonySymbol: 'Am7',
    });
    const degraded = engine.generateWindow({
      fromTick: 960,
      toTick: 1920,
      harmonySymbol: 'Am7',
      degraded: true,
    });
    assert.equal(degraded.degradation.active, true);
    assert.equal(degraded.degradation.code, LIVE_DEGRADED_PATTERN_CONTINUE);
    assert.ok(degraded.events.length > 0);

    // Empty harmony → hold last voicing
    const held = engine.generateWindow({
      fromTick: 1920,
      toTick: 2880,
      harmonySymbol: null,
      degraded: true,
    });
    assert.ok(held.events.length > 0);
    assert.equal(held.patternKind, 'hold');
  });
});
