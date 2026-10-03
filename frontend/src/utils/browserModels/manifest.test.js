import assert from 'node:assert/strict';
import { test } from 'node:test';

import { isForbiddenAssetUrl, parseBrowserModelManifest } from './manifest.js';
import { FALLBACK_REASONS } from './constants.js';

test('parse accepts asset_kind=none ship-1 manifest', () => {
  const parsed = parseBrowserModelManifest({
    schema_version: 'browser.model.manifest.v1',
    model_id: 'browser:symbolic-features-v1',
    profile_id: 'symbolic.features.v1',
    ops: ['embed'],
    asset_kind: 'none',
    asset_url: '',
    sha256: '',
    size_bytes: 0,
    input_spec: {},
    output_spec: { dims: 81 },
  });
  assert.equal(parsed.ok, true);
  assert.equal(parsed.manifest.model_id, 'browser:symbolic-features-v1');
});

test('parse refuses private llm path', () => {
  const parsed = parseBrowserModelManifest({
    schema_version: 'browser.model.manifest.v1',
    model_id: 'browser:bad',
    profile_id: 'x',
    ops: ['embed'],
    asset_kind: 'onnx',
    asset_url: '/models/llm/secret.gguf',
    sha256: 'abc',
    size_bytes: 10,
  });
  assert.equal(parsed.ok, false);
  assert.equal(parsed.code, FALLBACK_REASONS.MANIFEST_REFUSED);
});

test('isForbiddenAssetUrl catches dataset / personal roots', () => {
  assert.equal(isForbiddenAssetUrl('DATASET_ROOT/foo'), true);
  assert.equal(isForbiddenAssetUrl('/browser-models/ok.bin'), false);
  assert.equal(isForbiddenAssetUrl('file:///tmp/x'), true);
});
