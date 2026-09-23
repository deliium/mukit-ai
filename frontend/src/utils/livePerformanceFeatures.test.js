import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { createLiveLatencyTracker } from './liveLatency.js';
import {
  LIVE_PERFORMANCE_FEATURES_SCHEMA,
  assertNoBeliefInsideFeatures,
} from './liveJamContracts.js';
import {
  extractLivePerformanceFeatures,
  resetLivePerformanceFeaturesWarnCountForTests,
} from './livePerformanceFeatures.js';
import { MIDI_MESSAGE_KINDS } from './midiInputMessages.js';

function noteOn(tick, note) {
  return { kind: MIDI_MESSAGE_KINDS.NOTE_ON, tick, note, velocity: 90, channel: 0 };
}

function clockAt(tick, extras = {}) {
  return {
    tick,
    bar: Math.floor(tick / 1920) + 1,
    beatInBar: Math.floor((tick % 1920) / 480) + 1,
    tickInBar: tick % 1920,
    barTicks: 1920,
    ticksPerBeat: 480,
    tempo: 120,
    ...extras,
  };
}

describe('livePerformanceFeatures', () => {
  it('returns empty skeleton shape with schema and no belief', () => {
    resetLivePerformanceFeaturesWarnCountForTests();
    const features = extractLivePerformanceFeatures({
      events: [],
      clock: clockAt(0),
    });
    assert.equal(features.schema, LIVE_PERFORMANCE_FEATURES_SCHEMA);
    assert.equal(features.pitch_activity.note_on_rate, 0);
    assert.equal(features.probable_harmony.symbol, null);
    assert.equal(assertNoBeliefInsideFeatures(features).ok, true);
    assert.equal('belief' in features, false);
  });

  it('scores C major triad as probable C maj with register stats', () => {
    const events = [
      noteOn(0, 60),
      noteOn(10, 64),
      noteOn(20, 67),
      noteOn(30, 60),
      noteOn(40, 64),
      noteOn(50, 67),
    ];
    const features = extractLivePerformanceFeatures({
      events,
      clock: clockAt(480),
    });
    assert.equal(features.probable_harmony.root_pc, 0);
    assert.ok(
      features.probable_harmony.quality === 'maj'
      || features.probable_harmony.quality === 'maj7',
    );
    assert.ok(features.probable_harmony.confidence > 0.4);
    assert.equal(features.probable_key.tonic_pc, 0);
    assert.equal(features.probable_key.mode, 'major');
    assert.ok(features.pitch_activity.register_mean > 59);
    assert.ok(features.pitch_activity.note_on_rate > 0);
    assert.equal(features.beat.tick, 480);
    assert.equal(features.beat.beat_in_bar, 2);
  });

  it('marks phrase boundary on long IOI silence', () => {
    const events = [
      noteOn(0, 60),
      noteOn(100, 62),
      noteOn(2500, 64),
    ];
    const features = extractLivePerformanceFeatures({
      events,
      clock: clockAt(2600),
    });
    assert.equal(features.phrase.boundary_likely, true);
    assert.ok(features.phrase.confidence > 0);
  });

  it('marks analysis latency when tracker provided', () => {
    let t = 100;
    const tracker = createLiveLatencyTracker({
      now: () => t,
      summaryIntervalMs: 10_000,
    });
    extractLivePerformanceFeatures({
      events: [noteOn(0, 60), noteOn(10, 64), noteOn(20, 67)],
      clock: clockAt(100),
      latencyTracker: {
        markStart(key) {
          tracker.markStart(key);
          t += 5;
        },
        markEnd(key) {
          return tracker.markEnd(key);
        },
      },
    });
    const snap = tracker.snapshot();
    assert.ok(snap.analysis != null);
    assert.ok(snap.analysis >= 5);
  });

  it('keeps single-note harmony confidence low (ambiguous)', () => {
    const events = [noteOn(0, 60), noteOn(50, 60), noteOn(100, 72)];
    const features = extractLivePerformanceFeatures({
      events,
      clock: clockAt(200),
    });
    assert.ok(features.probable_harmony.confidence < 0.4);
  });
});
