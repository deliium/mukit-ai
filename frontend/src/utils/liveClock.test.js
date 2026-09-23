import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { getLiveClock, resetLiveClockLogThrottleForTests } from './liveClock.js';
import {
  activeHarmonyAtTick,
  resetLiveHarmonyEmptyWarnCountForTests,
} from './liveHarmonyContext.js';
import { LIVE_HARMONY_EMPTY } from './liveSessionContracts.js';

function miniComposition(overrides = {}) {
  return {
    schema_version: 'composition.v2',
    tempo: 120,
    ticks_per_quarter: 480,
    time_signature: '4/4',
    title: 'live-clock-test',
    tracks: [
      {
        id: 't1',
        name: 'Piano',
        instrument: 'piano',
        events: [{ pitch: 60, start_tick: 0, duration_ticks: 1920, velocity: 80 }],
      },
    ],
    harmony: [
      { start_tick: 0, duration_ticks: 1920, chord: 'Cmaj7' },
      { start_tick: 1920, duration_ticks: 1920, chord: 'Dm7' },
    ],
    ...overrides,
  };
}

describe('liveClock', () => {
  it('derives tick/bar/beat from transport seconds', () => {
    resetLiveClockLogThrottleForTests();
    const composition = miniComposition();
    // 120 bpm, 4/4, tpq 480 → 0.5s per beat, 2s per bar
    const atBeat1 = getLiveClock(0, composition);
    assert.equal(atBeat1.bar, 1);
    assert.equal(atBeat1.beatInBar, 1);
    assert.equal(atBeat1.tick, 0);

    const atBeat3 = getLiveClock(1.0, composition);
    assert.equal(atBeat3.bar, 1);
    assert.equal(atBeat3.beatInBar, 3);

    const atBar2 = getLiveClock(2.0, composition);
    assert.equal(atBar2.bar, 2);
    assert.equal(atBar2.beatInBar, 1);
  });
});

describe('liveHarmonyContext', () => {
  it('resolves active harmony at tick boundaries', () => {
    resetLiveHarmonyEmptyWarnCountForTests();
    const composition = miniComposition();
    const first = activeHarmonyAtTick(composition, 0);
    assert.equal(first.symbol, 'Cmaj7');
    assert.equal(first.start_tick, 0);

    const second = activeHarmonyAtTick(composition, 1920);
    assert.equal(second.symbol, 'Dm7');

    const past = activeHarmonyAtTick(composition, 10_000);
    assert.equal(past.symbol, null);
    assert.equal(past.warning, LIVE_HARMONY_EMPTY);
  });

  it('returns empty warning when harmony missing', () => {
    resetLiveHarmonyEmptyWarnCountForTests();
    const result = activeHarmonyAtTick(miniComposition({ harmony: [] }), 0);
    assert.equal(result.symbol, null);
    assert.equal(result.warning, LIVE_HARMONY_EMPTY);
  });
});
