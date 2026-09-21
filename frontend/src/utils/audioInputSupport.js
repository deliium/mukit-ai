/**
 * Feature detection for browser microphone capture.
 * Never requests permission on import / probe — only reports capability.
 */

import { createAppLogger } from './appLogger.js';

const log = createAppLogger('audioCapture');

export const AUDIO_SUPPORT_REASONS = Object.freeze({
  AVAILABLE: 'available',
  UNSUPPORTED: 'unsupported',
  INSECURE_CONTEXT: 'insecure_context',
});

/**
 * Probe mic capture support without calling getUserMedia.
 * @returns {{ supported: boolean, reason: string, mediaRecorder: boolean, getUserMedia: boolean, secureContext: boolean }}
 */
export function probeAudioInputSupport(globals = typeof globalThis !== 'undefined' ? globalThis : {}) {
  const secureContext = Boolean(globals.isSecureContext);
  const nav = globals.navigator || null;
  const hasGetUserMedia = Boolean(
    nav
    && nav.mediaDevices
    && typeof nav.mediaDevices.getUserMedia === 'function',
  );
  const hasMediaRecorder = typeof globals.MediaRecorder === 'function';

  let reason = AUDIO_SUPPORT_REASONS.AVAILABLE;
  let supported = true;
  if (!secureContext) {
    supported = false;
    reason = AUDIO_SUPPORT_REASONS.INSECURE_CONTEXT;
  } else if (!hasGetUserMedia || !hasMediaRecorder) {
    supported = false;
    reason = AUDIO_SUPPORT_REASONS.UNSUPPORTED;
  }

  const result = {
    supported,
    reason,
    mediaRecorder: hasMediaRecorder,
    getUserMedia: hasGetUserMedia,
    secureContext,
  };
  log.info('Audio input support probed', result);
  return result;
}

export const AUDIO_ACCEPT_EXTENSIONS = Object.freeze([
  '.wav',
  '.flac',
  '.ogg',
  '.oga',
  '.mp3',
]);

export function isAcceptedAudioFilename(name) {
  const lower = String(name || '').toLowerCase();
  return AUDIO_ACCEPT_EXTENSIONS.some((ext) => lower.endsWith(ext));
}
