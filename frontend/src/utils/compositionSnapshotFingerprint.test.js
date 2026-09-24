import assert from 'node:assert/strict';
import test from 'node:test';

import {
  NULL_SNAPSHOT_FINGERPRINT,
  canonicalSnapshotJsonDumps,
  compositionSnapshotFingerprint,
  isNeuralRenderStale,
  resolveLiveSnapshotFingerprint,
  sortKeysDeep,
} from './compositionSnapshotFingerprint.js';

test('sortKeysDeep orders object keys', () => {
  assert.deepEqual(sortKeysDeep({ b: 1, a: 2 }), { a: 2, b: 1 });
});

test('canonicalSnapshotJsonDumps is stable across key order', () => {
  const left = canonicalSnapshotJsonDumps({ b: 1, a: { d: 2, c: 3 } });
  const right = canonicalSnapshotJsonDumps({ a: { c: 3, d: 2 }, b: 1 });
  assert.equal(left, right);
});

test('null composition fingerprint', async () => {
  assert.equal(await compositionSnapshotFingerprint(null), NULL_SNAPSHOT_FINGERPRINT);
});

test('composition fingerprint is stable hex', async () => {
  const doc = {
    schema_version: 'composition.v2',
    tempo: 120,
    tracks: [],
  };
  const a = await compositionSnapshotFingerprint(doc);
  const b = await compositionSnapshotFingerprint({ tracks: [], tempo: 120, schema_version: 'composition.v2' });
  assert.equal(a, b);
  assert.equal(a.length, 64);
});

test('isNeuralRenderStale compares fingerprints', () => {
  assert.equal(isNeuralRenderStale('aaa', 'aaa'), false);
  assert.equal(isNeuralRenderStale('aaa', 'bbb'), true);
  assert.equal(isNeuralRenderStale(null, 'bbb'), false);
});

test('resolveLiveSnapshotFingerprint prefers working when saved', async () => {
  const fp = await resolveLiveSnapshotFingerprint({
    composition: { schema_version: 'composition.v2' },
    workingFingerprint: 'working_fp_saved',
    saveStatus: 'saved',
  });
  assert.equal(fp, 'working_fp_saved');
});
