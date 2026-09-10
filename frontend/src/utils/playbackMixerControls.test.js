import assert from 'node:assert/strict';
import test from 'node:test';

import {
  PLAYBACK_MIXER_SCOPE_PREVIEW,
  PLAYBACK_MIXER_SCOPE_WORKING,
} from './playbackSource.js';
import {
  ACTIVITY_MATERIAL_DELTA,
  activityLevelsMateriallyChanged,
  buildDefaultMixerControls,
  createDefaultTrackControl,
  mergeMixerControls,
  mixerControlsStateKey,
  normalizeTrackControlPatch,
  sanitizeMixerLogMeta,
} from './playbackMixerControls.js';

test('default mixer controls are neutral session overrides', () => {
  const control = createDefaultTrackControl(100);
  assert.equal(control.trimDb, 0);
  assert.equal(control.panOffset, 0);
  assert.equal(control.reverbSend, 0);
  assert.equal(control.muted, false);
  assert.equal(control.solo, false);
  assert.equal(control.volumeMidi, 100);
});

test('normalizeTrackControlPatch clamps send/pan/trim', () => {
  const next = normalizeTrackControlPatch({
    reverbSend: 4,
    panOffset: -3,
    trimDb: 100,
    muted: 1,
  });
  assert.equal(next.reverbSend, 1);
  assert.equal(next.panOffset, -1);
  assert.equal(next.trimDb, 24);
  assert.equal(next.muted, true);
});

test('mergeMixerControls preserves working overrides for surviving track ids', () => {
  const composition = {
    schema_version: 'composition.v2',
    tracks: [
      { id: 'a', volume: 90 },
      { id: 'b', volume: 80 },
    ],
  };
  const existing = {
    a: { muted: true, trimDb: 3, volumeMidi: 90 },
    gone: { muted: true },
  };
  const merged = mergeMixerControls(existing, composition);
  assert.equal(merged.a.muted, true);
  assert.equal(merged.a.trimDb, 3);
  assert.equal(merged.b.muted, false);
  assert.equal(Object.prototype.hasOwnProperty.call(merged, 'gone'), false);
});

test('activityLevelsMateriallyChanged ignores sub-threshold noise', () => {
  const previous = { tracks: { a: 0.5 }, clipped: false };
  assert.equal(
    activityLevelsMateriallyChanged(previous, {
      tracks: { a: 0.5 + ACTIVITY_MATERIAL_DELTA / 2 },
      clipped: false,
    }),
    false,
  );
  assert.equal(
    activityLevelsMateriallyChanged(previous, {
      tracks: { a: 0.5 + ACTIVITY_MATERIAL_DELTA },
      clipped: false,
    }),
    true,
  );
  assert.equal(
    activityLevelsMateriallyChanged(previous, { tracks: { a: 0.5 }, clipped: true }),
    true,
  );
});

test('sanitizeMixerLogMeta drops composition payloads', () => {
  const sanitized = sanitizeMixerLogMeta({
    trackId: 'piano-1',
    mixerScope: PLAYBACK_MIXER_SCOPE_WORKING,
    composition: { tracks: [] },
    events: [{ pitch: 'C4' }],
    muted: true,
  });
  assert.equal(sanitized.trackId, 'piano-1');
  assert.equal(sanitized.muted, true);
  assert.equal(Object.prototype.hasOwnProperty.call(sanitized, 'composition'), false);
  assert.equal(Object.prototype.hasOwnProperty.call(sanitized, 'events'), false);
});

test('mixerControlsStateKey maps preview scope', () => {
  assert.equal(mixerControlsStateKey(PLAYBACK_MIXER_SCOPE_PREVIEW), 'previewTrackControls');
  assert.equal(mixerControlsStateKey(PLAYBACK_MIXER_SCOPE_WORKING), 'trackControls');
});

test('buildDefaultMixerControls returns empty for missing tracks', () => {
  assert.deepEqual(buildDefaultMixerControls(null), {});
  assert.deepEqual(buildDefaultMixerControls({}), {});
});
