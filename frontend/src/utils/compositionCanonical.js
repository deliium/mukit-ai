/**
 * Deterministic canonical serialization for composition documents.
 * Preserves array order; recursively sorts object keys.
 */

export const SERIALIZATION_ERROR_KEY = 'error:serialization';

export function canonicalizeValue(value) {
  if (value === null || typeof value !== 'object') {
    return value;
  }
  if (Array.isArray(value)) {
    return value.map((item) => canonicalizeValue(item));
  }
  const sorted = {};
  for (const key of Object.keys(value).sort()) {
    sorted[key] = canonicalizeValue(value[key]);
  }
  return sorted;
}

export function canonicalCompositionSerialization(composition) {
  if (!composition || typeof composition !== 'object' || Array.isArray(composition)) {
    return 'empty';
  }
  try {
    return JSON.stringify(canonicalizeValue(composition));
  } catch (error) {
    console.warn('[compositionCanonical] Failed to serialize composition', {
      message: error.message,
    });
    return SERIALIZATION_ERROR_KEY;
  }
}

/** Full-document revision for audible playback refresh. */
export function audibleRevisionKey(composition) {
  return canonicalCompositionSerialization(composition);
}

/** Full-document revision for MusicXML / notation preview. */
export function notationRevisionKey(composition) {
  return canonicalCompositionSerialization(composition);
}
