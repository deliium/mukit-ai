/**
 * Web MIDI capability probe (no side effects on import).
 *
 * Browser caveats (details in docs/midi-live-input.md):
 * - Chrome / Edge / Opera: Web MIDI generally available on secure contexts.
 * - Firefox: may require about:config flag or is unsupported depending on version.
 * - Safari: historically limited / experimental; treat missing API as unsupported.
 * - Non-secure contexts (HTTP except localhost): requestMIDIAccess is unavailable.
 *
 * Never call requestMIDIAccess from this module — probe only.
 */

import { createAppLogger } from './appLogger.js';

const log = createAppLogger('midiInput');

/** @typedef {'unsupported' | 'insecure_context' | 'permission_denied' | 'available'} MidiSupportReason */

export const MIDI_SUPPORT_REASONS = Object.freeze({
  UNSUPPORTED: 'unsupported',
  INSECURE_CONTEXT: 'insecure_context',
  PERMISSION_DENIED: 'permission_denied',
  AVAILABLE: 'available',
});

/**
 * @param {{ isSecureContext?: boolean, hasRequestMidiAccess?: boolean } | null | undefined} [env]
 * @returns {{ supported: boolean, reason: MidiSupportReason }}
 */
export function probeWebMidiSupport(env) {
  const globalObj = typeof globalThis !== 'undefined' ? globalThis : undefined;
  const secure =
    env && typeof env.isSecureContext === 'boolean'
      ? env.isSecureContext
      : Boolean(globalObj && globalObj.isSecureContext);
  const hasApi =
    env && typeof env.hasRequestMidiAccess === 'boolean'
      ? env.hasRequestMidiAccess
      : Boolean(
          globalObj
          && globalObj.navigator
          && typeof globalObj.navigator.requestMIDIAccess === 'function',
        );

  /** @type {MidiSupportReason} */
  let reason;
  if (!secure) {
    reason = MIDI_SUPPORT_REASONS.INSECURE_CONTEXT;
  } else if (!hasApi) {
    reason = MIDI_SUPPORT_REASONS.UNSUPPORTED;
  } else {
    reason = MIDI_SUPPORT_REASONS.AVAILABLE;
  }

  const result = {
    supported: reason === MIDI_SUPPORT_REASONS.AVAILABLE,
    reason,
  };
  log.debug('Web MIDI support probe', {
    supported: result.supported,
    reason: result.reason,
    secure,
    hasApi,
  });
  return result;
}

/**
 * Convenience boolean for UI feature gates.
 * @param {Parameters<typeof probeWebMidiSupport>[0]} [env]
 */
export function isWebMidiSupported(env) {
  return probeWebMidiSupport(env).supported;
}

/**
 * Map a permission / access failure to a stable reason code.
 * @param {unknown} error
 * @returns {MidiSupportReason}
 */
export function midiAccessFailureReason(error) {
  const name = error && typeof error === 'object' && 'name' in error
    ? String(/** @type {{ name?: string }} */ (error).name || '')
    : '';
  if (name === 'NotAllowedError' || name === 'SecurityError') {
    return MIDI_SUPPORT_REASONS.PERMISSION_DENIED;
  }
  if (name === 'NotSupportedError') {
    return MIDI_SUPPORT_REASONS.UNSUPPORTED;
  }
  return MIDI_SUPPORT_REASONS.UNSUPPORTED;
}
