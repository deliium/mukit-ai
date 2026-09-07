/**
 * Persistable project fingerprint for dirty detection / Save skip.
 * Wider than playback compositionRevisionKey — includes metadata PATCH persists.
 */

function normalizeGenerationMeta(generationMeta) {
  if (!generationMeta || typeof generationMeta !== 'object') {
    return null;
  }
  return {
    provider: generationMeta.provider ?? null,
    model: generationMeta.model ?? null,
    prompt: generationMeta.prompt ?? null,
  };
}

function normalizeComposition(composition) {
  if (!composition || typeof composition !== 'object') {
    return null;
  }
  return {
    schema_version: composition.schema_version ?? null,
    tempo: composition.tempo ?? null,
    key: composition.key ?? null,
    ticks_per_quarter: composition.ticks_per_quarter ?? null,
    time_signature: composition.time_signature ?? null,
    duration_ticks: composition.duration_ticks ?? null,
    bar_count: composition.bar_count ?? null,
    sections: composition.sections ?? null,
    harmony: composition.harmony ?? null,
    tracks: Array.isArray(composition.tracks)
      ? composition.tracks.map((track) => ({
        id: track?.id ?? null,
        name: track?.name ?? null,
        instrument: track?.instrument ?? null,
        role: track?.role ?? null,
        midi_program: track?.midi_program ?? null,
        channel: track?.channel ?? null,
        volume: track?.volume ?? null,
        events: track?.events ?? null,
      }))
      : [],
  };
}

/**
 * Stable fingerprint of composition + generation meta for project persistence.
 * @param {object|null|undefined} composition
 * @param {object|null|undefined} generationMeta
 * @returns {string}
 */
export function projectPersistRevisionKey(composition, generationMeta = null) {
  try {
    return JSON.stringify({
      composition: normalizeComposition(composition),
      generation: normalizeGenerationMeta(generationMeta),
    });
  } catch (error) {
    console.warn('[projectPersistRevision] Failed to build persist revision key', {
      message: error.message,
    });
    return `fallback:${Date.now()}`;
  }
}
