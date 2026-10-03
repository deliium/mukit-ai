import assert from 'node:assert/strict';
import test from 'node:test';

import {
  canApplyRealizeCandidate,
  canPrepareOutbound,
  canRealizeExchange,
  canSendToAiComposer,
} from './workflowGating.js';

test('canSendToAiComposer requires enabled exchange + configured root', () => {
  assert.equal(canSendToAiComposer({
    exchangeEnabled: true,
    exchangeRootConfigured: true,
    busy: false,
  }), true);
  assert.equal(canSendToAiComposer({
    exchangeEnabled: false,
    exchangeRootConfigured: true,
    busy: false,
  }), false);
  assert.equal(canSendToAiComposer({
    exchangeEnabled: true,
    exchangeRootConfigured: false,
    busy: false,
  }), false);
  assert.equal(canSendToAiComposer({
    exchangeEnabled: true,
    exchangeRootConfigured: true,
    busy: true,
  }), false);
});

test('canRealizeExchange requires preview', () => {
  assert.equal(canRealizeExchange({
    exchangeEnabled: true,
    hasPreview: true,
    busy: false,
  }), true);
  assert.equal(canRealizeExchange({
    exchangeEnabled: true,
    hasPreview: false,
    busy: false,
  }), false);
});

test('canApplyRealizeCandidate and canPrepareOutbound gate busy/selection', () => {
  assert.equal(canApplyRealizeCandidate({
    hasRealizeResult: true,
    selectedCandidateId: 'c1',
    busy: false,
  }), true);
  assert.equal(canApplyRealizeCandidate({
    hasRealizeResult: true,
    selectedCandidateId: null,
    busy: false,
  }), false);
  assert.equal(canPrepareOutbound({ hasPreview: true, busy: false }), true);
  assert.equal(canPrepareOutbound({ hasPreview: true, busy: true }), false);
});
