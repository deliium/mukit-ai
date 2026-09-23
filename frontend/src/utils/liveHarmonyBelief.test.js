import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  JAM_HARMONY_CHANGED,
  JAM_HARMONY_HOLD,
  createIdleHarmonyBelief,
  readLiveJamSettings,
} from './liveJamContracts.js';
import {
  resetHarmonyBeliefCountersForTests,
  updateHarmonyBelief,
} from './liveHarmonyBelief.js';

function featuresWithHarmony(symbol, confidence) {
  return {
    probable_harmony: { symbol, root_pc: 0, quality: 'maj', confidence },
  };
}

describe('liveHarmonyBelief', () => {
  it('prefers parseable V2 span over inferred features', () => {
    resetHarmonyBeliefCountersForTests();
    const settings = readLiveJamSettings(() => undefined);
    const next = updateHarmonyBelief(
      createIdleHarmonyBelief({ symbol: 'C', confidence: 0.7 }),
      featuresWithHarmony('G', 0.99),
      { symbol: 'Am' },
      'medium',
      settings,
      { nowMs: 1000 },
    );
    assert.equal(next.symbol, 'Am');
    assert.ok(next.confidence >= 0.9);
    assert.equal(next.held, false);
    assert.equal(next.reason_code, JAM_HARMONY_CHANGED);
  });

  it('holds on ambiguous single-note (low conf) guesses', () => {
    resetHarmonyBeliefCountersForTests();
    const settings = readLiveJamSettings(() => undefined);
    let belief = updateHarmonyBelief(
      null,
      featuresWithHarmony('C', 0.8),
      null,
      'medium',
      settings,
      { nowMs: 0 },
    );
    assert.equal(belief.symbol, 'C');

    belief = updateHarmonyBelief(
      belief,
      featuresWithHarmony('G', 0.25),
      null,
      'medium',
      settings,
      { nowMs: 500 },
    );
    assert.equal(belief.symbol, 'C');
    assert.equal(belief.held, true);
    assert.equal(belief.reason_code, JAM_HARMONY_HOLD);
  });

  it('does not flip on passing tone until dwell + hysteresis met', () => {
    resetHarmonyBeliefCountersForTests();
    const settings = {
      ...readLiveJamSettings(() => undefined),
      harmonyConfidenceMin: 0.55,
      harmonyDwellMs: 400,
      harmonyDwellMsFloor: 150,
      harmonyHysteresis: 0.15,
    };
    let belief = updateHarmonyBelief(
      null,
      featuresWithHarmony('C', 0.8),
      null,
      'medium',
      settings,
      { nowMs: 0 },
    );
    assert.equal(belief.symbol, 'C');

    // High confidence alternate but dwell not elapsed (margin > hysteresis).
    belief = updateHarmonyBelief(
      belief,
      featuresWithHarmony('G', 0.98),
      null,
      'medium',
      settings,
      { nowMs: 100 },
    );
    assert.equal(belief.symbol, 'C');
    assert.equal(belief.held, true);
    assert.equal(belief.pending_symbol, 'G');

    // Still within dwell.
    belief = updateHarmonyBelief(
      belief,
      featuresWithHarmony('G', 0.98),
      null,
      'medium',
      settings,
      { nowMs: 300 },
    );
    assert.equal(belief.symbol, 'C');

    // After dwell → adopt.
    belief = updateHarmonyBelief(
      belief,
      featuresWithHarmony('G', 0.98),
      null,
      'medium',
      settings,
      { nowMs: 550 },
    );
    assert.equal(belief.symbol, 'G');
    assert.equal(belief.held, false);
    assert.equal(belief.reason_code, JAM_HARMONY_CHANGED);
  });

  it('rejects change when new conf lacks hysteresis margin', () => {
    resetHarmonyBeliefCountersForTests();
    const settings = {
      ...readLiveJamSettings(() => undefined),
      harmonyConfidenceMin: 0.5,
      harmonyDwellMs: 100,
      harmonyDwellMsFloor: 50,
      harmonyHysteresis: 0.2,
    };
    let belief = updateHarmonyBelief(
      null,
      featuresWithHarmony('C', 0.8),
      null,
      'medium',
      settings,
      { nowMs: 0 },
    );
    belief = updateHarmonyBelief(
      belief,
      featuresWithHarmony('F', 0.85),
      null,
      'medium',
      settings,
      { nowMs: 500 },
    );
    // 0.85 < 0.8 + 0.2 → hold
    assert.equal(belief.symbol, 'C');
    assert.equal(belief.held, true);
  });

  it('high responsiveness shortens dwell', () => {
    resetHarmonyBeliefCountersForTests();
    const settings = {
      ...readLiveJamSettings(() => undefined),
      harmonyConfidenceMin: 0.55,
      harmonyDwellMs: 400,
      harmonyDwellMsFloor: 150,
      harmonyHysteresis: 0.1,
    };
    let belief = updateHarmonyBelief(
      null,
      featuresWithHarmony('C', 0.8),
      null,
      'high',
      settings,
      { nowMs: 0 },
    );
    belief = updateHarmonyBelief(
      belief,
      featuresWithHarmony('Dm', 0.9),
      null,
      'high',
      settings,
      { nowMs: 50 },
    );
    assert.equal(belief.symbol, 'C');

    // high dwell ≈ 400 * 0.55 = 220
    belief = updateHarmonyBelief(
      belief,
      featuresWithHarmony('Dm', 0.9),
      null,
      'high',
      settings,
      { nowMs: 280 },
    );
    assert.equal(belief.symbol, 'Dm');
  });
});
