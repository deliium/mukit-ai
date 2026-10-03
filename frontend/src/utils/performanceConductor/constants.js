/**
 * Frontend twin of locked ship-1 conductor constants (UI validation only).
 * Not a realize engine — audition always calls POST .../realize.
 */

export const ENGINE_VERSION = 'performance.conductor.v1.0';
export const PLAN_SCHEMA_VERSION = 'performance.plan.v1';
export const REALIZATION_SCHEMA_VERSION = 'performance.realization.v1';

export const MICROTIMING_MAX_ABS_TICKS = 60;
export const RUBATO_TEMPO_FACTOR_MIN = 0.85;
export const RUBATO_TEMPO_FACTOR_MAX = 1.15;
export const VELOCITY_MIN = 1;
export const VELOCITY_MAX = 127;
export const DURATION_FLOOR_TICKS = 1;

export const CATALOG_PRESET_IDS = Object.freeze([
  'mechanical',
  'restrained',
  'intimate',
  'dramatic',
]);

export const PRESET_IDS = Object.freeze([
  ...CATALOG_PRESET_IDS,
  'custom',
]);

export const TEMPO_RUBATO_DEPTH_MAX = 1.0;
export const TEMPO_RUBATO_RATE_MAX = 4.0;
export const DYNAMICS_STRENGTH_MAX = 1.0;
export const DYNAMICS_CONTRAST_MAX = 1.0;
export const PHRASING_BREATH_GAP_MAX = 120;
export const PHRASING_ARC_MAX = 1.0;
export const ARTICULATION_BIAS_ABS_MAX = 1.0;
export const PEDALING_DEPTH_MAX = 1.0;
export const MICROTIMING_SWING_MAX = 1.0;
export const MICROTIMING_HUMANIZE_MAX = 1.0;
export const ACCENT_STRENGTH_MAX = 1.0;
export const ORCHESTRAL_BALANCE_GAIN_MIN = 0.25;
export const ORCHESTRAL_BALANCE_GAIN_MAX = 1.5;
