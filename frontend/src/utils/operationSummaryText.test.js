import test from 'node:test';
import assert from 'node:assert/strict';
import { mintOperationRunId, operationSummaryText } from './operationSummaryText.js';

test('missing summary renders nothing', () => {
  assert.equal(operationSummaryText(null), '');
  assert.equal(operationSummaryText(undefined), '');
  assert.equal(operationSummaryText({}), '');
});

test('summary line includes duration, calls, revisions, and failures', () => {
  assert.equal(
    operationSummaryText({
      status: 'ok',
      duration_ms: 40,
      model_call_count: 2,
      revision_count: 1,
      failure_count: 0,
      budget_code: null,
    }),
    'ok · 40 ms · 2 model calls · 1 revisions · 0 failures',
  );
});

test('budget code is visible when set', () => {
  assert.equal(
    operationSummaryText({
      status: 'budget_exceeded',
      duration_ms: 12,
      model_call_count: 1,
      revision_count: 0,
      failure_count: 0,
      budget_code: 'operation_model_call_budget',
    }),
    'budget_exceeded · 12 ms · 1 model calls · 0 revisions · 0 failures · operation_model_call_budget',
  );
});

test('cancelled status is the word cancelled', () => {
  assert.match(
    operationSummaryText({ status: 'cancelled', duration_ms: 3, model_call_count: 0, revision_count: 0, failure_count: 1 }),
    /^cancelled\b/,
  );
});

test('mintOperationRunId returns a uuid', () => {
  const runId = mintOperationRunId();
  assert.match(
    runId,
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/,
  );
});
