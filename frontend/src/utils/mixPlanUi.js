/**
 * Mix assist display helpers. Parameter bounds mirror mix.plan.v1.
 * Master targets are presets, not loudness guarantees.
 */

export const MIX_PLAN_NOT_A_GUARANTEE = 'Not a guarantee';

export const MIX_PLAN_MASTER_TARGETS = Object.freeze([
  {
    id: 'dynamic',
    label: 'Dynamic',
    goal: 'Light bus compression and headroom below 0 dBFS.',
  },
  {
    id: 'streaming',
    label: 'Streaming',
    goal: 'More even level. Loudness aim is about -14 LUFS.',
  },
  {
    id: 'cinematic',
    label: 'Cinematic',
    goal: 'Wider image and a higher send.',
  },
  {
    id: 'demo',
    label: 'Demo',
    goal: 'Conservative sketch, not a release master.',
  },
]);

const AFTER_BOUNDS = Object.freeze({
  gain: [-24, 12],
  eq: [-24, 12],
  automation: [-24, 12],
  pan: [-1, 1],
  send: [0, 1],
  compressor: [1, 8],
  filter: [0, 20000],
});

/**
 * @param {string} opType
 * @param {number} value
 * @returns {number}
 */
export function clampMixPlanAfter(opType, value) {
  const bounds = AFTER_BOUNDS[opType] || [-24, 24];
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return bounds[0];
  }
  return Math.min(bounds[1], Math.max(bounds[0], numeric));
}

/**
 * @param {object} change
 * @returns {string}
 */
export function formatMixPlanChange(change) {
  if (!change || typeof change !== 'object') {
    return '';
  }
  const before = Number(change.before);
  const after = Number(change.after);
  const unit = change.unit || '';
  const target = change.target || 'stem';
  const type = change.type || 'op';
  return `${type} ${target}: ${before} → ${after} ${unit}`.trim();
}

/**
 * Banner when the live stem-set fingerprint diverges from the plan pin.
 * @param {object|null|undefined} plan
 * @param {{ liveFingerprint?: string|null, stemSetFingerprint?: string|null }} live
 * @returns {boolean}
 */
export function isMixPlanStale(plan, live = {}) {
  const pinned = typeof plan?.source_stem_set_fingerprint === 'string'
    ? plan.source_stem_set_fingerprint.trim()
    : '';
  if (!pinned) {
    return false;
  }
  const liveFingerprint = typeof live.liveFingerprint === 'string' ? live.liveFingerprint.trim() : '';
  const stemSetFingerprint = typeof live.stemSetFingerprint === 'string'
    ? live.stemSetFingerprint.trim()
    : '';
  if (liveFingerprint && pinned !== liveFingerprint) {
    return true;
  }
  if (stemSetFingerprint && pinned !== stemSetFingerprint) {
    return true;
  }
  return false;
}

/**
 * Apply an edited after-value onto a plan copy. Digest identity is unchanged.
 * @param {object} plan
 * @param {string} opId
 * @param {number} after
 * @returns {object}
 */
/**
 * Preview body for Mix assist. Phrase chips stay phrase-only.
 * The section-loudness chip sends that observation code and no phrase.
 * @param {{
 *   projectId: string,
 *   stemSetId: string,
 *   masterTarget: string,
 *   phrase?: string,
 *   reportId?: string|null,
 *   observationCodes?: string[],
 *   includeAudioPreview?: boolean,
 * }} input
 * @returns {object}
 */
export function mixAssistPreviewRequest(input) {
  const codes = Array.isArray(input.observationCodes)
    ? input.observationCodes.filter((code) => typeof code === 'string' && code.trim())
    : [];
  const body = {
    project_id: input.projectId,
    stem_set_id: input.stemSetId,
    master_target: input.masterTarget,
    include_audio_preview: input.includeAudioPreview !== false,
  };
  if (codes.length > 0) {
    body.observation_codes = codes;
  } else if (typeof input.phrase === 'string' && input.phrase.trim()) {
    body.phrase = input.phrase;
  }
  if (typeof input.reportId === 'string' && input.reportId.trim()) {
    body.report_id = input.reportId;
  }
  return body;
}

export function editMixPlanAfter(plan, opId, after) {
  const ops = (plan?.ops || []).map((op) => {
    if (op.op_id !== opId) {
      return op;
    }
    return { ...op, after: clampMixPlanAfter(op.type, after) };
  });
  const changes = (plan?.changes || []).map((change) => {
    if (change.op_id !== opId) {
      return change;
    }
    const op = ops.find((item) => item.op_id === opId);
    return { ...change, after: op ? op.after : change.after };
  });
  return { ...plan, ops, changes };
}
