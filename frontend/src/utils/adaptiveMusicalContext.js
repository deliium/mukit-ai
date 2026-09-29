/** Locked adaptive musical-context example. The server owns dwell and smoothing. */

export const LOCKED_CONTEXT_MAPPING = Object.freeze({
  schema_version: 'adaptive.context.mapping.v1',
  baseline_state_id: 'state-exploration',
  bindings: Object.freeze([
    Object.freeze({
      id: 'bind-danger',
      kind: 'numeric',
      external_key: 'danger',
      slot: 'danger',
      transform: 'identity',
      smooth_alpha: 1,
    }),
  ]),
  state_rules: Object.freeze([
    Object.freeze({
      id: 'rule-combat',
      kind: 'numeric_band',
      slot: 'danger',
      polarity: 'high',
      enter: 0.65,
      exit: 0.35,
      min_dwell_samples: 3,
      target_state_id: 'state-combat',
      priority: 10,
    }),
  ]),
  intensity: Object.freeze({
    slot: 'danger',
    emit_epsilon: 0.02,
  }),
});

export const STREAM_A = Object.freeze(
  Array.from({ length: 20 }, (_item, index) => (index % 2 === 0 ? 0.49 : 0.51)),
);

export const STREAM_B = Object.freeze([0.2, 0.7, 0.7, 0.7]);

export function lockedContextStartBody(expectedDocumentRevision) {
  return {
    expected_document_revision: expectedDocumentRevision,
    mapping: LOCKED_CONTEXT_MAPPING,
  };
}

export function dangerSample(value) {
  return {
    schema_version: 'adaptive.context.external.v1',
    values: { danger: value },
  };
}
