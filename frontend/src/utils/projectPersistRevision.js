/**
 * Persistable project fingerprint for dirty detection / Save skip.
 * Uses deterministic full-document canonical serialization.
 */

import { canonicalizeValue, SERIALIZATION_ERROR_KEY } from './compositionCanonical.js';
import { tryPrepareCompositionForStore, SCHEMA_VERSION_V2 } from './compositionVersion.js';
import { ensureCompositionNoteIds, ensureNoteId } from './pianoRollEvents.js';

function normalizeCompositionForPersist(composition) {
  if (!composition) {
    return null;
  }
  const { composition: withIds } = ensureCompositionNoteIds(composition);
  const prepared = tryPrepareCompositionForStore(withIds) ?? withIds;
  if (!prepared || prepared.schema_version !== SCHEMA_VERSION_V2) {
    return prepared;
  }
  return {
    ...prepared,
    tempo_changes: Array.isArray(prepared.tempo_changes) ? prepared.tempo_changes : [],
    time_signature_changes: Array.isArray(prepared.time_signature_changes) ? prepared.time_signature_changes : [],
    key_changes: Array.isArray(prepared.key_changes) ? prepared.key_changes : [],
    markers: Array.isArray(prepared.markers) ? prepared.markers : [],
    sections: (Array.isArray(prepared.sections) ? prepared.sections : []).map((section, index) => ({
      ...section,
      id: section.id ?? `section-${index + 1}`,
      label: section.label ?? null,
    })),
    tracks: (Array.isArray(prepared.tracks) ? prepared.tracks : []).map((track) => ({
      ...track,
      expression: track.expression ?? 127,
      dynamic_marks: Array.isArray(track.dynamic_marks) ? track.dynamic_marks : [],
      sustain_pedals: Array.isArray(track.sustain_pedals) ? track.sustain_pedals : [],
      automation: Array.isArray(track.automation) ? track.automation : [],
      events: (Array.isArray(track.events) ? track.events : []).map((event, index) => {
        const { id } = ensureNoteId(event, { trackId: track.id, index });
        return {
          ...event,
          id,
          type: event.type || 'note',
          articulations: Array.isArray(event.articulations) ? event.articulations : [],
          tie: event.tie ?? null,
        };
      }),
    })),
  };
}

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

/**
 * Stable fingerprint of composition + generation meta for project persistence.
 * @param {object|null|undefined} composition
 * @param {object|null|undefined} generationMeta
 * @returns {string}
 */
export function projectPersistRevisionKey(composition, generationMeta = null) {
  try {
    const normalized = normalizeCompositionForPersist(composition);
    return JSON.stringify({
      composition: normalized ? canonicalizeValue(normalized) : null,
      generation: normalizeGenerationMeta(generationMeta),
    });
  } catch (error) {
    console.warn('[projectPersistRevision] Failed to build persist revision key', {
      message: error.message,
    });
    return SERIALIZATION_ERROR_KEY;
  }
}

export { SERIALIZATION_ERROR_KEY };
