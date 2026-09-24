/**
 * Mirror constants / JSDoc for audio.alignment.v1 + roundtrip provenance.
 *
 * Stem bindings in v1 are role → track_id only (no durable stem WAV asset ids).
 * Alignment never lives on composition.v2 note events.
 */

export const AUDIO_ALIGNMENT_SCHEMA_VERSION = 'audio.alignment.v1';
export const AUDIO_ROUNDTRIP_PROVENANCE_SCHEMA_VERSION =
  'audio.roundtrip.provenance.v1';
export const AUDIO_ALIGNMENT_BOUND_SCHEMA_VERSION = 'audio.alignment.bound.v1';

/** @typedef {'scaffolding'|'timeline_parametric'|'defaulted'|'user_adjusted'} AudioAlignmentMethod */

/**
 * @typedef {object} AudioAlignmentQuality
 * @property {number} overall_confidence 0..1
 * @property {number} tempo_confidence 0..1
 * @property {number} beat_grid_confidence 0..1
 * @property {number} offset_uncertainty_ms
 * @property {AudioAlignmentMethod} method
 * @property {string[]} issues code-only
 * @property {{stem: string, confidence: number}[]} [stem_qualities]
 */

/**
 * @typedef {object} AudioAlignmentMapParams
 * @property {number} tempo_bpm
 * @property {number} ticks_per_quarter
 * @property {number} downbeat_offset_seconds
 * @property {number} [origin_tick]
 * @property {string} [meter]
 * @property {number|null} [scaffolding_tempo_bpm]
 */

/**
 * @typedef {object} AudioAlignmentStemBinding
 * @property {string} stem role only
 * @property {string} track_id
 * @property {string} [provisional_stem_label]
 */

/**
 * @typedef {object} AudioAlignmentV1
 * @property {'audio.alignment.v1'} schema_version
 * @property {string} source_audio_asset_id
 * @property {string|null} [result_asset_id]
 * @property {string|null} [job_id]
 * @property {string|null} [project_id]
 * @property {string} composition_fingerprint composition.snapshot.v1
 * @property {AudioAlignmentMapParams} map
 * @property {AudioAlignmentStemBinding[]} stem_bindings
 * @property {AudioAlignmentQuality} quality
 * @property {string} created_at
 * @property {false} playable
 */

/**
 * @typedef {object} AudioRoundtripProvenanceV1
 * @property {'audio.roundtrip.provenance.v1'} schema_version
 * @property {string|null} [source_audio_asset_id]
 * @property {string|null} [source_sha256_prefix]
 * @property {string|null} [result_asset_id]
 * @property {string|null} [alignment_asset_id]
 * @property {string|null} [recovery_job_id]
 * @property {string|null} [composition_fingerprint]
 * @property {string|null} [revision_id]
 * @property {string|null} [neural_render_id]
 * @property {string|null} [bound_at]
 * @property {string|null} [rendered_at]
 */

export const AUDIO_ALIGNMENT_ISSUE_CODES = Object.freeze({
  ALIGNMENT_LOW_CONFIDENCE: 'alignment_low_confidence',
  ALIGNMENT_TEMPO_DIVERGED: 'alignment_tempo_diverged',
  ALIGNMENT_MISSING_SOURCE: 'alignment_missing_source',
  ALIGNMENT_REBUILD_FAILED: 'alignment_rebuild_failed',
  ALIGNMENT_DEFAULTED_OFFSET: 'alignment_defaulted_offset',
  ALIGNMENT_SPARSE_BEAT_GRID: 'alignment_sparse_beat_grid',
});

export const AUDIO_ALIGNMENT_ERROR_CODES = Object.freeze({
  MISSING_SOURCE: 'audio_alignment_missing_source',
  REBUILD_FAILED: 'audio_alignment_rebuild_failed',
  NOT_FOUND: 'audio_alignment_not_found',
  INVALID_DOCUMENT: 'audio_alignment_invalid_document',
  BOUND_NOT_FOUND: 'audio_alignment_bound_not_found',
});

/**
 * @param {unknown} value
 * @returns {value is AudioAlignmentV1}
 */
export function isAudioAlignmentV1(value) {
  return (
    value != null &&
    typeof value === 'object' &&
    /** @type {{schema_version?: string}} */ (value).schema_version ===
      AUDIO_ALIGNMENT_SCHEMA_VERSION &&
    /** @type {{playable?: boolean}} */ (value).playable === false
  );
}
