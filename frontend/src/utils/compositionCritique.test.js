import assert from 'node:assert/strict';
import test from 'node:test';

import {
  CRITIQUE_STRATUM_LABELS,
  critiqueSummaryFromArtifact,
  filterFindingsByStratum,
  formatFindingRange,
  normalizeCritiqueResult,
} from './compositionCritique.js';

test('normalizeCritiqueResult accepts nested and flat payloads', () => {
  const nested = normalizeCritiqueResult({
    critique: {
      schema_version: 'agent.critique.v1',
      recommendation: 'approve',
      findings: [
        {
          stratum: 'stylistic',
          code: 'climax_lacks_contrast',
          affected_range: { start_bar: 5, end_bar: 8 },
        },
      ],
      stratum_counts: { stylistic: 1 },
    },
  });
  assert.equal(nested.recommendation, 'approve');
  assert.equal(nested.findings.length, 1);
  assert.equal(formatFindingRange(nested.findings[0]), 'bars 5–8');
  assert.equal(CRITIQUE_STRATUM_LABELS.stylistic, 'Stylistic');
});

test('filterFindingsByStratum and artifact summary', () => {
  const findings = [
    { stratum: 'stylistic', code: 'a' },
    { stratum: 'technical', code: 'b' },
  ];
  assert.equal(filterFindingsByStratum(findings, 'stylistic').length, 1);
  assert.equal(filterFindingsByStratum(findings, 'all').length, 2);
  const summary = critiqueSummaryFromArtifact({
    content_type: 'agent.critique.v1',
    payload: {
      schema_version: 'agent.critique.v1',
      recommendation: 'approve',
      findings: [],
    },
  });
  assert.equal(summary.recommendation, 'approve');
});
