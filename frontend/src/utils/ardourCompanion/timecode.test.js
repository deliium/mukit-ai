import assert from 'node:assert/strict';
import test from 'node:test';

import { formatArdourTimecode } from './timecode.js';

test('formatArdourTimecode formats known samples and rate as HH:MM:SS', () => {
  const result = formatArdourTimecode(48000 * 3661, 48000);
  assert.equal(result.mode, 'clock');
  assert.equal(result.display, '01:01:01');
  assert.ok(Math.abs(result.seconds - 3661) < 1e-6);
});

test('formatArdourTimecode falls back to samples-only when sample_rate missing', () => {
  const result = formatArdourTimecode(12345, null);
  assert.equal(result.mode, 'samples');
  assert.equal(result.display, '12345 samples');
  assert.equal(result.seconds, null);
});

test('formatArdourTimecode returns unavailable for missing samples', () => {
  assert.equal(formatArdourTimecode(null, 48000).mode, 'unavailable');
  assert.equal(formatArdourTimecode(-1, 48000).mode, 'unavailable');
});

test('formatArdourTimecode optionally appends display-only 30 fps frames', () => {
  const result = formatArdourTimecode(24000, 48000, { includeFrames: true });
  assert.equal(result.display, '00:00:00:15');
});
