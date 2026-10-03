/**
 * Frontend twin of locked ship-1 spatial constants (UI validation only).
 * Not an FOA encoder — audition always calls POST .../compile for FOA coeffs.
 * Motion never mutates note ticks or Tone.Transport.bpm.
 */

export const ENGINE_VERSION = 'spatial.preview.v1.0';
export const SCENE_SCHEMA_VERSION = 'spatial.scene.v1';
export const PREVIEW_SCHEMA_VERSION = 'spatial.preview.v1';

export const MAX_SOURCES = 32;
export const MAX_MOTION_KEYFRAMES = 64;

export const D_REF = 1.0;
export const D_MAX = 100.0;
export const DISTANCE_GAIN_MIN = 0.05;
export const DISTANCE_GAIN_MAX = 1.0;

export const AZIMUTH_DEG_MIN = -180.0;
export const AZIMUTH_DEG_MAX = 180.0;
export const ELEVATION_DEG_MIN = -90.0;
export const ELEVATION_DEG_MAX = 90.0;
export const SPREAD_MIN = 0.0;
export const SPREAD_MAX = 1.0;
export const GAIN_MIN = 0.0;
export const GAIN_MAX = 1.0;

export const SOURCE_KINDS = Object.freeze(['track', 'stem']);

export const CATALOG_PRESET_IDS = Object.freeze([
  'front_stereo',
  'circle_ensemble',
  'close_intimate',
]);

export const STEM_SET_FINGERPRINT_HEX_LEN = 64;
