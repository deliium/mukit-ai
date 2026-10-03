/**
 * Locked expressive MIDI contracts (midi.capability / expressive.event / performance.take).
 * Keep aligned with docs/midi-live-input.md and capability.fixture.json.
 *
 * VITE_MIDI_EXPRESSIVE_ENABLED defaults to true when unset (Task 7 wires .env.example).
 */

export const MIDI_CAPABILITY_SCHEMA = 'midi.capability.v1';
export const MIDI_EXPRESSIVE_EVENT_SCHEMA = 'midi.expressive.event.v1';
export const MIDI_PERFORMANCE_TAKE_SCHEMA = 'midi.performance.take.v1';

/** @typedef {'midi1_bytes' | 'ump_experimental' | 'none'} MidiTransportId */
export const MIDI_TRANSPORT = Object.freeze({
  MIDI1_BYTES: 'midi1_bytes',
  UMP_EXPERIMENTAL: 'ump_experimental',
  NONE: 'none',
});

export const EXPRESSIVE_EVENT_KINDS = Object.freeze({
  NOTE_ON: 'note_on',
  NOTE_OFF: 'note_off',
  PITCH_BEND: 'pitch_bend',
  PRESSURE: 'pressure',
  CONTROL_CHANGE: 'control_change',
  IGNORED: 'ignored',
});

export const PRESSURE_SCOPES = Object.freeze({
  POLY: 'poly',
  CHANNEL: 'channel',
});

/** MIDI 1.0 promote: midi7 << 9 → max 65024 (not 65535). */
export const MIDI1_VELOCITY_SHIFT = 9;
export const MIDI1_VELOCITY_U16_MAX = 127 << MIDI1_VELOCITY_SHIFT; // 65024
export const VELOCITY_U16_MAX = 65535;
export const VELOCITY_DEGRADE_DIVISOR = 512;

/** Default MPE lower zone (1-based channels, matching Architecture decision 4). */
export const MPE_DEFAULT_ZONE = Object.freeze({
  master_channel: 1,
  member_channel_low: 2,
  member_channel_high: 16,
  source: 'default',
});

/** User MPE mapping preference default — off preserves legacy single-channel keyboards. */
export const MPE_MAPPING_DEFAULT_ENABLED = false;

/**
 * note_performances curve / controller caps (Architecture decision 3).
 * Task 5 validators must use these exact numbers.
 */
export const NOTE_PERFORMANCE_MAX_CURVE_POINTS = 32;
export const NOTE_PERFORMANCE_MAX_CONTROLLER_IDS = 8;

/** UMP MIDI 2.0 Channel Voice message type (ship-subset only). */
export const UMP_MT_MIDI2_CHANNEL_VOICE = 0x4;

/**
 * Closed UMP ship-subset kind ids — no full UMP/WASM stack.
 * @type {readonly string[]}
 */
export const UMP_SHIP_SUBSET_KINDS = Object.freeze([
  'ump_midi2_note_on',
  'ump_midi2_note_off',
  'ump_midi2_poly_pressure',
  'ump_midi2_control_change',
  'ump_midi2_channel_pressure',
  'ump_midi2_pitch_bend',
]);

/** Opcode nibble for MIDI 2.0 Channel Voice (high nibble of status byte). */
export const UMP_MIDI2_OPCODE = Object.freeze({
  NOTE_OFF: 0x8,
  NOTE_ON: 0x9,
  POLY_PRESSURE: 0xa,
  CONTROL_CHANGE: 0xb,
  CHANNEL_PRESSURE: 0xd,
  PITCH_BEND: 0xe,
});

export const MIDI_CAPABILITY_REASON_CODES = Object.freeze({
  AVAILABLE: 'available',
  UNSUPPORTED: 'unsupported',
  INSECURE_CONTEXT: 'insecure_context',
  PERMISSION_DENIED: 'permission_denied',
  UMP_API_ABSENT: 'ump_api_absent',
  MPE_ZONE_DEFAULT: 'mpe_zone_default',
  EXPRESSIVE_DISABLED: 'expressive_disabled',
});

export const PERFORMANCE_METADATA_DROP_REASON = 'performance_metadata_dropped';
export const SMF_PERFORMANCE_EXPRESSION_OMITTED = 'performance_expression_omitted';

/**
 * Read VITE_MIDI_EXPRESSIVE_ENABLED (default true when unset).
 * @param {Record<string, unknown> | null | undefined} [env]
 * @returns {boolean}
 */
export function isMidiExpressiveEnabled(env) {
  let raw;
  if (env && Object.prototype.hasOwnProperty.call(env, 'VITE_MIDI_EXPRESSIVE_ENABLED')) {
    raw = env.VITE_MIDI_EXPRESSIVE_ENABLED;
  } else {
    try {
      const viteEnv = typeof import.meta !== 'undefined' ? import.meta.env : undefined;
      raw = viteEnv?.VITE_MIDI_EXPRESSIVE_ENABLED;
    } catch {
      raw = undefined;
    }
    if (raw == null && typeof process !== 'undefined' && process.env) {
      raw = process.env.VITE_MIDI_EXPRESSIVE_ENABLED;
    }
  }
  if (raw == null || raw === '') {
    return true;
  }
  const s = String(raw).trim().toLowerCase();
  if (s === '0' || s === 'false' || s === 'no' || s === 'off') {
    return false;
  }
  return true;
}
