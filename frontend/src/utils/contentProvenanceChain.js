/**
 * Pure helpers for content.provenance.chain.v1 honesty copy.
 * Cryptographically signed only when honesty.cryptographic === true.
 */

export function trustClassLabel(trustClass) {
  if (trustClass === 'c2pa_signed') {
    return 'Content Credentials (signed)';
  }
  if (trustClass === 'unavailable') {
    return 'Unavailable parent';
  }
  return 'Studio metadata (not cryptographically signed)';
}

export function honestyHeadline(honesty) {
  if (honesty && honesty.cryptographic === true) {
    return 'Cryptographically signed';
  }
  // Even if a nested string mentions c2pa, never claim signed when false.
  return 'Studio metadata (not cryptographically signed)';
}

export function summarizeChainRecords(records) {
  if (!Array.isArray(records) || records.length === 0) {
    return [];
  }
  return records.map((record) => ({
    recordId: record.record_id,
    operation: record.operation,
    artifactKind: record.artifact_kind,
    artifactId: record.artifact_id,
    trustLabel: trustClassLabel(record.trust_class),
    modelId: record.model_id || null,
  }));
}

export function buildCompositionRevisionArtifactRef(revisionId) {
  return {
    kind: 'composition_revision',
    id: revisionId,
  };
}
