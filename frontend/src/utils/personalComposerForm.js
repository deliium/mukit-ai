/**
 * Pure eligibility for a personal composer train form.
 * Does not call the network and does not log composition JSON.
 */

const DISPLAY_NAME_PATTERN = /^[A-Za-z][A-Za-z0-9._-]{0,63}$/;

export function provenanceEligible(rights) {
  if (!rights || typeof rights !== 'object') return false;
  const status = rights.status;
  if (status === 'user_owned') return rights.user_owned_attested === true;
  if (status === 'verified_redistributable') {
    const license = Boolean(rights.license || rights.license_spdx);
    const source = Boolean(rights.source_url || rights.source_reference);
    return license && source;
  }
  if (status === 'public_domain') {
    return Boolean(rights.source_url || rights.source_reference);
  }
  return false;
}

export function selectionEligible(projectIds, rightsById, displayName) {
  if (!Array.isArray(projectIds) || projectIds.length === 0) return false;
  if (!DISPLAY_NAME_PATTERN.test(displayName || '')) return false;
  return projectIds.every((projectId) => provenanceEligible(rightsById?.[projectId]));
}

export function composerOptionFromJob(job) {
  if (!job || job.status !== 'complete') return null;
  if (!job.display_name || !job.registry_model_id) return null;
  return {
    label: job.display_name,
    value: job.registry_model_id,
  };
}

export function rightsForRequest(draft) {
  const status = draft?.status || 'unknown';
  const rights = { status };
  if (status === 'user_owned') {
    rights.user_owned_attested = draft?.user_owned_attested === true;
  }
  if (draft?.license) rights.license = draft.license;
  if (draft?.license_spdx) rights.license_spdx = draft.license_spdx;
  if (draft?.source_url) rights.source_url = draft.source_url;
  if (draft?.source_reference) rights.source_reference = draft.source_reference;
  return rights;
}
