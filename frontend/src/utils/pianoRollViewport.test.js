import assert from 'node:assert/strict';
import test from 'node:test';

import {
  DEFAULT_PITCH_BUFFER_ROWS,
  DEFAULT_TICK_BUFFER,
  anchorZoom,
  clampScrollLeft,
  clampScrollTop,
  computeVisiblePitchRange,
  computeVisibleTickRange,
  filterNotesInViewport,
  noteIntersectsViewport,
} from './pianoRollViewport.js';

test('computeVisibleTickRange applies buffer and clamps to duration', () => {
  const range = computeVisibleTickRange({
    scrollLeft: 100,
    clientWidth: 200,
    pixelsPerTick: 0.05,
    durationTicks: 10000,
    bufferTicks: 100,
  });
  // visible ticks without buffer: 100/0.05=2000 .. (100+200)/0.05=6000
  assert.equal(range.startTick, 1900);
  assert.equal(range.endTick, 6100);
  assert.equal(range.bufferTicks, 100);
});

test('computeVisibleTickRange uses default tick buffer when omitted', () => {
  const range = computeVisibleTickRange({
    scrollLeft: 0,
    clientWidth: 100,
    pixelsPerTick: 0.05,
    durationTicks: 20000,
  });
  assert.equal(range.bufferTicks, DEFAULT_TICK_BUFFER);
  assert.equal(range.startTick, 0);
  assert.equal(range.endTick, Math.ceil(100 / 0.05) + DEFAULT_TICK_BUFFER);
});

test('computeVisibleTickRange clamps start at 0 and end at duration', () => {
  const range = computeVisibleTickRange({
    scrollLeft: 0,
    clientWidth: 50,
    pixelsPerTick: 0.05,
    durationTicks: 500,
    bufferTicks: 1000,
  });
  assert.equal(range.startTick, 0);
  assert.equal(range.endTick, 500);
});

test('computeVisiblePitchRange buffers rows around viewport', () => {
  // maxMidi=96 at top; rowHeight=10; scrollTop=20 → row 2 → midi 94
  // clientHeight=30 → covers ~3 rows → bottom row ~4 → midi 92 before buffer
  const range = computeVisiblePitchRange({
    scrollTop: 20,
    clientHeight: 30,
    rowHeight: 10,
    minMidi: 36,
    maxMidi: 96,
    bufferRows: 1,
  });
  assert.equal(range.bufferRows, 1);
  assert.ok(range.maxMidi <= 96);
  assert.ok(range.minMidi >= 36);
  assert.ok(range.maxMidi >= range.minMidi);
  // Without buffer top would be ~94; with 1 row buffer → 95
  assert.equal(range.maxMidi, 95);
  assert.equal(range.minMidi, 91);
});

test('computeVisiblePitchRange respects DEFAULT_PITCH_BUFFER_ROWS', () => {
  const range = computeVisiblePitchRange({
    scrollTop: 0,
    clientHeight: 14,
    rowHeight: 14,
    minMidi: 60,
    maxMidi: 72,
  });
  assert.equal(range.bufferRows, DEFAULT_PITCH_BUFFER_ROWS);
  assert.equal(range.maxMidi, 72);
  assert.ok(range.minMidi <= 72);
});

test('noteIntersectsViewport detects tick and pitch overlap', () => {
  const ticks = { startTick: 100, endTick: 500 };
  const pitches = { minMidi: 60, maxMidi: 72 };
  assert.equal(
    noteIntersectsViewport({ startTick: 200, endTick: 300, pitchMidi: 64 }, ticks, pitches),
    true,
  );
  assert.equal(
    noteIntersectsViewport({ startTick: 500, endTick: 600, pitchMidi: 64 }, ticks, pitches),
    false,
  );
  assert.equal(
    noteIntersectsViewport({ startTick: 0, endTick: 100, pitchMidi: 64 }, ticks, pitches),
    false,
  );
  assert.equal(
    noteIntersectsViewport({ startTick: 200, endTick: 300, pitchMidi: 80 }, ticks, pitches),
    false,
  );
  // Touching edge: end === startTick → no intersection
  assert.equal(
    noteIntersectsViewport({ startTick: 0, endTick: 100, pitchMidi: 64 }, ticks, pitches),
    false,
  );
});

test('filterNotesInViewport culls offscreen notes and keeps buffered ones', () => {
  const notes = [
    { id: 'a', start_tick: 0, duration_ticks: 100, pitchMidi: 60 },
    { id: 'b', start_tick: 1000, duration_ticks: 200, pitchMidi: 64 },
    { id: 'c', start_tick: 5000, duration_ticks: 100, pitchMidi: 64 },
    { id: 'd', start_tick: 1000, duration_ticks: 100, pitchMidi: 90 },
  ];
  const visible = filterNotesInViewport(notes, {
    visibleTicks: { startTick: 800, endTick: 2000 },
    visiblePitches: { minMidi: 60, maxMidi: 72 },
  });
  assert.deepEqual(visible.map((n) => n.id), ['b']);
});

test('filterNotesInViewport accepts pitchOf for raw events', () => {
  const notes = [
    { id: 'x', start_tick: 10, duration_ticks: 20, pitch: 'C4' },
    { id: 'y', start_tick: 10, duration_ticks: 20, pitch: 'C8' },
  ];
  const visible = filterNotesInViewport(notes, {
    visibleTicks: { startTick: 0, endTick: 100 },
    visiblePitches: { minMidi: 60, maxMidi: 72 },
    pitchOf: (note) => (note.pitch === 'C4' ? 60 : 108),
  });
  assert.deepEqual(visible.map((n) => n.id), ['x']);
});

test('anchorZoom keeps content point under pointer stable', () => {
  const result = anchorZoom({
    scrollLeft: 100,
    pointerX: 50,
    oldPixelsPerTick: 0.05,
    newPixelsPerTick: 0.1,
    contentWidth: 20000,
    clientWidth: 400,
  });
  // content tick at pointer: (100+50)/0.05 = 3000
  // new scroll: 3000*0.1 - 50 = 250
  assert.equal(result.contentTick, 3000);
  assert.equal(result.scrollLeft, 250);
  assert.equal(result.clamped, false);
});

test('anchorZoom defaults pointer to viewport center', () => {
  const result = anchorZoom({
    scrollLeft: 0,
    oldPixelsPerTick: 0.05,
    newPixelsPerTick: 0.1,
    contentWidth: 10000,
    clientWidth: 200,
  });
  // center pointerX = 100; tick = 100/0.05 = 2000; scroll = 200 - 100 = 100
  assert.equal(result.contentTick, 2000);
  assert.equal(result.scrollLeft, 100);
});

test('anchorZoom clamps when zoom would scroll past content end', () => {
  const result = anchorZoom({
    scrollLeft: 900,
    pointerX: 50,
    oldPixelsPerTick: 0.05,
    newPixelsPerTick: 0.2,
    contentWidth: 1000,
    clientWidth: 400,
  });
  assert.equal(result.scrollLeft, 600); // max = 1000-400
  assert.equal(result.clamped, true);
});

test('clampScrollLeft bounds scroll into content', () => {
  assert.deepEqual(
    clampScrollLeft(-10, { contentWidth: 1000, clientWidth: 200 }),
    { scrollLeft: 0, clamped: true, maxScrollLeft: 800 },
  );
  assert.deepEqual(
    clampScrollLeft(500, { contentWidth: 1000, clientWidth: 200 }),
    { scrollLeft: 500, clamped: false, maxScrollLeft: 800 },
  );
  assert.deepEqual(
    clampScrollLeft(9999, { contentWidth: 1000, clientWidth: 200 }),
    { scrollLeft: 800, clamped: true, maxScrollLeft: 800 },
  );
  assert.deepEqual(
    clampScrollLeft(50, { contentWidth: 100, clientWidth: 200 }),
    { scrollLeft: 0, clamped: true, maxScrollLeft: 0 },
  );
});

test('clampScrollTop bounds vertical scroll', () => {
  assert.deepEqual(
    clampScrollTop(999, { contentHeight: 400, clientHeight: 100 }),
    { scrollTop: 300, clamped: true, maxScrollTop: 300 },
  );
});
