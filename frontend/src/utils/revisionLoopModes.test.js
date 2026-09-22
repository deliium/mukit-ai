import assert from 'node:assert/strict';
import test from 'node:test';

import {
  maxPassesForRevisionMode,
  normalizeRevisionMode,
} from './revisionLoopModes.js';

test('maps product modes to hard caps', () => {
  assert.equal(maxPassesForRevisionMode('off'), 0);
  assert.equal(maxPassesForRevisionMode('fast'), 1);
  assert.equal(maxPassesForRevisionMode('balanced'), 2);
  assert.equal(maxPassesForRevisionMode('thorough'), 3);
});

test('falls back to off for unknown modes', () => {
  assert.equal(normalizeRevisionMode('unbounded'), 'off');
  assert.equal(maxPassesForRevisionMode(''), 0);
});
