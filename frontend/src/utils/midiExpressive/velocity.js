/**
 * Locked velocity promote / degrade helpers for expressive MIDI.
 * See docs/midi-live-input.md §Expressive MIDI locks.
 */

import {
  MIDI1_VELOCITY_SHIFT,
  MIDI1_VELOCITY_U16_MAX,
  VELOCITY_DEGRADE_DIVISOR,
  VELOCITY_U16_MAX,
} from './constants.js';

/**
 * MIDI 1.0 7-bit → velocity_u16 (`clamp(midi7,0,127) << 9`).
 * @param {number} midi7
 * @returns {number} 0..65024
 */
export function promoteMidi1VelocityToU16(midi7) {
  const n = Number(midi7);
  if (!Number.isFinite(n) || n <= 0) {
    return 0;
  }
  const clamped = Math.min(127, Math.max(0, Math.round(n)));
  return Math.min(MIDI1_VELOCITY_U16_MAX, clamped << MIDI1_VELOCITY_SHIFT);
}

/**
 * Commit degrade: note-on attacks → V2 velocity 1..127.
 * `velocity_u16 == 0` returns 0 (note-off / silent path).
 * @param {number} velocityU16
 * @returns {number} 0 or 1..127
 */
export function degradeVelocityU16ToMidi7(velocityU16) {
  const n = Number(velocityU16);
  if (!Number.isFinite(n) || n <= 0) {
    return 0;
  }
  const u16 = Math.min(VELOCITY_U16_MAX, Math.max(0, Math.round(n)));
  if (u16 === 0) {
    return 0;
  }
  return Math.min(127, Math.max(1, Math.round(u16 / VELOCITY_DEGRADE_DIVISOR)));
}

/**
 * UMP / true 16-bit pass-through into velocity_u16 (no << 9).
 * @param {number} umpVelocityU16
 * @returns {number} 0..65535
 */
export function promoteUmpVelocityToU16(umpVelocityU16) {
  const n = Number(umpVelocityU16);
  if (!Number.isFinite(n) || n <= 0) {
    return 0;
  }
  return Math.min(VELOCITY_U16_MAX, Math.max(0, Math.round(n)));
}

/**
 * Whether a stored velocity_u16 matches the V2 note's degraded velocity.
 * @param {number} velocityU16
 * @param {number} midi7
 * @returns {boolean}
 */
export function velocityU16MatchesDegraded(velocityU16, midi7) {
  return degradeVelocityU16ToMidi7(velocityU16) === Math.round(Number(midi7));
}
