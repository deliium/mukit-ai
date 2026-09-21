import assert from 'node:assert/strict';
import test from 'node:test';
import {
  createMidiPerformanceCapture,
  ticksFromElapsedMs,
} from './midiPerformanceCapture.js';

const composition = { tempo: 120, ticks_per_quarter: 480 };

test('ticksFromElapsedMs maps wall time at root tempo', () => {
  // 120 BPM, 480 tpq → 960 ticks/sec → 0.96 ticks/ms
  assert.equal(ticksFromElapsedMs(composition, 0, 1000), 960);
  assert.equal(ticksFromElapsedMs(composition, 100, 500), 100 + 480);
});

test('capture opens and closes notes with raw timing', () => {
  let now = 1000;
  const capture = createMidiPerformanceCapture({
    now: () => now,
    composition,
    originTick: 480,
  });
  capture.start({ atMs: now });
  capture.injectMessage([0x90, 60, 100], { atMs: now });
  now += 500;
  capture.injectMessage([0x80, 60, 0], { atMs: now });
  const summary = capture.stop({ atMs: now });
  assert.equal(summary.noteCount, 1);
  assert.equal(summary.notes[0].pitch, 'C4');
  assert.equal(summary.notes[0].start_tick, 480);
  assert.equal(summary.notes[0].duration_ticks, 480);
  assert.equal(summary.notes[0].velocity, 100);
  assert.equal(summary.pedalCount, 0);
});

test('capture records CC64 sustain spans', () => {
  let now = 0;
  const capture = createMidiPerformanceCapture({
    now: () => now,
    composition,
    originTick: 0,
  });
  capture.start({ atMs: 0 });
  capture.injectMessage([0xb0, 64, 127], { atMs: 0 });
  now = 1000;
  capture.injectMessage([0xb0, 64, 0], { atMs: now });
  const summary = capture.stop({ atMs: now });
  assert.equal(summary.pedalCount, 1);
  assert.equal(summary.sustainPedals[0].start_tick, 0);
  assert.equal(summary.sustainPedals[0].duration_ticks, 960);
});

test('stop force-closes hanging notes', () => {
  const capture = createMidiPerformanceCapture({
    now: () => 0,
    composition,
    originTick: 0,
  });
  capture.start({ atMs: 0 });
  capture.injectMessage([0x90, 64, 80], { atMs: 0 });
  const summary = capture.stop({ atMs: 250 });
  assert.equal(summary.noteCount, 1);
  assert.ok(summary.notes[0].duration_ticks >= 1);
  assert.equal(summary.openNoteCount, 0);
});

test('discard clears buffer without notes', () => {
  const capture = createMidiPerformanceCapture({ composition, originTick: 0 });
  capture.start();
  capture.injectMessage([0x90, 60, 90]);
  capture.discard();
  assert.equal(capture.isCapturing(), false);
  assert.equal(capture.getSnapshot().noteCount, 0);
});
