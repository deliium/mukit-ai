/**
 * Optional UMP transport — ship-subset MIDI 2.0 Channel Voice decode only.
 * No WASM / full UMP stack. Activate only when capability.transport === ump_experimental
 * or when callers pass simulated packets in tests.
 */

import { createAppLogger } from '../appLogger.js';
import { MIDI_CC_SUSTAIN, MIDI_SUSTAIN_ON_THRESHOLD } from '../midiInputMessages.js';
import {
  EXPRESSIVE_EVENT_KINDS,
  PRESSURE_SCOPES,
  UMP_MIDI2_OPCODE,
  UMP_MT_MIDI2_CHANNEL_VOICE,
  UMP_SHIP_SUBSET_KINDS,
} from './constants.js';
import { promoteUmpVelocityToU16 } from './velocity.js';

const log = createAppLogger('midiExpressive');

/**
 * @typedef {{
 *   decodeWords: (words: ArrayLike<number>, options?: { atMs?: number }) => import('./schemas.js').MidiExpressiveEventV1[],
 * }} UmpTransport
 */

/**
 * @param {number} word
 * @returns {{ mt: number, group: number, status: number, data1: number, data2: number }}
 */
function unpackUmpWord0(word) {
  const w = word >>> 0;
  return {
    mt: (w >>> 28) & 0xf,
    group: (w >>> 24) & 0xf,
    status: (w >>> 16) & 0xff,
    data1: (w >>> 8) & 0xff,
    data2: w & 0xff,
  };
}

/**
 * Build a 2-word MIDI 2.0 Channel Voice UMP for tests / simulation.
 *
 * @param {{
 *   opcode: number,
 *   channel?: number,
 *   group?: number,
 *   data1?: number,
 *   data2?: number,
 *   velocityU16?: number,
 *   data32?: number,
 * }} fields
 * @returns {[number, number]}
 */
export function buildSimulatedMidi2ChannelVoiceUmp(fields) {
  const group = Math.min(15, Math.max(0, Number(fields.group) || 0));
  const channel = Math.min(15, Math.max(0, Number(fields.channel) || 0));
  const opcode = fields.opcode & 0xf;
  const status = ((opcode & 0xf) << 4) | (channel & 0xf);
  const data1 = (Number(fields.data1) || 0) & 0xff;
  const data2 = (Number(fields.data2) || 0) & 0xff;
  const word0 =
    ((UMP_MT_MIDI2_CHANNEL_VOICE & 0xf) << 28)
    | ((group & 0xf) << 24)
    | ((status & 0xff) << 16)
    | ((data1 & 0xff) << 8)
    | (data2 & 0xff);
  let word1 = 0;
  if (fields.velocityU16 != null) {
    word1 = ((Number(fields.velocityU16) & 0xffff) << 16) >>> 0;
  } else if (fields.data32 != null) {
    word1 = Number(fields.data32) >>> 0;
  }
  return [word0 >>> 0, word1 >>> 0];
}

/**
 * Decode ship-subset UMP words into expressive events.
 *
 * @param {ArrayLike<number>} words
 * @param {{ atMs?: number }} [options]
 * @returns {import('./schemas.js').MidiExpressiveEventV1[]}
 */
export function decodeUmpShipSubset(words, options = {}) {
  const atMs = options.atMs;
  const list = Array.from(words || [], (w) => Number(w) >>> 0);
  /** @type {import('./schemas.js').MidiExpressiveEventV1[]} */
  const out = [];
  let i = 0;
  while (i < list.length) {
    const word0 = list[i];
    const head = unpackUmpWord0(word0);
    if (head.mt !== UMP_MT_MIDI2_CHANNEL_VOICE) {
      out.push({
        kind: EXPRESSIVE_EVENT_KINDS.IGNORED,
        reason: 'ump_mt_unsupported',
        status: head.mt,
        ...(atMs != null ? { at_ms: atMs } : {}),
      });
      i += 1;
      continue;
    }
    if (i + 1 >= list.length) {
      out.push({
        kind: EXPRESSIVE_EVENT_KINDS.IGNORED,
        reason: 'ump_short_packet',
        status: head.status,
        ...(atMs != null ? { at_ms: atMs } : {}),
      });
      break;
    }
    const word1 = list[i + 1];
    i += 2;
    const opcode = (head.status >>> 4) & 0xf;
    const channel = head.status & 0xf;
    const note = head.data1 & 0x7f;

    if (opcode === UMP_MIDI2_OPCODE.NOTE_ON) {
      const velocityU16 = promoteUmpVelocityToU16((word1 >>> 16) & 0xffff);
      if (velocityU16 === 0) {
        out.push({
          kind: EXPRESSIVE_EVENT_KINDS.NOTE_OFF,
          note,
          velocity_u16: 0,
          channel,
          group: head.group,
          ...(atMs != null ? { at_ms: atMs } : {}),
        });
      } else {
        out.push({
          kind: EXPRESSIVE_EVENT_KINDS.NOTE_ON,
          note,
          velocity_u16: velocityU16,
          channel,
          group: head.group,
          ...(atMs != null ? { at_ms: atMs } : {}),
        });
      }
      continue;
    }

    if (opcode === UMP_MIDI2_OPCODE.NOTE_OFF) {
      out.push({
        kind: EXPRESSIVE_EVENT_KINDS.NOTE_OFF,
        note,
        velocity_u16: promoteUmpVelocityToU16((word1 >>> 16) & 0xffff),
        channel,
        group: head.group,
        ...(atMs != null ? { at_ms: atMs } : {}),
      });
      continue;
    }

    if (opcode === UMP_MIDI2_OPCODE.POLY_PRESSURE) {
      const raw = (word1 >>> 16) & 0xffff;
      out.push({
        kind: EXPRESSIVE_EVENT_KINDS.PRESSURE,
        scope: PRESSURE_SCOPES.POLY,
        note,
        value: raw / 65535,
        channel,
        group: head.group,
        ...(atMs != null ? { at_ms: atMs } : {}),
      });
      continue;
    }

    if (opcode === UMP_MIDI2_OPCODE.CONTROL_CHANGE) {
      const controller = note;
      const valueU32 = word1 >>> 0;
      const midi7 = (valueU32 >>> 25) & 0x7f;
      /** @type {import('./schemas.js').MidiExpressiveControlChangeEvent} */
      const event = {
        kind: EXPRESSIVE_EVENT_KINDS.CONTROL_CHANGE,
        controller,
        value_u32: valueU32,
        channel,
        group: head.group,
        ...(atMs != null ? { at_ms: atMs } : {}),
      };
      if (controller === MIDI_CC_SUSTAIN) {
        event.sustain = midi7 >= MIDI_SUSTAIN_ON_THRESHOLD;
      }
      out.push(event);
      continue;
    }

    if (opcode === UMP_MIDI2_OPCODE.CHANNEL_PRESSURE) {
      const raw = (word1 >>> 16) & 0xffff;
      out.push({
        kind: EXPRESSIVE_EVENT_KINDS.PRESSURE,
        scope: PRESSURE_SCOPES.CHANNEL,
        value: raw / 65535,
        channel,
        group: head.group,
        ...(atMs != null ? { at_ms: atMs } : {}),
      });
      continue;
    }

    if (opcode === UMP_MIDI2_OPCODE.PITCH_BEND) {
      // 32-bit bipolar; map mid (0x80000000) → 0
      const raw = word1 >>> 0;
      const bend = (raw - 0x80000000) / 0x80000000;
      out.push({
        kind: EXPRESSIVE_EVENT_KINDS.PITCH_BEND,
        bend: Math.max(-1, Math.min(1, bend)),
        channel,
        group: head.group,
        ...(atMs != null ? { at_ms: atMs } : {}),
      });
      continue;
    }

    out.push({
      kind: EXPRESSIVE_EVENT_KINDS.IGNORED,
      reason: 'ump_opcode_unsupported',
      status: head.status,
      ...(atMs != null ? { at_ms: atMs } : {}),
    });
  }

  log.debug('UMP ship-subset decode', {
    wordCount: list.length,
    eventCount: out.length,
    kindCounts: countKinds(out),
    subset: UMP_SHIP_SUBSET_KINDS.length,
  });
  return out;
}

/**
 * @param {import('./schemas.js').MidiExpressiveEventV1[]} events
 */
function countKinds(events) {
  /** @type {Record<string, number>} */
  const counts = {};
  for (const e of events) {
    counts[e.kind] = (counts[e.kind] || 0) + 1;
  }
  return counts;
}

/**
 * @returns {UmpTransport}
 */
export function createUmpTransport() {
  return {
    decodeWords(words, options) {
      return decodeUmpShipSubset(words, options);
    },
  };
}
