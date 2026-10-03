/** Pure Ardour display helper: samples + sample_rate → clock string.

Does not import video scoring modules. Not true Ardour BBT (OSC bit +32 out of scope).
*/

import { createAppLogger } from '../appLogger.js';

const logger = createAppLogger('ardourCompanion.timecode');

/**
 * Format audio samples as HH:MM:SS (optional fractional frames at 30 fps display-only).
 *
 * @param {number|null|undefined} samples
 * @param {number|null|undefined} sampleRate
 * @param {{ includeFrames?: boolean }} [options]
 * @returns {{ display: string, mode: 'clock'|'samples'|'unavailable', seconds: number|null }}
 */
export function formatArdourTimecode(samples, sampleRate, options = {}) {
  const includeFrames = options.includeFrames === true;
  if (samples == null || !Number.isFinite(Number(samples)) || Number(samples) < 0) {
    return { display: '—', mode: 'unavailable', seconds: null };
  }
  const sampleCount = Math.floor(Number(samples));
  const rate = Number(sampleRate);
  if (!Number.isFinite(rate) || rate <= 0) {
    logger.debug('samples only', { reason: 'sample_rate_missing' });
    return {
      display: `${sampleCount} samples`,
      mode: 'samples',
      seconds: null,
    };
  }
  const totalSeconds = sampleCount / rate;
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = Math.floor(totalSeconds % 60);
  const hh = String(hours).padStart(2, '0');
  const mm = String(minutes).padStart(2, '0');
  const ss = String(seconds).padStart(2, '0');
  let display = `${hh}:${mm}:${ss}`;
  if (includeFrames) {
    const fractional = totalSeconds - Math.floor(totalSeconds);
    const frames = Math.min(29, Math.floor(fractional * 30));
    display = `${display}:${String(frames).padStart(2, '0')}`;
  }
  return { display, mode: 'clock', seconds: totalSeconds };
}
