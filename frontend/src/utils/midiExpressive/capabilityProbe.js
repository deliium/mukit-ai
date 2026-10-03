/**
 * midi.capability.v1 probe — extends Web MIDI support without calling requestMIDIAccess.
 */

import { createAppLogger } from '../appLogger.js';
import {
  midiAccessFailureReason,
  MIDI_SUPPORT_REASONS,
  probeWebMidiSupport,
} from '../midiInputSupport.js';
import {
  isMidiExpressiveEnabled,
  MIDI_CAPABILITY_REASON_CODES,
  MIDI_CAPABILITY_SCHEMA,
  MIDI_TRANSPORT,
  MPE_DEFAULT_ZONE,
  MPE_MAPPING_DEFAULT_ENABLED,
} from './constants.js';

const log = createAppLogger('midiExpressive');

/**
 * Detect a stubbable UMP / MIDI 2.0 Web entry point (almost always absent today).
 * @param {object | null | undefined} [env]
 * @returns {boolean}
 */
export function probeUmpApiPresent(env) {
  if (env && typeof env.hasUmpApi === 'boolean') {
    return env.hasUmpApi;
  }
  const globalObj = typeof globalThis !== 'undefined' ? globalThis : undefined;
  const nav = globalObj?.navigator;
  if (!nav || typeof nav !== 'object') {
    return false;
  }
  // Experimental hooks only — never invent presence from MIDI 1.0 alone.
  if (typeof nav.requestMIDIAccess2 === 'function') {
    return true;
  }
  if (nav.midi && typeof nav.midi === 'object' && nav.midi.ump === true) {
    return true;
  }
  return false;
}

/**
 * Build session-only midi.capability.v1.
 *
 * @param {{
 *   webMidiEnv?: Parameters<typeof probeWebMidiSupport>[0],
 *   hasUmpApi?: boolean,
 *   mpeMappingEnabled?: boolean,
 *   mpeZone?: { master_channel: number, member_channel_low: number, member_channel_high: number },
 *   mpeSource?: 'default' | 'user' | 'device_hint',
 *   expressiveEnv?: Record<string, unknown>,
 * }} [options]
 * @returns {import('./schemas.js').MidiCapabilityV1}
 */
export function probeMidiCapability(options = {}) {
  const expressiveOn = isMidiExpressiveEnabled(options.expressiveEnv);
  const web = probeWebMidiSupport(options.webMidiEnv);
  /** @type {import('./schemas.js').MidiWebMidiStatus} */
  const webMidi = web.reason;
  const umpPresent = probeUmpApiPresent({
    hasUmpApi: options.hasUmpApi,
  });

  /** @type {string[]} */
  const reasonCodes = [webMidi];
  /** @type {import('./schemas.js').MidiTransportId} */
  let transport = MIDI_TRANSPORT.NONE;
  let highResVelocity = false;

  if (!expressiveOn) {
    reasonCodes.push(MIDI_CAPABILITY_REASON_CODES.EXPRESSIVE_DISABLED);
    if (web.supported) {
      transport = MIDI_TRANSPORT.MIDI1_BYTES;
    }
  } else if (web.supported) {
    if (umpPresent) {
      transport = MIDI_TRANSPORT.UMP_EXPERIMENTAL;
      highResVelocity = true;
    } else {
      transport = MIDI_TRANSPORT.MIDI1_BYTES;
      reasonCodes.push(MIDI_CAPABILITY_REASON_CODES.UMP_API_ABSENT);
    }
  } else {
    reasonCodes.push(MIDI_CAPABILITY_REASON_CODES.UMP_API_ABSENT);
  }

  const mpeEnabled = options.mpeMappingEnabled === true;
  const zone = options.mpeZone
    ? {
        master_channel: options.mpeZone.master_channel,
        member_channel_low: options.mpeZone.member_channel_low,
        member_channel_high: options.mpeZone.member_channel_high,
      }
    : {
        master_channel: MPE_DEFAULT_ZONE.master_channel,
        member_channel_low: MPE_DEFAULT_ZONE.member_channel_low,
        member_channel_high: MPE_DEFAULT_ZONE.member_channel_high,
      };
  const mpeSource = options.mpeSource
    || (mpeEnabled ? 'user' : MPE_DEFAULT_ZONE.source);
  if (!mpeEnabled || mpeSource === 'default') {
    reasonCodes.push(MIDI_CAPABILITY_REASON_CODES.MPE_ZONE_DEFAULT);
  }

  /** @type {import('./schemas.js').MidiCapabilityV1} */
  const capability = {
    schema: MIDI_CAPABILITY_SCHEMA,
    web_midi: webMidi,
    transport,
    mpe: {
      eligible: expressiveOn && web.supported,
      zone,
      source: /** @type {'default' | 'user' | 'device_hint'} */ (mpeSource),
    },
    high_res_velocity: highResVelocity,
    reason_codes: [...new Set(reasonCodes)],
  };

  log.debug('midi.capability.v1 probe', {
    web_midi: capability.web_midi,
    transport: capability.transport,
    high_res_velocity: capability.high_res_velocity,
    mpeEligible: capability.mpe.eligible,
    mpeMappingPref: mpeEnabled || MPE_MAPPING_DEFAULT_ENABLED,
    reason_codes: capability.reason_codes,
  });

  return capability;
}

/**
 * Map enableMIDI failure onto capability web_midi status.
 * @param {unknown} error
 * @returns {import('./schemas.js').MidiWebMidiStatus}
 */
export function capabilityWebMidiFromAccessError(error) {
  const reason = midiAccessFailureReason(error);
  if (reason === MIDI_SUPPORT_REASONS.PERMISSION_DENIED) {
    return 'permission_denied';
  }
  if (reason === MIDI_SUPPORT_REASONS.INSECURE_CONTEXT) {
    return 'insecure_context';
  }
  return 'unsupported';
}
