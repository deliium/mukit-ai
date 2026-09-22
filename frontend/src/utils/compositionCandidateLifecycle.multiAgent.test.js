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

test('buildArtifactRoleMapFromLog and validateArtifactRoleMap', async () => {
  const { buildArtifactRoleMapFromLog, validateArtifactRoleMap, formatRevisionAiArtifactSummary } = await import('./compositionCandidateLifecycle.js');
  const log = [
    { artifact_id: 'b1', content_type: 'agent.brief.v1' },
    { artifact_id: 'h1', content_type: 'agent.harmony_plan.v1' },
    { artifact_id: 'm1', content_type: 'agent.motif_plan.v1' },
    { artifact_id: 'a1', content_type: 'agent.arrangement_plan.v1' },
    { artifact_id: 'c1', content_type: 'agent.critique.v1' },
  ];
  const map = buildArtifactRoleMapFromLog(log);
  assert.equal(map.brief.artifact_id, 'b1');
  assert.equal(map.revision_plan, null);
  assert.equal(validateArtifactRoleMap(map).ok, true);
  assert.equal(validateArtifactRoleMap({ brief: map.brief }).ok, false);
  const line = formatRevisionAiArtifactSummary({
    ai_artifacts: {
      schema_version: 'revision.ai_artifact_summary.v1',
      roles: {
        brief: map.brief,
        harmony_plan: map.harmony_plan,
        motif_plan: map.motif_plan,
        arrangement_plan: map.arrangement_plan,
        critique: map.critique,
        revision_plan: null,
      },
    },
  });
  assert.match(line, /Brief/);
  assert.match(line, /Harmony/);
  assert.doesNotMatch(line, /harmony_artifact/);
});
