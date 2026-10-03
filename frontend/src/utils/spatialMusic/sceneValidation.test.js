import assert from 'node:assert/strict';
import test from 'node:test';

import { SCENE_SCHEMA_VERSION } from './constants.js';
import { validateSpatialSceneBody } from './sceneValidation.js';

function minimalScene(overrides = {}) {
  return {
    schema_version: SCENE_SCHEMA_VERSION,
    name: 'Test scene',
    source_composition_fingerprint: 'a'.repeat(32),
    sources: [
      {
        id: 'ssrc_m1',
        source_kind: 'track',
        track_id: 'melody-1',
        azimuth_deg: 20,
        elevation_deg: 0,
        distance: 1.5,
        spread: 0.1,
      },
    ],
    ...overrides,
  };
}

test('validateSpatialSceneBody accepts a minimal track scene', () => {
  assert.deepEqual(validateSpatialSceneBody(minimalScene()), { ok: true });
});

test('validateSpatialSceneBody refuses embedded events', () => {
  const result = validateSpatialSceneBody(minimalScene({ events: [{ id: 'n1' }] }));
  assert.equal(result.ok, false);
  assert.equal(result.code, 'scene_embeds_events');
});

test('validateSpatialSceneBody refuses stem_role without stem_id', () => {
  const result = validateSpatialSceneBody(
    minimalScene({
      sources: [
        {
          id: 'ssrc_s1',
          source_kind: 'stem',
          stem_role: 'melody',
          azimuth_deg: 0,
          elevation_deg: 0,
          distance: 1,
          spread: 0,
        },
      ],
      source_stem_set_id: 'set1',
      source_stem_set_fingerprint: 'b'.repeat(64),
    }),
  );
  assert.equal(result.ok, false);
  assert.equal(result.code, 'stem_id_required');
});

test('validateSpatialSceneBody refuses pcm embeds', () => {
  const result = validateSpatialSceneBody(minimalScene({ audio_base64: 'AAAA' }));
  assert.equal(result.ok, false);
  assert.equal(result.code, 'scene_embeds_pcm');
});
