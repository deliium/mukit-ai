import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildSpatialMixerOverridesFromPreview,
  distanceGainToTrimDb,
  spatialMixToCartesian,
  stereoGainsToPanOffset,
} from './spatialApply.js';

test('stereoGainsToPanOffset maps equal L/R to center', () => {
  assert.ok(Math.abs(stereoGainsToPanOffset(0.707, 0.707)) < 1e-5);
});

test('stereoGainsToPanOffset maps left-dominant to negative pan', () => {
  assert.ok(Math.abs(stereoGainsToPanOffset(1, 0) - (-1)) < 1e-5);
});

test('stereoGainsToPanOffset maps right-dominant to positive pan', () => {
  assert.ok(Math.abs(stereoGainsToPanOffset(0, 1) - 1) < 1e-5);
});

test('spatialMixToCartesian places +azimuth (left) on negative X', () => {
  const pos = spatialMixToCartesian(90, 0, 2);
  assert.ok(Math.abs(pos.x - (-2)) < 1e-5);
  assert.ok(Math.abs(pos.z) < 1e-5);
});

test('buildSpatialMixerOverridesFromPreview builds track overrides', () => {
  const { overrides, trackCount } = buildSpatialMixerOverridesFromPreview({
    sources: [
      {
        source_kind: 'track',
        track_id: 'melody-1',
        stereo: { left_gain: 1, right_gain: 0 },
        distance_gain: 1,
        muted: false,
      },
      {
        source_kind: 'stem',
        stem_id: 's1',
        stereo: { left_gain: 0.5, right_gain: 0.5 },
        distance_gain: 0.5,
      },
    ],
  });
  assert.equal(trackCount, 1);
  assert.ok(Math.abs(overrides['melody-1'].panOffset - (-1)) < 1e-5);
  assert.ok(Math.abs(distanceGainToTrimDb(0.5) - (20 * Math.log10(0.5))) < 1e-5);
});
