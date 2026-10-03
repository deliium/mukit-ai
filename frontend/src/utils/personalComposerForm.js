/**
 * Pure eligibility for a personal composer train form.
 * Does not call the network and does not log composition JSON.
 */

const DISPLAY_NAME_PATTERN = /^[A-Za-z][A-Za-z0-9._-]{0,63}$/;

const TRAIN_REFUSE_MESSAGES = Object.freeze({
  personal_rights_refused: 'That project is not eligible to train on.',
  rights_train_refused: 'That project is not eligible to train on.',
  rights_reference_refused: 'That source cannot be used for reference analysis.',
});

export function provenanceEligible(rights) {
  if (!rights || typeof rights !== 'object') return false;
  // Registry / resolved use_policy wins for client gating.
  if (rights.use_policy === 'reference_only' || rights.use_policy === 'no_training') {
    return false;
  }
  if (rights.use_policy === 'training_allowed') {
    if (rights.ownership_class === 'user_owned') {
      return rights.verification_status === 'attested'
        || rights.verification_status === 'verified'
        || rights.user_owned_attested === true;
    }
    return rights.verification_status !== 'disputed';
  }
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
  if (draft?.use_policy) rights.use_policy = draft.use_policy;
  return rights;
}

/**
 * Merge a GET registry entry into the attestation draft (display + eligibility).
 * Does not invent training_allowed when the row is missing.
 */
export function hydrateRightsDraftFromRegistry(draft, registryEntry) {
  const base = draft && typeof draft === 'object' ? { ...draft } : {
    status: 'unknown',
    user_owned_attested: false,
    license: '',
    license_spdx: '',
    source_url: '',
    source_reference: '',
  };
  if (!registryEntry || typeof registryEntry !== 'object') {
    return base;
  }
  return {
    ...base,
    use_policy: registryEntry.use_policy || null,
    ownership_class: registryEntry.ownership_class || null,
    verification_status: registryEntry.verification_status || null,
    license: registryEntry.license || base.license || '',
    license_spdx: registryEntry.license_spdx || base.license_spdx || '',
    source_url: registryEntry.source_url || base.source_url || '',
    source_reference: registryEntry.source_reference || base.source_reference || '',
    status: registryEntry.legacy_status || base.status || 'unknown',
    user_owned_attested:
      registryEntry.verification_status === 'attested'
      || registryEntry.verification_status === 'verified'
      || base.user_owned_attested === true,
  };
}

export function formatPersonalComposerRefuseMessage(error) {
  const code = error?.code || error?.details?.rights_code || null;
  if (code && TRAIN_REFUSE_MESSAGES[code]) {
    return TRAIN_REFUSE_MESSAGES[code];
  }
  return error?.message || 'Training failed';
}

export function formatReferenceRightsRefuseMessage(error) {
  const code = error?.code || null;
  if (code === 'rights_reference_refused') {
    return 'This reference cannot be used (rights refuse). Attest ownership or pick a permitted source.';
  }
  return error?.message || 'Reference analysis failed';
}
