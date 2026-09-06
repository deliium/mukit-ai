import assert from 'node:assert/strict';
import test from 'node:test';

import {
  defaultTargetTrackIds,
  normalizeBarRange,
  pointerXToBar,
  selectedTickBoundaries,
} from './pianoRollSelection.js';

test('normalizeBarRange clamps and orders bars', () => {
  assert.deepEqual(normalizeBarRange(12, 9, 16), { startBar: 9, endBar: 12 });
  assert.deepEqual(normalizeBarRange(0, 20, 16), { startBar: 1, endBar: 16 });
});

test('pointerXToBar maps pixels to inclusive bars', () => {
  const mapped = pointerXToBar(1920 * 0.05 * 8 + 1, {
    pixelsPerTick: 0.05,
    barTicks: 1920,
    barCount: 16,
  });
  assert.equal(mapped.bar, 9);
});

test('selectedTickBoundaries returns inclusive bar tick window', () => {
  const bounds = selectedTickBoundaries(9, 12, {
    timeSignature: '4/4',
    ticksPerQuarter: 480,
    durationTicks: 16 * 1920,
  });
  assert.equal(bounds.startTick, 8 * 1920);
  assert.equal(bounds.endTick, 12 * 1920);
});

test('defaultTargetTrackIds prefers current track then falls back', () => {
  const composition = {
    tracks: [{ id: 'melody-1' }, { id: 'bass-1' }],
  };
  assert.deepEqual(
    defaultTargetTrackIds(composition, { mode: 'current', currentTrackId: 'bass-1' }),
    ['bass-1'],
  );
  assert.deepEqual(
    defaultTargetTrackIds(composition, { mode: 'all', currentTrackId: 'bass-1' }),
    ['melody-1', 'bass-1'],
  );
});
