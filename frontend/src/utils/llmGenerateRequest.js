/**
 * Build POST /llm/generate-music-json request body from Music Generator form state.
 * Kept free of React so node:test can cover pipeline options without mounting the UI.
 */

export function buildLlmRequest(prompt, selectedProvider, selectedModel, pipelineOptions = {}) {
  const pipeline = pipelineOptions.pipeline || 'llm_only';
  const options = {
    max_retries: 1,
    pipeline,
  };
  if (
    pipeline === 'hybrid_plan_symbolic'
    || pipeline === 'symbolic_continuation'
    || pipeline === 'symbolic_variation'
  ) {
    const rawSeed = pipelineOptions.seed;
    if (rawSeed !== '' && rawSeed != null && Number.isFinite(Number(rawSeed))) {
      options.seed = Number(rawSeed);
    }
  }
  const profileStrength = pipelineOptions.profileStrength || 'off';
  const profileId = pipelineOptions.profileId || null;
  const body = {
    selection: {
      provider: selectedProvider || null,
      model: selectedModel || null,
    },
    options,
    prompt: {
      genre: prompt.genre,
      mood: prompt.mood,
      key: prompt.key || null,
      time_signature: prompt.time_signature,
      tempo_min: Number(prompt.tempo_min),
      tempo_max: Number(prompt.tempo_max),
      instruments: prompt.instruments
        .split(',')
        .map((instrument) => instrument.trim())
        .filter(Boolean),
      sections: parseSections(prompt.sections),
      complexity: prompt.complexity,
      duration_bars: Number(prompt.duration_bars),
      instructions: prompt.instructions || null,
    },
    profile_strength: profileStrength,
  };
  if (profileId && profileStrength !== 'off') {
    body.profile_id = profileId;
  }
  return body;
}

export function parseSections(value) {
  return value
    .split(',')
    .map((section) => {
      const [type, bars] = section.split(':').map((part) => part.trim());
      return type && bars ? { type, bars: Number(bars) } : null;
    })
    .filter(Boolean);
}
