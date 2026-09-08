/**
 * Playback transport/position helpers for Composition V1/V2.
 */

import {
  barAtTick,
  barDurationTicks as timelineBarDurationTicks,
  compileTimeline,
  secondsToTick,
  tickToSeconds,
} from './compositionTimeline.js';

export function barDurationTicks(timeSignature, ticksPerQuarter) {
  return timelineBarDurationTicks(timeSignature, ticksPerQuarter);
}

export function secondsToPlaybackPosition(seconds, {
  tempo = 100,
  ticksPerQuarter = 480,
  timeSignature = '4/4',
  composition = null,
} = {}) {
  const safeSeconds = Math.max(0, Number(seconds) || 0);
  const timeline = composition ? compileTimeline(composition) : null;
  if (timeline) {
    const tick = secondsToTick(timeline, safeSeconds);
    const bar = barAtTick(timeline, Math.min(tick, timeline.durationTicks));
    const barStart = timeline.barBoundaries[bar - 1] ?? 0;
    const barEnd = timeline.barBoundaries[bar] ?? (barStart + timeline.ticksPerQuarter * 4);
    return {
      seconds: safeSeconds,
      tick,
      bar,
      tickInBar: tick - barStart,
      barTicks: barEnd - barStart,
    };
  }

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

export function ticksToPlaybackSeconds(tick, {
  tempo = 100,
  ticksPerQuarter = 480,
  composition = null,
} = {}) {
  const timeline = composition ? compileTimeline(composition) : null;
  if (timeline) {
    return tickToSeconds(timeline, Math.max(0, Math.min(Number(tick) || 0, timeline.durationTicks)));
  }
  const safeTempo = Number(tempo) || 100;
  const safeTpq = Number(ticksPerQuarter) || 480;
  return (Math.max(0, Number(tick) || 0) / safeTpq) * (60 / safeTempo);
}

export { audibleRevisionKey as compositionRevisionKey } from './compositionCanonical.js';
export { notationRevisionKey } from './compositionCanonical.js';
