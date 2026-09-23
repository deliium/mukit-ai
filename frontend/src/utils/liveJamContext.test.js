import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { createLiveJamContext } from './liveJamContext.js';

describe('liveJamContext', () => {
  it('builds inferred planned_window from belief when no V2 spans', () => {
    const ctx = createLiveJamContext({ barTicks: 1920, defaultWindowBars: 2 });
    const snap = ctx.refresh({
      belief: { symbol: 'C', confidence: 0.8, held: false },
      features: {
        probable_key: { tonic_pc: 0, mode: 'major', confidence: 0.7 },
        phrase: { boundary_likely: false },
      },
      nowTick: 0,
      windowBars: 2,
    });
    assert.equal(snap.belief_symbol, 'C');
    assert.equal(snap.planned_window.length, 2);
    assert.ok(snap.planned_window.every((e) => e.source === 'inferred'));
    assert.equal(snap.planned_window[0].symbol, 'C');
    assert.equal(snap.key_belief.tonic_pc, 0);
    assert.equal(ctx.getSnapshot(), snap);
  });

  it('tags V2 spans as source v2 and fills gaps with held/inferred', () => {
    const ctx = createLiveJamContext({ barTicks: 1920 });
    const snap = ctx.refresh({
      belief: { symbol: 'G', confidence: 0.6, held: true },
      features: {
        probable_key: { tonic_pc: 7, mode: 'major', confidence: 0.5 },
        phrase: { boundary_likely: true },
      },
      v2HarmonySpans: [
        { start_tick: 960, duration_ticks: 960, chord: 'Am' },
      ],
      nowTick: 0,
      windowBars: 2,
    });
    assert.ok(snap.planned_window.some((e) => e.source === 'v2' && e.symbol === 'Am'));
    assert.ok(snap.planned_window.some((e) => e.source === 'held' && e.symbol === 'G'));
    assert.ok(snap.phrase_boundary_ticks.includes(0));
  });

  it('clear resets; freeze blocks refresh', () => {
    const ctx = createLiveJamContext({ barTicks: 1920 });
    ctx.refresh({
      belief: { symbol: 'Dm', confidence: 0.7 },
      nowTick: 480,
      windowBars: 1,
    });
    assert.equal(ctx.getSnapshot().belief_symbol, 'Dm');

    ctx.freeze();
    assert.equal(ctx.isFrozen(), true);
    assert.equal(ctx.getSnapshot().frozen, true);

    ctx.refresh({
      belief: { symbol: 'E', confidence: 0.9 },
      nowTick: 2000,
      windowBars: 1,
    });
    assert.equal(ctx.getSnapshot().belief_symbol, 'Dm');

    ctx.clear({ reason: 'cancel' });
    assert.equal(ctx.isFrozen(), false);
    assert.equal(ctx.getSnapshot().belief_symbol, null);
    assert.equal(ctx.getSnapshot().planned_window.length, 0);
  });

  it('getSnapshot is stable O(1) reference until refresh', () => {
    const ctx = createLiveJamContext();
    const a = ctx.getSnapshot();
    const b = ctx.getSnapshot();
    assert.equal(a, b);
    ctx.refresh({ belief: { symbol: 'F', confidence: 0.5 }, nowTick: 0, windowBars: 1 });
    const c = ctx.getSnapshot();
    assert.notEqual(a, c);
    assert.equal(ctx.getSnapshot(), c);
  });
});
