import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  CAPABILITY_REASONS,
  probeBrowserCapability,
} from './capabilityProbe.js';

test('probe reports webgpu unsupported without navigator.gpu', async () => {
  const snap = await probeBrowserCapability({
    isSecureContext: true,
    navigator: {},
  });
  assert.equal(snap.schema_version, 'browser.capability.v1');
  assert.equal(snap.webgpu_ready, false);
  assert.ok(snap.reason_codes.includes(CAPABILITY_REASONS.WEBGPU_UNSUPPORTED));
  assert.ok(snap.reason_codes.includes(CAPABILITY_REASONS.CPU_AVAILABLE));
});

test('probe reports insecure context', async () => {
  const snap = await probeBrowserCapability({
    isSecureContext: false,
    navigator: { gpu: { requestAdapter: async () => null } },
  });
  assert.equal(snap.webgpu_ready, false);
  assert.ok(snap.reason_codes.includes(CAPABILITY_REASONS.WEBGPU_INSECURE));
});

test('probe marks webgpu ready when API present (no request)', async () => {
  const snap = await probeBrowserCapability({
    isSecureContext: true,
    navigator: { gpu: { requestAdapter: async () => ({}) } },
  });
  assert.equal(snap.webgpu_ready, true);
  assert.ok(snap.reason_codes.includes(CAPABILITY_REASONS.WEBGPU_READY));
});
