/**
 * One-line label for operation.summary.v1. No network and no composition.
 */

export function operationSummaryText(summary) {
  if (!summary || typeof summary !== 'object') return '';
  const parts = [];
  if (summary.status) parts.push(String(summary.status));
  if (Number.isFinite(summary.duration_ms)) parts.push(`${summary.duration_ms} ms`);
  if (Number.isFinite(summary.model_call_count)) {
    parts.push(`${summary.model_call_count} model calls`);
  }
  if (Number.isFinite(summary.revision_count)) {
    parts.push(`${summary.revision_count} revisions`);
  }
  if (Number.isFinite(summary.failure_count)) {
    parts.push(`${summary.failure_count} failures`);
  }
  if (summary.budget_code) parts.push(String(summary.budget_code));
  return parts.join(' · ');
}

export function mintOperationRunId() {
  if (typeof globalThis.crypto?.randomUUID === 'function') {
    return globalThis.crypto.randomUUID();
  }
  return null;
}
