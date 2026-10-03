/**
 * MIDI 1.0 byte-stream → midi.expressive.event.v1 adapter.
 * Expands note on/off, pitch bend, channel/poly pressure, and all CC (not only CC64).
 */

import {
  MIDI_CC_SUSTAIN,
  MIDI_MESSAGE_KINDS,
  MIDI_SUSTAIN_ON_THRESHOLD,
  parseMidiMessage,
} from '../midiInputMessages.js';
import { EXPRESSIVE_EVENT_KINDS, PRESSURE_SCOPES } from './constants.js';
import { promoteMidi1VelocityToU16 } from './velocity.js';

/**
 * @param {Iterable<number> | ArrayLike<number> | null | undefined} data
 * @returns {number[]}
 */
function toByteArray(data) {
  if (data == null) {
    return [];
  }
  if (Array.isArray(data)) {
    return data.map((b) => Number(b) & 0xff);
  }
  try {
    return Array.from(data, (b) => Number(b) & 0xff);
  } catch {
    return [];
  }
}

/**
 * Normalize 14-bit pitch bend bytes to bend in [-1, 1] (center 8192 → 0).
 * @param {number} lsb
 * @param {number} msb
 * @returns {number}
 */
export function pitchBendBytesToNormalized(lsb, msb) {
  const value = ((msb & 0x7f) << 7) | (lsb & 0x7f);
  return (value - 8192) / 8192;
}

/**
 * 7-bit controller → value_u32 (left-aligned into 32-bit style: midi7 << 25).
 * @param {number} midi7
 * @returns {number}
 */
export function promoteMidi1CcToU32(midi7) {
  const n = Math.min(127, Math.max(0, Math.round(Number(midi7) || 0)));
  return n << 25;
}

/**
 * Parse MIDI 1.0 bytes into an expressive event (legacy-compatible kinds when restricted).
 *
 * @param {Iterable<number> | ArrayLike<number> | null | undefined} data
 * @param {{ legacyOnly?: boolean, atMs?: number }} [options]
 * @returns {import('./schemas.js').MidiExpressiveEventV1}
 */
export function adaptMidi1BytesToExpressive(data, options = {}) {
  const legacyOnly = options.legacyOnly === true;
  const atMs = options.atMs;
  const bytes = toByteArray(data);

  if (legacyOnly) {
    const legacy = parseMidiMessage(bytes);
    if (legacy.kind === MIDI_MESSAGE_KINDS.NOTE_ON) {
      return {
        kind: EXPRESSIVE_EVENT_KINDS.NOTE_ON,
        note: legacy.note,
        velocity_u16: promoteMidi1VelocityToU16(legacy.velocity),
        channel: legacy.channel,
        ...(atMs != null ? { at_ms: atMs } : {}),
      };
    }
    if (legacy.kind === MIDI_MESSAGE_KINDS.NOTE_OFF) {
      return {
        kind: EXPRESSIVE_EVENT_KINDS.NOTE_OFF,
        note: legacy.note,
        velocity_u16: promoteMidi1VelocityToU16(legacy.velocity),
        channel: legacy.channel,
        ...(atMs != null ? { at_ms: atMs } : {}),
      };
    }
    if (
      legacy.kind === MIDI_MESSAGE_KINDS.CONTROL_CHANGE
      && legacy.controller === MIDI_CC_SUSTAIN
    ) {
      return {
        kind: EXPRESSIVE_EVENT_KINDS.CONTROL_CHANGE,
        controller: legacy.controller,
        value_u32: promoteMidi1CcToU32(legacy.value),
        channel: legacy.channel,
        sustain: legacy.sustain,
        ...(atMs != null ? { at_ms: atMs } : {}),
      };
    }
    return {
      kind: EXPRESSIVE_EVENT_KINDS.IGNORED,
      reason: 'legacy_expressive_disabled',
      status: 'status' in legacy ? legacy.status : undefined,
      ...(atMs != null ? { at_ms: atMs } : {}),
    };
  }

  if (bytes.length < 1) {
    return { kind: EXPRESSIVE_EVENT_KINDS.IGNORED, reason: 'empty' };
  }

  const status = bytes[0];
  if (status < 0x80 || status >= 0xf0) {
    return {
      kind: EXPRESSIVE_EVENT_KINDS.IGNORED,
      reason: 'non_channel_voice',
      status,
      ...(atMs != null ? { at_ms: atMs } : {}),
    };
  }

  const command = status & 0xf0;
  const channel = status & 0x0f;

  if (command === 0x90 || command === 0x80) {
    if (bytes.length < 3) {
      return {
        kind: EXPRESSIVE_EVENT_KINDS.IGNORED,
        reason: 'short_note',
        status,
        ...(atMs != null ? { at_ms: atMs } : {}),
      };
    }
    const note = bytes[1] & 0x7f;
    const velocity = bytes[2] & 0x7f;
    if (command === 0x90 && velocity === 0) {
      return {
        kind: EXPRESSIVE_EVENT_KINDS.NOTE_OFF,
        note,
        velocity_u16: 0,
        channel,
        ...(atMs != null ? { at_ms: atMs } : {}),
      };
    }
    if (command === 0x80) {
      return {
        kind: EXPRESSIVE_EVENT_KINDS.NOTE_OFF,
        note,
        velocity_u16: promoteMidi1VelocityToU16(velocity),
        channel,
        ...(atMs != null ? { at_ms: atMs } : {}),
      };
    }
    return {
      kind: EXPRESSIVE_EVENT_KINDS.NOTE_ON,
      note,
      velocity_u16: promoteMidi1VelocityToU16(velocity),
      channel,
      ...(atMs != null ? { at_ms: atMs } : {}),
    };
  }

  if (command === 0xa0) {
    if (bytes.length < 3) {
      return {
        kind: EXPRESSIVE_EVENT_KINDS.IGNORED,
        reason: 'short_poly_pressure',
        status,
        ...(atMs != null ? { at_ms: atMs } : {}),
      };
    }
    return {
      kind: EXPRESSIVE_EVENT_KINDS.PRESSURE,
      scope: PRESSURE_SCOPES.POLY,
      note: bytes[1] & 0x7f,
      value: (bytes[2] & 0x7f) / 127,
      channel,
      ...(atMs != null ? { at_ms: atMs } : {}),
    };
  }

  if (command === 0xb0) {
    if (bytes.length < 3) {
      return {
        kind: EXPRESSIVE_EVENT_KINDS.IGNORED,
        reason: 'short_cc',
        status,
        ...(atMs != null ? { at_ms: atMs } : {}),
      };
    }
    const controller = bytes[1] & 0x7f;
    const value = bytes[2] & 0x7f;
    /** @type {import('./schemas.js').MidiExpressiveControlChangeEvent} */
    const event = {
      kind: EXPRESSIVE_EVENT_KINDS.CONTROL_CHANGE,
      controller,
      value_u32: promoteMidi1CcToU32(value),
      channel,
      ...(atMs != null ? { at_ms: atMs } : {}),
    };
    if (controller === MIDI_CC_SUSTAIN) {
      event.sustain = value >= MIDI_SUSTAIN_ON_THRESHOLD;
    }
    return event;
  }

  if (command === 0xd0) {
    if (bytes.length < 2) {
      return {
        kind: EXPRESSIVE_EVENT_KINDS.IGNORED,
        reason: 'short_channel_pressure',
        status,
        ...(atMs != null ? { at_ms: atMs } : {}),
      };
    }
    return {
      kind: EXPRESSIVE_EVENT_KINDS.PRESSURE,
      scope: PRESSURE_SCOPES.CHANNEL,
      value: (bytes[1] & 0x7f) / 127,
      channel,
      ...(atMs != null ? { at_ms: atMs } : {}),
    };
  }

  if (command === 0xe0) {
    if (bytes.length < 3) {
      return {
        kind: EXPRESSIVE_EVENT_KINDS.IGNORED,
        reason: 'short_pitch_bend',
        status,
        ...(atMs != null ? { at_ms: atMs } : {}),
      };
    }
    return {
      kind: EXPRESSIVE_EVENT_KINDS.PITCH_BEND,
      bend: pitchBendBytesToNormalized(bytes[1], bytes[2]),
      channel,
      ...(atMs != null ? { at_ms: atMs } : {}),
    };
  }

  return {
    kind: EXPRESSIVE_EVENT_KINDS.IGNORED,
    reason: 'unsupported_command',
    status,
    ...(atMs != null ? { at_ms: atMs } : {}),
  };
}
