/**
 * Pure Model Lab wizard helpers. Never starts training.
 */

const DISPLAY_NAME_PATTERN = /^[A-Za-z][A-Za-z0-9._-]{0,63}$/;

export const LAB_STAGES = Object.freeze([
  'dataset',
  'tokenizer',
  'architecture',
  'training',
  'run',
  'evaluation',
  'registry',
]);

export const HARD_MAX_STEPS = 512;
export const HARD_MAX_BATCH = 64;
export const DEFAULT_MAX_STEPS = 64;
export const DEFAULT_MAX_BATCH = 8;

export function clampLabSteps(steps, maxSteps = DEFAULT_MAX_STEPS) {
  const ceiling = Math.min(Math.max(Number(maxSteps) || DEFAULT_MAX_STEPS, 1), HARD_MAX_STEPS);
  const value = Number(steps);
  if (!Number.isFinite(value)) return 1;
  return Math.min(Math.max(Math.trunc(value), 1), ceiling);
}

export function clampLabBatch(batchSize, maxBatch = DEFAULT_MAX_BATCH) {
  const ceiling = Math.min(Math.max(Number(maxBatch) || DEFAULT_MAX_BATCH, 1), HARD_MAX_BATCH);
  const value = Number(batchSize);
  if (!Number.isFinite(value)) return 1;
  return Math.min(Math.max(Math.trunc(value), 1), ceiling);
}

export function displayNameValid(name) {
  return DISPLAY_NAME_PATTERN.test(String(name || ''));
}

export function createEligible(form, status) {
  if (!status?.enabled) return false;
  if (!displayNameValid(form?.displayName)) return false;
  if (!form?.datasetVersionId) return false;
  if (!form?.tokenizerPreset || !form?.architecturePreset) return false;
  const steps = clampLabSteps(form.steps, status.max_steps);
  const batch = clampLabBatch(form.batchSize, status.max_batch);
  return steps >= 1 && batch >= 1;
}

export function registerEligible(experiment, checkpointStep) {
  if (!experiment || experiment.status !== 'complete') return false;
  if (experiment.registry_model_id) return false;
  const step = Number(checkpointStep);
  return Number.isFinite(step) && step >= 0;
}

export function buildCreatePayload(form, status) {
  return {
    schema_version: 'model.lab.create.v1',
    display_name: String(form.displayName || '').trim(),
    dataset_version_id: form.datasetVersionId,
    tokenizer_preset: form.tokenizerPreset || 'core',
    architecture_preset: form.architecturePreset || 'tiny_lab',
    seed: Number.isFinite(Number(form.seed)) ? Math.trunc(Number(form.seed)) : 42,
    train: {
      steps: clampLabSteps(form.steps, status?.max_steps),
      batch_size: clampLabBatch(form.batchSize, status?.max_batch),
      lr: Number.isFinite(Number(form.lr)) ? Number(form.lr) : 0.0003,
      device: form.device || 'cpu',
    },
    eval_enabled: form.evalEnabled === true,
    listening_enabled: form.listeningEnabled === true,
  };
}

export function formatModelLabRefuseMessage(code) {
  const messages = {
    model_lab_disabled: 'Enable MODEL_LAB_ENABLED to train experiments.',
    model_lab_busy: 'Another Model Lab experiment is already running.',
    model_lab_dataset_refused: 'That dataset version cannot be used.',
    rights_train_refused: 'That dataset is not eligible for training.',
    model_lab_payload_refused: 'That request body is not allowed.',
    model_lab_name_taken: 'That display name is already in use.',
    collaboration_role_denied: 'Your studio role cannot train Model Lab experiments.',
  };
  return messages[code] || null;
}
