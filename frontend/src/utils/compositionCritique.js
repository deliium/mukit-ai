/**
 * Normalize / display helpers for agent.critique.v1 findings (Analysis tab).
 */

export const CRITIQUE_STRATA = [
  'hard_constraint',
  'technical',
  'stylistic',
  'subjective',
];

export const CRITIQUE_STRATUM_LABELS = {
  hard_constraint: 'Hard constraints',
  technical: 'Technical',
  stylistic: 'Stylistic',
  subjective: 'Subjective',
};

export function normalizeCritiqueResult(payload) {
  if (!payload || typeof payload !== 'object') return null;
  const critique = payload.critique && typeof payload.critique === 'object'
    ? payload.critique
    : payload;
  if (critique.schema_version && critique.schema_version !== 'agent.critique.v1') {
    return null;
  }
  const findings = Array.isArray(critique.findings) ? critique.findings : [];
  return {
    recommendation: critique.recommendation || 'approve',
    summary: typeof critique.summary === 'string' ? critique.summary : '',
    findings,
    stratum_counts: critique.stratum_counts || {},
    engine_version: critique.engine_version || null,
    model_critique_status: critique.model_critique_status || null,
    reason_codes: Array.isArray(critique.reason_codes) ? critique.reason_codes : [],
  };
}

export function filterFindingsByStratum(findings, stratumFilter) {
  const list = Array.isArray(findings) ? findings : [];
  if (!stratumFilter || stratumFilter === 'all') return list;
  return list.filter((f) => f && f.stratum === stratumFilter);
}

export function formatFindingRange(finding) {
  const range = finding?.affected_range;
  if (!range) return '';
  if (range.start_bar != null && range.end_bar != null) {
    return range.start_bar === range.end_bar
      ? `bar ${range.start_bar}`
      : `bars ${range.start_bar}–${range.end_bar}`;
  }
  return '';
}

export function critiqueSummaryFromArtifact(artifact) {
  if (!artifact || artifact.content_type !== 'agent.critique.v1') return null;
  return normalizeCritiqueResult(artifact.payload || artifact);
}
