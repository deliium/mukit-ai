/**
 * Typed MIDI message parsers for live performance capture.
 * Parses note on/off and CC64 (sustain). Other messages return kind 'ignored'.
 * Never logs raw byte streams at INFO — callers may DEBUG counts only.
 */

export const MIDI_MESSAGE_KINDS = Object.freeze({
  NOTE_ON: 'note_on',
  NOTE_OFF: 'note_off',
  CONTROL_CHANGE: 'control_change',
  IGNORED: 'ignored',
});

/** Sustain pedal controller number (CC64). */
export const MIDI_CC_SUSTAIN = 64;

/** MIDI sustain pedal on threshold (traditional ≥ 64). */
export const MIDI_SUSTAIN_ON_THRESHOLD = 64;

/**
 * @typedef {{
 *   kind: 'note_on' | 'note_off',
 *   channel: number,
 *   note: number,
 *   velocity: number,
 * }} MidiNoteMessage
 *
 * @typedef {{
 *   kind: 'control_change',
 *   channel: number,
 *   controller: number,
 *   value: number,
 *   sustain?: boolean,
 * }} MidiCcMessage
 *
 * @typedef {{ kind: 'ignored', status?: number }} MidiIgnoredMessage
 *
 * @typedef {MidiNoteMessage | MidiCcMessage | MidiIgnoredMessage} MidiParsedMessage
 */

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
 * Parse a raw MIDI message (status + data bytes).
 * Velocity-0 note-on is normalized to note_off per MIDI spec.
 *
 * @param {Iterable<number> | ArrayLike<number> | null | undefined} data
 * @returns {MidiParsedMessage}
 */
export function parseMidiMessage(data) {
  const bytes = toByteArray(data);
  if (bytes.length < 1) {
    return { kind: MIDI_MESSAGE_KINDS.IGNORED };
  }

  const status = bytes[0];
  // Running status / SysEx / real-time not handled in v1.
  if (status < 0x80 || status >= 0xf0) {
    return { kind: MIDI_MESSAGE_KINDS.IGNORED, status };
  }

  const command = status & 0xf0;
  const channel = status & 0x0f;

  if (command === 0x90) {
    if (bytes.length < 3) {
      return { kind: MIDI_MESSAGE_KINDS.IGNORED, status };
    }
    const note = bytes[1] & 0x7f;
    const velocity = bytes[2] & 0x7f;
    if (velocity === 0) {
      return {
        kind: MIDI_MESSAGE_KINDS.NOTE_OFF,
        channel,
        note,
        velocity: 0,
      };
    }
    return {
      kind: MIDI_MESSAGE_KINDS.NOTE_ON,
      channel,
      note,
      velocity,
    };
  }

  if (command === 0x80) {
    if (bytes.length < 3) {
      return { kind: MIDI_MESSAGE_KINDS.IGNORED, status };
    }
    return {
      kind: MIDI_MESSAGE_KINDS.NOTE_OFF,
      channel,
      note: bytes[1] & 0x7f,
      velocity: bytes[2] & 0x7f,
    };
  }

  if (command === 0xb0) {
    if (bytes.length < 3) {
      return { kind: MIDI_MESSAGE_KINDS.IGNORED, status };
    }
    const controller = bytes[1] & 0x7f;
    const value = bytes[2] & 0x7f;
    /** @type {MidiCcMessage} */
    const message = {
      kind: MIDI_MESSAGE_KINDS.CONTROL_CHANGE,
      channel,
      controller,
      value,
    };
    if (controller === MIDI_CC_SUSTAIN) {
      message.sustain = value >= MIDI_SUSTAIN_ON_THRESHOLD;
    }
    return message;
  }

  return { kind: MIDI_MESSAGE_KINDS.IGNORED, status };
}

/**
 * Clamp MIDI velocity into composition.v2 range [1, 127].
 * @param {number} velocity
 * @returns {number}
 */
export function clampMidiVelocity(velocity) {
  const n = Number(velocity);
  if (!Number.isFinite(n) || n <= 0) {
    return 1;
  }
  return Math.min(127, Math.max(1, Math.round(n)));
}

/**
 * @param {MidiParsedMessage} message
 * @returns {boolean}
 */
export function isSustainControlChange(message) {
  return Boolean(
    message
    && message.kind === MIDI_MESSAGE_KINDS.CONTROL_CHANGE
    && message.controller === MIDI_CC_SUSTAIN
    && typeof message.sustain === 'boolean',
  );
}
