import assert from 'node:assert/strict';
import test from 'node:test';

import {
  AI_CANDIDATE_STATUS,
  aiCandidateLogFields,
  aiRuntimeFieldsFromResponse,
  buildAiCandidateEnvelope,
  buildGenerationMetaFromCandidate,
  buildHistoryAiProvenance,
  captureAiRequestContext,
  detectAiRequestStale,
  formatRevisionProvenanceSummary,
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

test('buildAiCandidateEnvelope stores additive runtime provenance and fallback flags', () => {
  const envelope = buildAiCandidateEnvelope({
    candidateId: 'gen-deadbeef',
    operationType: 'arrangement-preview',
    composition: { schema_version: 'composition.v2' },
    sourceFingerprint: 'a'.repeat(64),
    candidateFingerprint: 'b'.repeat(64),
    provider: 'openai',
    model: 'gpt-4o-mini',
    modelId: 'openai:gpt-4o-mini',
    runtime: 'openai_compatible_chat',
    capability: 'language_planner',
    operation: 'arrangement_preview',
    requestedModelId: 'openai:gpt-4o',
    resolvedModelId: 'openai:gpt-4o-mini',
    fallbackApplied: true,
    generationParameters: { temperature: 0.2 },
  });
  assert.equal(envelope.model_id, 'openai:gpt-4o-mini');
  assert.equal(envelope.runtime, 'openai_compatible_chat');
  assert.equal(envelope.capability, 'language_planner');
  assert.equal(envelope.requested_model_id, 'openai:gpt-4o');
  assert.equal(envelope.fallback_applied, true);
  assert.deepEqual(envelope.generation_parameters, { temperature: 0.2 });
});

test('aiRuntimeFieldsFromResponse and buildHistoryAiProvenance map response → durable AiProvenance', () => {
  const fields = aiRuntimeFieldsFromResponse({
    provider: 'openai',
    model: 'gpt-4o-mini',
    model_id: 'openai:gpt-4o-mini',
    model_version: '2024-08',
    runtime: 'openai_compatible_chat',
    capability: 'language_planner',
    operation: 'generate',
    requested_model_id: 'openai:gpt-4o',
    resolved_model_id: 'openai:gpt-4o-mini',
    fallback_applied: true,
    generation_parameters: { max_tokens: 100 },
  });
  assert.equal(fields.modelId, 'openai:gpt-4o-mini');
  assert.equal(fields.fallbackApplied, true);
  assert.equal(fields.resolvedModelId, 'openai:gpt-4o-mini');

  const provenance = buildHistoryAiProvenance({
    candidate_id: 'gen-1',
    provider: 'openai',
    model: 'gpt-4o-mini',
    model_id: 'openai:gpt-4o-mini',
    runtime: 'openai_compatible_chat',
    capability: 'language_planner',
    operation: 'generate',
    generation_parameters: { max_tokens: 100 },
    instruction: 'write a motif',
    warnings: ['fake_llm_mode'],
  });
  assert.equal(provenance.model_id, 'openai:gpt-4o-mini');
  assert.equal(provenance.runtime, 'openai_compatible_chat');
  assert.equal(provenance.user_instruction, 'write a motif');
  assert.deepEqual(provenance.warning_codes, ['fake_llm_mode']);
});

test('buildGenerationMetaFromCandidate mirrors pipeline seed and stages', () => {
  const meta = buildGenerationMetaFromCandidate({
    provider: 'fake',
    model: 'fake-v1',
    model_id: 'fake:fake-v1',
    pipeline_id: 'hybrid_plan_symbolic',
    seed: 42,
    stages: [
      { model_id: 'fake:fake-v1' },
      { model_id: 'fake:symbolic-tiny' },
    ],
    generation_parameters: {
      provenance_schema: 'generation.provenance.v1',
      pipeline_id: 'hybrid_plan_symbolic',
      seed: 42,
      stages: [
        { model_id: 'fake:fake-v1' },
        { model_id: 'fake:symbolic-tiny' },
      ],
    },
  }, { genre: 'pop' });
  assert.equal(meta.pipeline_id, 'hybrid_plan_symbolic');
  assert.equal(meta.seed, 42);
  assert.deepEqual(meta.stage_model_ids, ['fake:fake-v1', 'fake:symbolic-tiny']);
  assert.equal(meta.generation_parameters.provenance_schema, 'generation.provenance.v1');
  const line = formatRevisionProvenanceSummary({
    generation_parameters: meta.generation_parameters,
  });
  assert.match(line, /hybrid_plan_symbolic/);
  assert.match(line, /seed 42/);
  assert.match(line, /fake:symbolic-tiny/);
});
