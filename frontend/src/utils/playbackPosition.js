/**
 * Playback transport/position helpers for Composition V1.
 */

export function barDurationTicks(timeSignature, ticksPerQuarter) {
  if (!timeSignature || typeof timeSignature !== 'string') {
    return null;
  }
  const [numerator, denominator] = timeSignature.split('/').map(Number);
  if (!Number.isInteger(numerator) || !Number.isInteger(denominator) || numerator <= 0 || denominator <= 0) {
    return null;
  }
  if (![1, 2, 4, 8, 16, 32].includes(denominator)) {
    return null;
  }
  const ticks = numerator * ticksPerQuarter * (4 / denominator);
  return Number.isInteger(ticks) ? ticks : null;
}

export function secondsToPlaybackPosition(seconds, {
  tempo = 100,
  ticksPerQuarter = 480,
  timeSignature = '4/4',
} = {}) {
  const safeSeconds = Math.max(0, Number(seconds) || 0);
  const safeTempo = Number(tempo) || 100;
  const safeTpq = Number(ticksPerQuarter) || 480;
  const secondsPerTick = 60 / safeTempo / safeTpq;
  const tick = safeSeconds / secondsPerTick;
  const barTicks = barDurationTicks(timeSignature, safeTpq) || (safeTpq * 4);
  const bar = Math.floor(tick / barTicks) + 1;
  const tickInBar = tick - (bar - 1) * barTicks;
  return {
    seconds: safeSeconds,
    tick,
    bar,
    tickInBar,
    barTicks,
  };
}

export function compositionRevisionKey(musicJson) {
  if (!musicJson || typeof musicJson !== 'object') {
    return 'empty';
  }
  try {
    return JSON.stringify({
      schema_version: musicJson.schema_version,
      tempo: musicJson.tempo,
      ticks_per_quarter: musicJson.ticks_per_quarter,
      time_signature: musicJson.time_signature,
      duration_ticks: musicJson.duration_ticks,
      tracks: Array.isArray(musicJson.tracks)
        ? musicJson.tracks.map((track) => ({
          id: track.id,
          volume: track.volume,
          events: track.events,
        }))
        : [],
    });
  } catch (error) {
    console.warn('[playbackPosition] Failed to build composition revision key', { message: error.message });
    return `fallback:${Date.now()}`;
  }
}
