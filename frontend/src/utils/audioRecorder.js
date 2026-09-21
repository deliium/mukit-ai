/**
 * Microphone recording → WAV PCM blob for transcription upload.
 * Permission is requested only on start(); never on module import.
 */

import { createAppLogger } from './appLogger.js';

const log = createAppLogger('audioCapture');

export const AUDIO_RECORDER_ERROR_CODES = Object.freeze({
  UNSUPPORTED: 'unsupported',
  PERMISSION_DENIED: 'permission_denied',
  ALREADY_RECORDING: 'already_recording',
  NOT_RECORDING: 'not_recording',
  ENCODE_FAILED: 'encode_failed',
  DURATION_EXCEEDED: 'duration_exceeded',
});

const DEFAULT_MAX_DURATION_MS = 60_000;

/**
 * Encode Float32 mono PCM as a WAV ArrayBuffer.
 */
export function encodeWavPcm(float32Mono, sampleRate) {
  const rate = Math.max(1, Math.round(Number(sampleRate) || 48000));
  const samples = float32Mono instanceof Float32Array
    ? float32Mono
    : new Float32Array(float32Mono || []);
  const dataLength = samples.length * 2;
  const buffer = new ArrayBuffer(44 + dataLength);
  const view = new DataView(buffer);

  const writeString = (offset, text) => {
    for (let i = 0; i < text.length; i += 1) {
      view.setUint8(offset + i, text.charCodeAt(i));
    }
  };

  writeString(0, 'RIFF');
  view.setUint32(4, 36 + dataLength, true);
  writeString(8, 'WAVE');
  writeString(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(36, 'data');
  view.setUint32(40, dataLength, true);

  let offset = 44;
  for (let i = 0; i < samples.length; i += 1) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    offset += 2;
  }
  return buffer;
}

/**
 * Create a recorder that captures via MediaRecorder then re-encodes to WAV
 * through AudioContext (preferred upload format).
 */
export function createAudioRecorder({
  maxDurationMs = DEFAULT_MAX_DURATION_MS,
  globals = typeof globalThis !== 'undefined' ? globalThis : {},
} = {}) {
  let mediaStream = null;
  let mediaRecorder = null;
  let chunks = [];
  let startedAt = 0;
  let maxTimer = null;
  let recording = false;

  async function start() {
    if (recording) {
      return { ok: false, code: AUDIO_RECORDER_ERROR_CODES.ALREADY_RECORDING };
    }
    const nav = globals.navigator;
    if (!nav?.mediaDevices?.getUserMedia || typeof globals.MediaRecorder !== 'function') {
      return { ok: false, code: AUDIO_RECORDER_ERROR_CODES.UNSUPPORTED };
    }
    try {
      mediaStream = await nav.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
        },
      });
    } catch (error) {
      const name = error?.name || '';
      const denied = name === 'NotAllowedError' || name === 'PermissionDeniedError';
      log.warn('Microphone permission failed', {
        code: denied
          ? AUDIO_RECORDER_ERROR_CODES.PERMISSION_DENIED
          : AUDIO_RECORDER_ERROR_CODES.UNSUPPORTED,
        errorName: name,
      });
      return {
        ok: false,
        code: denied
          ? AUDIO_RECORDER_ERROR_CODES.PERMISSION_DENIED
          : AUDIO_RECORDER_ERROR_CODES.UNSUPPORTED,
      };
    }

    chunks = [];
    const mime = globals.MediaRecorder.isTypeSupported?.('audio/webm;codecs=opus')
      ? 'audio/webm;codecs=opus'
      : globals.MediaRecorder.isTypeSupported?.('audio/webm')
        ? 'audio/webm'
        : '';
    mediaRecorder = mime
      ? new globals.MediaRecorder(mediaStream, { mimeType: mime })
      : new globals.MediaRecorder(mediaStream);
    mediaRecorder.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) {
        chunks.push(event.data);
      }
    };
    mediaRecorder.start(250);
    recording = true;
    startedAt = Date.now();
    log.info('Audio recording started', { maxDurationMs });

    if (maxDurationMs > 0) {
      maxTimer = globals.setTimeout?.(() => {
        log.warn('Client max duration reached; stopping', {
          code: AUDIO_RECORDER_ERROR_CODES.DURATION_EXCEEDED,
        });
        stop().catch(() => {});
      }, maxDurationMs);
    }
    return { ok: true };
  }

  async function stop() {
    if (!recording || !mediaRecorder) {
      return { ok: false, code: AUDIO_RECORDER_ERROR_CODES.NOT_RECORDING };
    }
    if (maxTimer && globals.clearTimeout) {
      globals.clearTimeout(maxTimer);
      maxTimer = null;
    }
    const durationMs = Date.now() - startedAt;
    const blob = await new Promise((resolve, reject) => {
      mediaRecorder.onstop = () => {
        resolve(new Blob(chunks, { type: mediaRecorder.mimeType || 'audio/webm' }));
      };
      mediaRecorder.onerror = () => reject(new Error('recorder_error'));
      try {
        mediaRecorder.stop();
      } catch (error) {
        reject(error);
      }
    }).catch((error) => {
      log.error('MediaRecorder stop failed', { errorName: error?.name });
      return null;
    });

    cleanupStream();
    recording = false;

    if (!blob) {
      return { ok: false, code: AUDIO_RECORDER_ERROR_CODES.ENCODE_FAILED, durationMs };
    }

    try {
      const wavBlob = await mediaBlobToWav(blob, globals);
      log.info('Audio recording stopped', {
        durationMs,
        uploadBytes: wavBlob.size,
      });
      return { ok: true, blob: wavBlob, durationMs, mimeType: 'audio/wav' };
    } catch (error) {
      log.error('WAV encode failed', { errorName: error?.name });
      return { ok: false, code: AUDIO_RECORDER_ERROR_CODES.ENCODE_FAILED, durationMs };
    }
  }

  function cancel() {
    if (maxTimer && globals.clearTimeout) {
      globals.clearTimeout(maxTimer);
      maxTimer = null;
    }
    if (mediaRecorder && recording) {
      try {
        mediaRecorder.stop();
      } catch {
        // ignore
      }
    }
    cleanupStream();
    recording = false;
    chunks = [];
    log.info('Audio recording cancelled');
    return { ok: true };
  }

  function cleanupStream() {
    if (mediaStream) {
      for (const track of mediaStream.getTracks()) {
        try {
          track.stop();
        } catch {
          // ignore
        }
      }
    }
    mediaStream = null;
    mediaRecorder = null;
  }

  function isRecording() {
    return recording;
  }

  return { start, stop, cancel, isRecording };
}

async function mediaBlobToWav(blob, globals) {
  const AudioCtx = globals.AudioContext || globals.webkitAudioContext;
  if (!AudioCtx) {
    throw new Error('AudioContext unavailable');
  }
  const ctx = new AudioCtx();
  try {
    const arrayBuffer = await blob.arrayBuffer();
    const decoded = await ctx.decodeAudioData(arrayBuffer.slice(0));
    const channel = decoded.numberOfChannels > 0
      ? decoded.getChannelData(0)
      : new Float32Array(0);
    // Downmix if multi-channel
    let mono = channel;
    if (decoded.numberOfChannels > 1) {
      mono = new Float32Array(decoded.length);
      for (let ch = 0; ch < decoded.numberOfChannels; ch += 1) {
        const data = decoded.getChannelData(ch);
        for (let i = 0; i < data.length; i += 1) {
          mono[i] += data[i] / decoded.numberOfChannels;
        }
      }
    }
    const wav = encodeWavPcm(mono, decoded.sampleRate);
    return new Blob([wav], { type: 'audio/wav' });
  } finally {
    await ctx.close?.();
  }
}
