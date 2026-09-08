/**
 * Client-side composition version boundary: classify, migrate V1→V2, reject unsupported.
 */

export const SCHEMA_VERSION_V1 = 'composition.v1';
export const SCHEMA_VERSION_V2 = 'composition.v2';

export class CompositionVersionError extends Error {
  constructor(message, { code = 'unsupported_schema_version', schemaVersion = null } = {}) {
    super(message);
    this.name = 'CompositionVersionError';
    this.code = code;
    this.schemaVersion = schemaVersion;
  }
}

export function classifyCompositionVersion(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return 'unsupported';
  }
  const schemaVersion = value.schema_version;
  if (schemaVersion === SCHEMA_VERSION_V2) {
    return 'v2';
  }
  if (schemaVersion === SCHEMA_VERSION_V1) {
    return 'v1';
  }
  if (schemaVersion !== undefined && schemaVersion !== null) {
    return 'unsupported';
  }
  if (Array.isArray(value.notes) || Array.isArray(value.harmony)) {
    return 'legacy';
  }
  if (Array.isArray(value.tracks) && value.ticks_per_quarter != null) {
    return 'v1';
  }
  return 'legacy';
}

/**
 * Migrate composition.v1 to composition.v2 mirroring backend defaults.
 * Does not mutate the source object.
 */
export function migrateV1ToV2(source) {
  const v1 = structuredClone(source);
  const sectionIdsAdded = [];
  const sections = (Array.isArray(v1.sections) ? v1.sections : []).map((section, index) => ({
    ...section,
    id: section.id ?? `section-${index + 1}`,
    label: section.label ?? null,
  }));
  sections.forEach((section, index) => {
    if (!source.sections?.[index]?.id) {
      sectionIdsAdded.push(section.id);
    }
  });

  const tracks = (Array.isArray(v1.tracks) ? v1.tracks : []).map((track) => ({
    ...track,
    expression: track.expression ?? 127,
    dynamic_marks: Array.isArray(track.dynamic_marks) ? track.dynamic_marks : [],
    sustain_pedals: Array.isArray(track.sustain_pedals) ? track.sustain_pedals : [],
    automation: Array.isArray(track.automation) ? track.automation : [],
    events: (Array.isArray(track.events) ? track.events : []).map((event) => ({
      ...event,
      articulations: Array.isArray(event.articulations) ? event.articulations : [],
      tie: event.tie ?? null,
    })),
  }));

  const v2 = {
    ...v1,
    schema_version: SCHEMA_VERSION_V2,
    sections,
    tracks,
    tempo_changes: Array.isArray(v1.tempo_changes) ? v1.tempo_changes : [],
    time_signature_changes: Array.isArray(v1.time_signature_changes) ? v1.time_signature_changes : [],
    key_changes: Array.isArray(v1.key_changes) ? v1.key_changes : [],
    markers: Array.isArray(v1.markers) ? v1.markers : [],
  };

  console.debug('[compositionVersion] V1 to V2 migration completed', {
    sourceSchemaVersion: SCHEMA_VERSION_V1,
    targetSchemaVersion: SCHEMA_VERSION_V2,
    sectionCount: sections.length,
    trackCount: tracks.length,
    sectionIdsAdded: sectionIdsAdded.length,
  });

  return v2;
}

/**
 * Normalize API/store input to operational composition.v2.
 * @throws {CompositionVersionError}
 */
export function prepareCompositionForStore(raw) {
  const category = classifyCompositionVersion(raw);
  console.debug('[compositionVersion] Classifying composition input', {
    category,
    schemaVersion: raw?.schema_version ?? null,
  });

  if (category === 'v2') {
    return structuredClone(raw);
  }
  if (category === 'v1') {
    console.info('[compositionVersion] Normalizing composition.v1 input to composition.v2');
    return migrateV1ToV2(raw);
  }
  if (category === 'legacy') {
    throw new CompositionVersionError(
      'Legacy JSON requires backend normalization before canonical storage.',
      { code: 'legacy_requires_backend', schemaVersion: null },
    );
  }
  throw new CompositionVersionError(
    `Unsupported schema_version: ${String(raw?.schema_version ?? 'missing')}`,
    { code: 'unsupported_schema_version', schemaVersion: raw?.schema_version ?? null },
  );
}

/**
 * Best-effort normalization for user JSON edits; returns null when migration is impossible.
 */
export function tryPrepareCompositionForStore(raw) {
  try {
    return prepareCompositionForStore(raw);
  } catch (error) {
    if (error instanceof CompositionVersionError) {
      console.warn('[compositionVersion] Composition normalization skipped', {
        code: error.code,
        schemaVersion: error.schemaVersion,
        message: error.message,
      });
      return null;
    }
    throw error;
  }
}
