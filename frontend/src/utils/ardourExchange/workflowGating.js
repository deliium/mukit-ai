/** Pure gating helpers for the Ardour workflow surface. */

export function canSendToAiComposer({ exchangeEnabled, exchangeRootConfigured, busy }) {
  return Boolean(exchangeEnabled && exchangeRootConfigured && !busy);
}

export function canRealizeExchange({ exchangeEnabled, hasPreview, busy }) {
  return Boolean(exchangeEnabled && hasPreview && !busy);
}

export function canApplyRealizeCandidate({ hasRealizeResult, selectedCandidateId, busy }) {
  return Boolean(hasRealizeResult && selectedCandidateId && !busy);
}

export function canPrepareOutbound({ hasPreview, busy }) {
  return Boolean(hasPreview && !busy);
}
