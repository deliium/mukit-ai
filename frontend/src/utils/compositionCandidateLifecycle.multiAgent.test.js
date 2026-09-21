/**
 * FE unit tests for multi-agent candidate envelope mapping.
 */

import assert from 'node:assert/strict';
import test from 'node:test';
import { buildAiCandidateEnvelope } from './compositionCandidateLifecycle.js';

test('buildAiCandidateEnvelope accepts multi-agent-apply operation_type', () => {
  const envelope = buildAiCandidateEnvelope({
    candidateId: 'ma-1',
    operationType: 'multi-agent-apply',
    composition: { schema_version: 'composition.v2' },
    sourceFingerprint: 'a'.repeat(64),
    candidateFingerprint: 'b'.repeat(64),
    generationParameters: { pipeline_id: 'agent_spine_v1' },
    extras: {
      agent_sequence: ['creative_director', 'harmony', 'melody_motif', 'arrangement', 'critic'],
      recommendation: 'approve',
    },
  });
  assert.equal(envelope.operation_type, 'multi-agent-apply');
  assert.equal(envelope.recommendation, 'approve');
  assert.deepEqual(envelope.agent_sequence, [
    'creative_director',
    'harmony',
    'melody_motif',
    'arrangement',
    'critic',
  ]);
  assert.equal(envelope.generation_parameters.pipeline_id, 'agent_spine_v1');
});
