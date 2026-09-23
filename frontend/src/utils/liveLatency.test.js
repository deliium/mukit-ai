import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { createLiveLatencyTracker } from './liveLatency.js';

describe('liveLatency', () => {
  it('measures mark pairs with injected clock', () => {
    let t = 1000;
    const tracker = createLiveLatencyTracker({
      now: () => t,
      summaryIntervalMs: 10_000,
    });
    tracker.markStart('midi_input');
    t = 1004.5;
    const elapsed = tracker.markEnd('midi_input');
    assert.equal(elapsed, 4.5);
    assert.equal(tracker.snapshot().midi_input, 4.5);

    tracker.record('scheduling', 2);
    assert.equal(tracker.snapshot().scheduling, 2);
    assert.ok(tracker.snapshot().ewma.scheduling > 0);
  });

  it('ignores unknown keys and unmatched ends', () => {
    const tracker = createLiveLatencyTracker({ now: () => 0 });
    assert.equal(tracker.markEnd('scheduling'), null);
    tracker.markStart('not_a_key');
    assert.equal(tracker.snapshot().sampleCount, 0);
  });
});
