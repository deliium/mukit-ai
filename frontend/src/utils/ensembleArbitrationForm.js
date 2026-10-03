/**
 * Ensemble arbitration form clamps and request builders.
 * Preference/critic scores are not musical truth.
 */

export const ENSEMBLE_HARD_MAX_MODELS = 4;
export const ENSEMBLE_MIN_MODELS = 2;
export const ENSEMBLE_SELECTION_MODES = Object.freeze([
  'auto_suggest',
  'human',
  'top_n',
]);

/**
 * @param {string[]} modelIds
 * @param {number} [maxModels=3]
 */
export function clampEnsembleModelIds(modelIds, maxModels = 3) {
  const ceiling = Math.min(
    ENSEMBLE_HARD_MAX_MODELS,
    Math.max(ENSEMBLE_MIN_MODELS, Number(maxModels) || 3),
  );
  const seen = new Set();
  const cleaned = [];
  for (const raw of Array.isArray(modelIds) ? modelIds : []) {
    const id = String(raw || '').trim();
    if (!id || seen.has(id)) continue;
    seen.add(id);
    cleaned.push(id);
    if (cleaned.length >= ceiling) break;
  }
  return cleaned;
}

/**
 * @param {number|string} topN
 * @param {number} modelCount
 */
export function clampEnsembleTopN(topN, modelCount) {
  const ceiling = Math.min(
    ENSEMBLE_HARD_MAX_MODELS,
    Math.max(1, Number(modelCount) || 1),
  );
  const parsed = Number.parseInt(String(topN), 10);
  if (!Number.isFinite(parsed) || parsed < 1) return 1;
  return Math.min(parsed, ceiling);
}

/**
 * @param {string} mode
 */
export function normalizeEnsembleSelectionMode(mode) {
  const text = String(mode || '').trim();
  return ENSEMBLE_SELECTION_MODES.includes(text) ? text : 'human';
}

/**
 * Build a tiny client-supplied composition.plan.v1 from Generate prompt fields.
 * @param {object} prompt
 */
export function buildEnsemblePlanFromPrompt(prompt) {
  const src = prompt && typeof prompt === 'object' ? prompt : {};
  const barCount = Math.max(1, Math.min(512, Number(src.duration_bars) || 8));
  const instruments = Array.isArray(src.instruments) && src.instruments.length
    ? src.instruments.map((item) => String(item).trim()).filter(Boolean)
    : ['piano'];
  const tempoMin = Number(src.tempo_min) || 100;
  const tempoMax = Number(src.tempo_max) || 120;
  const tempo = Math.round((tempoMin + tempoMax) / 2);
  const key = String(src.key || 'C major').trim() || 'C major';
  const timeSignature = String(src.time_signature || '4/4').trim() || '4/4';

  let sections = Array.isArray(src.sections) && src.sections.length
    ? src.sections.map((section) => ({
      type: String(section.type || 'verse'),
      start_bar: Number(section.start_bar) || 1,
      bar_count: Number(section.bar_count) || 1,
    }))
    : null;
  if (!sections) {
    const intro = Math.max(1, Math.floor(barCount / 4));
    const outro = Math.max(1, Math.floor(barCount / 4));
    const mid = Math.max(1, barCount - intro - outro);
    sections = [
      { type: 'intro', start_bar: 1, bar_count: intro },
      { type: 'verse', start_bar: intro + 1, bar_count: mid },
      { type: 'outro', start_bar: intro + mid + 1, bar_count: outro },
    ];
  }

  return {
    schema_version: 'composition.plan.v1',
    form: {
      tempo,
      key,
      time_signature: timeSignature,
      bar_count: barCount,
      sections,
      instrumentation: instruments,
    },
    instrumentation: {
      hints: instruments.slice(0, 4).map((family, index) => ({
        family,
        role: index === 0 ? 'melody' : index === 1 ? 'bass' : 'harmony',
      })),
    },
    density: { global_band: 'moderate', per_section: [] },
  };
}

/**
 * @param {object} prompt
 */
export function buildEnsembleConstraintsFromPrompt(prompt) {
  const src = prompt && typeof prompt === 'object' ? prompt : {};
  const barCount = Math.max(1, Math.min(512, Number(src.duration_bars) || 8));
  const instruments = Array.isArray(src.instruments) && src.instruments.length
    ? src.instruments.map((item) => String(item).trim()).filter(Boolean)
    : ['piano'];
  const key = src.key ? String(src.key).trim() : null;
  return {
    key,
    key_user_specified: Boolean(key),
    time_signature: String(src.time_signature || '4/4'),
    duration_bars: barCount,
    tempo_min: Number(src.tempo_min) || 80,
    tempo_max: Number(src.tempo_max) || 120,
    sections: null,
    sections_user_specified: false,
    required_instrument_families: instruments,
    requested_instruments: instruments,
    allow_extra_instrument_families: true,
    mood: String(src.mood || ''),
    genre: String(src.genre || ''),
    complexity: src.complexity === 'simple' || src.complexity === 'complex'
      ? src.complexity
      : 'moderate',
    has_instructions: Boolean(src.instructions && String(src.instructions).trim()),
    instructions_length: src.instructions ? String(src.instructions).length : 0,
  };
}

/**
 * @param {object} params
 */
export function buildEnsemblePreviewRequest({
  modelIds,
  selectionMode = 'human',
  topN = 2,
  baseSeed = 0,
  maxModels = 3,
  prompt,
  execution = 'sequential',
}) {
  const ids = clampEnsembleModelIds(modelIds, maxModels);
  if (ids.length < ENSEMBLE_MIN_MODELS) {
    return {
      ok: false,
      code: 'ensemble_model_limit',
      message: `Select at least ${ENSEMBLE_MIN_MODELS} symbolic composers.`,
    };
  }
  const mode = normalizeEnsembleSelectionMode(selectionMode);
  const request = {
    schema_version: 'ensemble.arbitration.request.v1',
    policy: {
      schema_version: 'ensemble.policy.v1',
      model_ids: ids,
      strategy: 'parallel_once',
      selection_mode: mode,
      top_n: clampEnsembleTopN(topN, ids.length),
      base_seed: Number.isFinite(Number(baseSeed)) ? Number(baseSeed) : 0,
      execution: execution === 'parallel' ? 'parallel' : 'sequential',
    },
    plan: buildEnsemblePlanFromPrompt(prompt),
    constraints: buildEnsembleConstraintsFromPrompt(prompt),
  };
  return { ok: true, request };
}
