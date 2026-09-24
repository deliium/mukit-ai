/**
 * Phase exclusion guards for V4 audio recovery vs MIDI / live / mono audio.
 * Mirror midi↔live exclusivity — recovery upload/record blocked while others capture.
 */

import { createAppLogger } from './appLogger.js';

const log = createAppLogger('audioRecovery');

export const AUDIO_RECOVERY_PHASE_EXCLUSION = 'audio_recovery_phase_exclusion';

/**
 * @param {string|null|undefined} midiPhase
 * @param {string|null|undefined} livePhase
 * @param {string|null|undefined} audioPhase
 * @returns {{ ok: boolean, code?: string, reason?: string }}
 */
export function assertRecoveryAllowedForOtherPhases(midiPhase, livePhase, audioPhase) {
  const midi = String(midiPhase || 'idle');
  if (midi === 'recording' || midi === 'counting_in') {
    log.info('Recovery rejected — midiPhase exclusion', { midiPhase: midi });
    return {
      ok: false,
      code: AUDIO_RECOVERY_PHASE_EXCLUSION,
      reason: 'midi_capturing',
    };
  }
  const live = String(livePhase || 'idle');
  if (live === 'running' || live === 'degraded') {
    log.info('Recovery rejected — livePhase exclusion', { livePhase: live });
    return {
      ok: false,
      code: AUDIO_RECOVERY_PHASE_EXCLUSION,
      reason: 'live_running',
    };
  }
  const audio = String(audioPhase || 'idle');
  if (audio === 'recording' || audio === 'requesting_mic') {
    log.info('Recovery rejected — audioPhase exclusion', { audioPhase: audio });
    return {
      ok: false,
      code: AUDIO_RECOVERY_PHASE_EXCLUSION,
      reason: 'mono_audio_recording',
    };
  }
  return { ok: true };
}

/**
 * Inverse: mono audio recording blocked while recovery is active.
 *
 * @param {string|null|undefined} recoveryPhase
 * @returns {{ ok: boolean, code?: string, reason?: string }}
 */
export function assertMonoAudioAllowedForRecoveryPhase(recoveryPhase) {
  const phase = String(recoveryPhase || 'idle');
  if (
    phase === 'recording'
    || phase === 'requesting_mic'
    || phase === 'uploading'
    || phase === 'running'
  ) {
    log.info('Mono audio rejected — recoveryPhase exclusion', { recoveryPhase: phase });
    return {
      ok: false,
      code: AUDIO_RECOVERY_PHASE_EXCLUSION,
      reason: 'recovery_active',
    };
  }
  return { ok: true };
}

/**
 * Inverse: MIDI capture blocked while recovery is uploading/running/recording.
 *
 * @param {string|null|undefined} recoveryPhase
 */
export function assertMidiCaptureAllowedForRecoveryPhase(recoveryPhase) {
  return assertMonoAudioAllowedForRecoveryPhase(recoveryPhase);
}

/**
 * Inverse: live jam blocked while recovery is active.
 *
 * @param {string|null|undefined} recoveryPhase
 */
export function assertLiveAllowedForRecoveryPhase(recoveryPhase) {
  return assertMonoAudioAllowedForRecoveryPhase(recoveryPhase);
}
