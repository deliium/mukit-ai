import assert from 'node:assert/strict';
import test from 'node:test';

import { audibleRevisionKey, canonicalizeValue, notationRevisionKey } from './compositionCanonical.js';

test('canonicalizeValue sorts object keys recursively and preserves array order', () => {
  const input = { b: 2, a: { d: 4, c: [3, 1] } };
  assert.deepEqual(canonicalizeValue(input), { a: { c: [3, 1], d: 4 }, b: 2 });
});

test('audible and notation revision keys change when V2 expression fields change', () => {
  const base = {
    schema_version: 'composition.v2',
    tempo: 100,
    tracks: [{
      id: 'a',
      expression: 127,
      events: [{ pitch: 'C4', start_tick: 0, duration_ticks: 480, velocity: 80 }],
    }],
  };
  const withExpression = structuredClone(base);
  withExpression.tracks[0].expression = 100;
  assert.notEqual(audibleRevisionKey(base), audibleRevisionKey(withExpression));
  assert.notEqual(notationRevisionKey(base), notationRevisionKey(withExpression));
});
