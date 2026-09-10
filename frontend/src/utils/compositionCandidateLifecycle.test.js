import assert from 'node:assert/strict';
import test from 'node:test';

import {
  AI_CANDIDATE_STATUS,
  aiCandidateLogFields,
  buildAiCandidateEnvelope,
  captureAiRequestContext,
  detectAiRequestStale,
  makeAiCandidateId,
  toHistoryAiWarningCodes,
} from './compositionCandidateLifecycle.js';

test('makeAiCandidateId returns prefixed id', () => {
  const id = makeAiCandidateId('gen');
  assert.match(id, /^gen-[a-f0-9]+$/i);
});

test('toHistoryAiWarningCodes extracts codes and drops freeform oversize prose', () => {
  const codes = toHistoryAiWarningCodes([
    'Fake LLM mode: returned deterministic fixture composition (no API credits used).',
    'Fake LLM mode: applied deterministic Motif A transpose recurrence via production transforms.',
    'automation_omitted_from_notation: Track automation has no MusicXML score notation.',
    'sustain_projected: Sustain spans were projected to CC64 or pedal directions.',
    { code: 'ok_code' },
    'x'.repeat(100),
  ]);
  assert.deepEqual(codes, [
    'fake_llm_mode',
    'automation_omitted_from_notation',
    'sustain_projected',
    'ok_code',
    'x'.repeat(80),
  ]);
});

test('buildAiCandidateEnvelope keeps bounded instruction and warnings', () => {
  const envelope = buildAiCandidateEnvelope({
    candidateId: 'gen-12345678',
    operationType: 'generate-apply',
    composition: { schema_version: 'composition.v2' },
    sourceFingerprint: 'a'.repeat(64),
    candidateFingerprint: 'b'.repeat(64),
    instruction: `x${'y'.repeat(600)}`,
    warnings: Array.from({ length: 40 }, (_, i) => `w${i}`),
    provider: 'openai',
    model: 'gpt',
  });
  assert.equal(envelope.status, AI_CANDIDATE_STATUS.READY);
  assert.equal(envelope.instruction.length, 500);
  assert.equal(envelope.warnings.length, 32);
  assert.equal(envelope.provider, 'openai');
});

test('detectAiRequestStale catches project and composition edits', () => {
  const capture = captureAiRequestContext({
    currentProjectId: 'p1',
    activeBranchId: 'b1',
    currentRevisionId: 'r1',
    workingVersion: 2,
    compositionRevision: 'rev-a',
  });
  assert.equal(detectAiRequestStale(capture, {
    currentProjectId: 'p2',
    activeBranchId: 'b1',
    currentRevisionId: 'r1',
    workingVersion: 2,
    compositionRevision: 'rev-a',
  }).reason, 'project_changed');
  assert.equal(detectAiRequestStale(capture, {
    currentProjectId: 'p1',
    activeBranchId: 'b1',
    currentRevisionId: 'r1',
    workingVersion: 2,
    compositionRevision: 'rev-b',
  }).reason, 'composition_edited');
  assert.equal(detectAiRequestStale(capture, {
    currentProjectId: 'p1',
    activeBranchId: 'b1',
    currentRevisionId: 'r1',
    workingVersion: 2,
    compositionRevision: 'rev-a',
  }).stale, false);
});

test('aiCandidateLogFields never includes instruction text', () => {
  const fields = aiCandidateLogFields({
    candidate_id: 'gen-abcdefghijklmnop',
    operation_type: 'generate-apply',
    instruction: 'secret musical idea',
    source_fingerprint: 'abcdef0123456789',
    candidate_fingerprint: 'fedcba9876543210',
    warnings: ['w'],
  });
  assert.equal(fields.candidateIdSuffix, 'ijklmnop');
  assert.ok(!JSON.stringify(fields).includes('secret'));
});
