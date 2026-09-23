/**
 * Transport-synced beat/bar clock for co-performance.
 *
 * Derives { seconds, tick, bar, beatInBar, tickInBar } from Transport seconds
 * + composition timeline via secondsToPlaybackPosition / compileTimeline.
 * Prefer sampling engine.getPlaybackPosition() when an engine is attached.
 * Never mutates composition.v2.
 */

import { createAppLogger } from './appLogger.js';
import { parseTimeSignature } from './compositionTimeline.js';
import { secondsToPlaybackPosition } from './playbackPosition.js';

const log = createAppLogger('liveTransport');

/** @type {number} */
let lastLoggedBar = -1;

/**
 * @param {number} transportSeconds
 * @param {object|null|undefined} composition
 * @param {{ tempo?: number, ticksPerQuarter?: number, timeSignature?: string }} [fallback]
 */
export function getLiveClock(transportSeconds, composition, fallback = {}) {
  const seconds = Math.max(0, Number(transportSeconds) || 0);
  const tempo = Number(composition?.tempo) || Number(fallback.tempo) || 100;
  const ticksPerQuarter =
    Number(composition?.ticks_per_quarter) || Number(fallback.ticksPerQuarter) || 480;
  const timeSignature =
    composition?.time_signature || fallback.timeSignature || '4/4';

  const position = secondsToPlaybackPosition(seconds, {
    composition: composition || null,
    tempo,
    ticksPerQuarter,
    timeSignature,
  });

  const parsed = parseTimeSignature(timeSignature) || { numerator: 4, denominator: 4 };
  const barTicks = Math.max(1, Number(position.barTicks) || ticksPerQuarter * parsed.numerator);
  const ticksPerBeat = barTicks / Math.max(1, parsed.numerator);
  const tickInBar = Math.max(0, Number(position.tickInBar) || 0);
  const beatInBar = Math.min(
    parsed.numerator,
    Math.max(1, Math.floor(tickInBar / ticksPerBeat) + 1),
  );
  const tick = Math.max(0, Math.round(Number(position.tick) || 0));
  const bar = Math.max(1, Math.round(Number(position.bar) || 1));

  if (bar !== lastLoggedBar) {
    lastLoggedBar = bar;
    log.debug('clock sample', { tick, bar, beatInBar });
  }

  return {
    seconds,
    tick,
    bar,
    beatInBar,
    tickInBar: Math.round(tickInBar),
    barTicks: Math.round(barTicks),
    ticksPerBeat: Math.round(ticksPerBeat),
    tempo,
    timeSignature,
  };
}

/**
 * Prefer engine-authoritative position when available.
 * @param {{ getPlaybackPosition?: () => object, getPositionSeconds?: () => number }|null|undefined} engine
 * @param {object|null|undefined} composition
 */
export function getLiveClockFromEngine(engine, composition) {
  if (engine && typeof engine.getPlaybackPosition === 'function') {
    const pos = engine.getPlaybackPosition();
    const seconds =
      typeof pos?.seconds === 'number'
        ? pos.seconds
        : typeof engine.getPositionSeconds === 'function'
          ? engine.getPositionSeconds()
          : 0;
    // Re-derive beatInBar from tick so clock shape stays consistent.
    return getLiveClock(seconds, composition || null, {
      tempo: composition?.tempo,
      ticksPerQuarter: composition?.ticks_per_quarter,
      timeSignature: composition?.time_signature,
    });
  }
  const seconds =
    engine && typeof engine.getPositionSeconds === 'function'
      ? engine.getPositionSeconds()
      : 0;
  return getLiveClock(seconds, composition);
}

/** Test helper — reset throttled bar log. */
export function resetLiveClockLogThrottleForTests() {
  lastLoggedBar = -1;
}
