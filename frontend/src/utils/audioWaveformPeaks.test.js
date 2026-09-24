import assert from 'node:assert/strict';
import test from 'node:test';

import {
  DEFAULT_MAX_PEAKS,
  buildWaveformPeaks,
  pointerXToSourceSeconds,
} from './audioWaveformPeaks.js';

function fakeBuffer({ length = 1000, sampleRate = 1000, duration = 1 } = {}) {
  const data = new Float32Array(length);
  for (let i = 0; i < length; i += 1) {
    data[i] = Math.sin((i / length) * Math.PI) * 0.5;
  }
  return {
    duration,
    sampleRate,
    getChannelData: () => data,
  };
}

test('buildWaveformPeaks downsamples and caps count', () => {
  const result = buildWaveformPeaks(fakeBuffer({ length: 10000 }), { maxPeaks: 100 });
  assert.ok(result);
  assert.equal(result.peakCount, 100);
  assert.equal(result.peaks.length, 100);
  assert.ok(result.durationSeconds > 0);
  assert.ok(result.peakCount <= DEFAULT_MAX_PEAKS || result.peakCount === 100);
});

test('buildWaveformPeaks returns null for missing buffer', () => {
  assert.equal(buildWaveformPeaks(null), null);
});

test('pointerXToSourceSeconds maps edges', () => {
  const rect = { left: 0, width: 200 };
  assert.equal(pointerXToSourceSeconds(0, rect, 10), 0);
  assert.equal(pointerXToSourceSeconds(200, rect, 10), 10);
  assert.ok(Math.abs(pointerXToSourceSeconds(100, rect, 10) - 5) < 1e-9);
});
