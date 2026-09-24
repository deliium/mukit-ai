/**
 * Downsample AudioBuffer peaks for lightweight waveform canvases.
 * Cap peak count to bound memory; never log PCM samples.
 */

import { createAppLogger } from './appLogger.js';

const log = createAppLogger('audioAlignment');

/** Soft cap for peak points drawn in the UI. */
export const DEFAULT_MAX_PEAKS = 800;

/**
 * @param {AudioBuffer} audioBuffer
 * @param {{ maxPeaks?: number }} [opts]
 * @returns {{ peaks: Float32Array, durationSeconds: number, peakCount: number }|null}
 */
export function buildWaveformPeaks(audioBuffer, opts = {}) {
  if (!audioBuffer || typeof audioBuffer.getChannelData !== 'function') {
    log.warn('peaks build: missing AudioBuffer');
    return null;
  }
  const maxPeaks = Math.max(32, Math.min(4096, Number(opts.maxPeaks) || DEFAULT_MAX_PEAKS));
  const channel = audioBuffer.getChannelData(0);
  const length = channel.length;
  if (length <= 0) {
    return { peaks: new Float32Array(0), durationSeconds: 0, peakCount: 0 };
  }
  const block = Math.max(1, Math.floor(length / maxPeaks));
  const peakCount = Math.min(maxPeaks, Math.ceil(length / block));
  const peaks = new Float32Array(peakCount);
  for (let i = 0; i < peakCount; i += 1) {
    const start = i * block;
    const end = Math.min(length, start + block);
    let max = 0;
    for (let j = start; j < end; j += 1) {
      const v = Math.abs(channel[j]);
      if (v > max) max = v;
    }
    peaks[i] = max;
  }
  const durationSeconds = Number(audioBuffer.duration) || length / (audioBuffer.sampleRate || 1);
  log.info('peaks build', {
    duration_s: Number(durationSeconds.toFixed(3)),
    peak_count: peakCount,
  });
  return { peaks, durationSeconds, peakCount };
}

/**
 * Decode an already-fetched blob URL into peaks (no new upload).
 * @param {string} blobUrl
 * @param {{ maxPeaks?: number, audioContext?: AudioContext }} [opts]
 */
export async function decodeBlobUrlToPeaks(blobUrl, opts = {}) {
  if (!blobUrl) {
    log.warn('peaks decode: missing blob URL');
    return null;
  }
  try {
    const response = await fetch(blobUrl);
    const arrayBuffer = await response.arrayBuffer();
    const Ctx = opts.audioContext
      || (typeof window !== 'undefined'
        ? (window.AudioContext || window.webkitAudioContext)
        : null);
    if (!Ctx) {
      log.warn('peaks decode: AudioContext unavailable');
      return null;
    }
    const ctx = opts.audioContext || new Ctx();
    const ownContext = !opts.audioContext;
    try {
      const buffer = await ctx.decodeAudioData(arrayBuffer.slice(0));
      return buildWaveformPeaks(buffer, { maxPeaks: opts.maxPeaks });
    } finally {
      if (ownContext && typeof ctx.close === 'function') {
        void ctx.close();
      }
    }
  } catch (error) {
    log.warn('peaks decode failed', { code: error?.name || 'decode_error' });
    return null;
  }
}

/**
 * Map a client X position within a waveform width to source seconds.
 * @param {number} clientX
 * @param {DOMRect} rect
 * @param {number} durationSeconds
 */
export function pointerXToSourceSeconds(clientX, rect, durationSeconds) {
  if (!rect || !Number.isFinite(durationSeconds) || durationSeconds <= 0) {
    return null;
  }
  const x = Math.max(0, Math.min(rect.width, clientX - rect.left));
  return (x / rect.width) * durationSeconds;
}
