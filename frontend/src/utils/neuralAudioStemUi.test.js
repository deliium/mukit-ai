import test from 'node:test';
import assert from 'node:assert/strict';
import {
  isNeuralAudioStemSetStale,
  latestStemsByRole,
  neuralAudioStemSyncDisclaimer,
} from './neuralAudioStemUi.js';

test('generative sync disclaimer refuses sample-accurate claim', () => {
  const text = neuralAudioStemSyncDisclaimer('generative_independent');
  assert.match(text, /not sample-locked/i);
  assert.match(text, /do not claim sample-accurate/i);
});

test('stem set stale when fingerprint diverges', () => {
  assert.equal(
    isNeuralAudioStemSetStale(
      { status: 'complete', source_fingerprint: 'aaa' },
      'bbb',
    ),
    true,
  );
  assert.equal(
    isNeuralAudioStemSetStale(
      { status: 'complete', source_fingerprint: 'aaa' },
      'aaa',
    ),
    false,
  );
});

test('latestStemsByRole prefers newer superseding stem', () => {
  const map = latestStemsByRole({
    stems: [
      {
        id: '1',
        stem_role: 'strings',
        status: 'complete',
        created_at: '2026-09-24T00:00:00Z',
      },
      {
        id: '2',
        stem_role: 'strings',
        status: 'complete',
        created_at: '2026-09-24T01:00:00Z',
        supersedes_stem_id: '1',
      },
      {
        id: '3',
        stem_role: 'piano',
        status: 'complete',
        created_at: '2026-09-24T00:00:00Z',
      },
    ],
  });
  assert.equal(map.get('strings').id, '2');
  assert.equal(map.get('piano').id, '3');
});
