import test from 'node:test';
import assert from 'node:assert/strict';

import {
  buildCompositionRevisionArtifactRef,
  honestyHeadline,
  trustClassLabel,
} from './contentProvenanceChain.js';

test('mukit_internal label is studio metadata', () => {
  assert.equal(
    trustClassLabel('mukit_internal'),
    'Studio metadata (not cryptographically signed)',
  );
});

test('honesty headline refuses Cryptographically signed when false even if c2pa string present', () => {
  const headline = honestyHeadline({
    cryptographic: false,
    c2pa: { attached: true, fake_mode: true, status: 'fake c2pa' },
  });
  assert.equal(headline, 'Studio metadata (not cryptographically signed)');
  assert.ok(!headline.includes('Cryptographically signed'));
});

test('honesty headline allows Cryptographically signed only when true', () => {
  assert.equal(
    honestyHeadline({ cryptographic: true, c2pa: { attached: true, fake_mode: false } }),
    'Cryptographically signed',
  );
});

test('buildCompositionRevisionArtifactRef', () => {
  assert.deepEqual(buildCompositionRevisionArtifactRef('rev_abc'), {
    kind: 'composition_revision',
    id: 'rev_abc',
  });
});
