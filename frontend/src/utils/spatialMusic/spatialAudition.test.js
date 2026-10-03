import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildSpatialCompileBody,
  buildStemMetaFromStemSet,
  createStemSpatialDestination,
  previewStemById,
  sceneHasStemSources,
  sceneHasTrackSources,
} from './spatialAudition.js';

test('sceneHasTrackSources / sceneHasStemSources detect kinds', () => {
  const scene = {
    sources: [
      { source_kind: 'track', track_id: 't1' },
      { source_kind: 'stem', stem_id: 's1' },
    ],
  };
  assert.equal(sceneHasTrackSources(scene), true);
  assert.equal(sceneHasStemSources(scene), true);
  assert.equal(sceneHasStemSources({ sources: [{ source_kind: 'track' }] }), false);
});

test('buildStemMetaFromStemSet requires sha256_prefix length >= 8', () => {
  const meta = buildStemMetaFromStemSet({
    stems: [
      { id: 'a', sha256_prefix: 'short', role: 'bass' },
      { id: 'b', sha256_prefix: 'abcdefgh', role: 'drums' },
      { stem_id: 'c', sha256_prefix: '12345678' },
    ],
  });
  assert.deepEqual(meta, [
    { stem_id: 'b', sha256_prefix: 'abcdefgh', role: 'drums' },
    { stem_id: 'c', sha256_prefix: '12345678', role: undefined },
  ]);
});

test('buildSpatialCompileBody includes composition only for track sources', () => {
  const composition = { schema_version: 'composition.v2', tracks: [] };
  const trackScene = {
    sources: [{ source_kind: 'track', track_id: 't1' }],
  };
  const trackBody = buildSpatialCompileBody({ scene: trackScene, composition });
  assert.equal(trackBody.composition, composition);
  assert.equal(trackBody.stems, undefined);

  const stemScene = {
    source_stem_set_id: 'set-1',
    sources: [{ source_kind: 'stem', stem_id: 's1' }],
  };
  const stemSet = {
    id: 'set-1',
    stems: [{ id: 's1', sha256_prefix: 'aaaaaaaa', role: 'lead' }],
  };
  const stemBody = buildSpatialCompileBody({
    scene: stemScene,
    composition,
    stemSet,
  });
  assert.equal(stemBody.composition, undefined);
  assert.equal(stemBody.stem_set_id, 'set-1');
  assert.equal(stemBody.stems.length, 1);
  assert.equal(stemBody.stems[0].stem_id, 's1');
});

test('buildSpatialCompileBody falls back to scene stem fingerprint', () => {
  const scene = {
    source_stem_set_id: 'set-1',
    source_stem_set_fingerprint: 'f'.repeat(64),
    sources: [{ source_kind: 'stem', stem_id: 's1' }],
  };
  const body = buildSpatialCompileBody({ scene, stemSet: { id: 'set-1', stems: [] } });
  assert.equal(body.stem_set_id, 'set-1');
  assert.equal(body.stem_set_fingerprint, 'f'.repeat(64));
  assert.equal(body.stems, undefined);
});

test('previewStemById indexes stem preview rows', () => {
  const map = previewStemById({
    sources: [
      { source_kind: 'track', track_id: 't1' },
      { source_kind: 'stem', stem_id: 's1', skipped: true },
    ],
  });
  assert.equal(map.has('s1'), true);
  assert.equal(map.get('s1').skipped, true);
  assert.equal(map.has('t1'), false);
});

test('createStemSpatialDestination falls back when Panner3D missing', () => {
  const disposed = [];
  const Tone = {
    Panner3D: undefined,
    Panner: class {
      constructor(pan) {
        this.pan = pan;
      }

      connect() { return this; }

      disconnect() {}

      dispose() { disposed.push('panner'); }
    },
    Gain: class {
      constructor(g) {
        this.gain = g;
      }

      connect() { return this; }

      disconnect() {}

      dispose() { disposed.push('gain'); }
    },
  };
  const master = { connect() { return this; } };
  const chain = createStemSpatialDestination(
    Tone,
    { azimuth_deg: 90, elevation_deg: 0, distance: 2, spread: 0 },
    { left_gain: 1, right_gain: 0, distance_gain: 0.5 },
    master,
  );
  assert.ok(chain);
  assert.equal(chain.reason, 'compiled_stereo');
  assert.ok(chain.input);
  assert.ok(chain.nodes.length >= 1);
});
