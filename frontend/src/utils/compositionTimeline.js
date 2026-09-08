/**
 * Variable-tempo / variable-meter timeline helpers for composition.v2.
 * Pure functions derived only from canonical V2 fields. No runtime logging
 * in conversion hot paths; callers log compile summaries.
 */

const SUPPORTED_DENOMINATORS = new Set([1, 2, 4, 8, 16, 32]);

export function parseTimeSignature(timeSignature) {
  if (!timeSignature || typeof timeSignature !== 'string') {
    return null;
  }
  const parts = timeSignature.trim().split('/');
  if (parts.length !== 2) {
    return null;
  }
  const numerator = Number(parts[0]);
  const denominator = Number(parts[1]);
  if (!Number.isInteger(numerator) || !Number.isInteger(denominator)) {
    return null;
  }
  if (numerator < 1 || numerator > 32 || !SUPPORTED_DENOMINATORS.has(denominator)) {
    return null;
  }
  return { numerator, denominator };
}

export function barDurationTicks(timeSignature, ticksPerQuarter) {
  const parsed = parseTimeSignature(timeSignature);
  const tpq = Number(ticksPerQuarter);
  if (!parsed || !Number.isInteger(tpq) || tpq <= 0) {
    return null;
  }
  const numeratorTicks = parsed.numerator * 4 * tpq;
  if (numeratorTicks % parsed.denominator !== 0) {
    return null;
  }
  return numeratorTicks / parsed.denominator;
}

export function roundHalfAwayFromZero(value) {
  if (value >= 0) {
    return Math.trunc(value + 0.5);
  }
  return Math.trunc(value - 0.5);
}

/**
 * @returns {number[]|null} bar start ticks plus final end tick (length barCount+1)
 */
export function compileBarBoundaries({
  timeSignature,
  ticksPerQuarter,
  barCount,
  durationTicks,
  timeSignatureChanges = [],
} = {}) {
  const tpq = Number(ticksPerQuarter);
  const bars = Number(barCount);
  const duration = Number(durationTicks);
  if (!Number.isInteger(tpq) || tpq <= 0 || !Number.isInteger(bars) || bars < 1) {
    return null;
  }
  if (!Number.isInteger(duration) || duration <= 0) {
    return null;
  }

  const changeByTick = new Map();
  for (const change of timeSignatureChanges || []) {
    const tick = Number(change.tick);
    const meter = change.time_signature;
    if (!Number.isInteger(tick) || tick <= 0 || tick >= duration) {
      return null;
    }
    if (changeByTick.has(tick)) {
      return null;
    }
    if (barDurationTicks(meter, tpq) == null) {
      return null;
    }
    changeByTick.set(tick, meter);
  }

  const boundaries = [0];
  let activeMeter = timeSignature;
  for (let index = 0; index < bars; index += 1) {
    const start = boundaries[boundaries.length - 1];
    if (start !== 0 && changeByTick.has(start)) {
      activeMeter = changeByTick.get(start);
    }
    const barTicks = barDurationTicks(activeMeter, tpq);
    if (barTicks == null) {
      return null;
    }
    boundaries.push(start + barTicks);
  }

  if (boundaries[boundaries.length - 1] !== duration) {
    return null;
  }

  for (const tick of changeByTick.keys()) {
    if (!boundaries.slice(0, -1).includes(tick)) {
      return null;
    }
  }
  return boundaries;
}

function sortedChanges(items, valueKey) {
  return [...(items || [])]
    .map((item) => ({ tick: Number(item.tick), value: item[valueKey] }))
    .filter((item) => Number.isInteger(item.tick))
    .sort((a, b) => a.tick - b.tick);
}

function activeFromChanges(rootValue, changes, tick, valueKey = 'value') {
  let current = rootValue;
  for (const change of changes) {
    if (change.tick <= tick) {
      current = change[valueKey];
    } else {
      break;
    }
  }
  return current;
}

/**
 * Compile a timeline object from a composition document.
 * @returns {object|null}
 */
export function compileTimeline(composition) {
  if (!composition || typeof composition !== 'object') {
    return null;
  }
  const ticksPerQuarter = Number(composition.ticks_per_quarter) || 480;
  const durationTicks = Number(composition.duration_ticks);
  const barCount = Number(composition.bar_count);
  const rootTempo = Number(composition.tempo);
  const rootMeter = composition.time_signature;
  const rootKey = composition.key;
  const tempoChanges = sortedChanges(composition.tempo_changes, 'bpm').map((item) => ({
    tick: item.tick,
    value: Number(item.value),
  }));
  const meterChanges = sortedChanges(composition.time_signature_changes, 'time_signature');
  const keyChanges = sortedChanges(composition.key_changes, 'key');

  const barBoundaries = compileBarBoundaries({
    timeSignature: rootMeter,
    ticksPerQuarter,
    barCount,
    durationTicks,
    timeSignatureChanges: composition.time_signature_changes || [],
  });
  if (!barBoundaries) {
    return null;
  }

  return {
    ticksPerQuarter,
    durationTicks,
    barCount,
    barBoundaries,
    rootTempo,
    rootTimeSignature: rootMeter,
    rootKey,
    tempoChanges,
    timeSignatureChanges: meterChanges,
    keyChanges,
  };
}

export function barStartTick(timeline, bar) {
  if (!timeline || bar < 1 || bar > timeline.barCount) {
    return null;
  }
  return timeline.barBoundaries[bar - 1];
}

export function barEndTick(timeline, bar) {
  if (!timeline || bar < 1 || bar > timeline.barCount) {
    return null;
  }
  return timeline.barBoundaries[bar];
}

export function barRangeTicks(timeline, startBar, endBar) {
  if (!timeline || endBar < startBar) {
    return null;
  }
  const startTick = barStartTick(timeline, startBar);
  const endTick = barEndTick(timeline, endBar);
  if (startTick == null || endTick == null) {
    return null;
  }
  return { startTick, endTick };
}

export function barAtTick(timeline, tick) {
  if (!timeline || tick < 0 || tick > timeline.durationTicks) {
    return null;
  }
  if (tick === timeline.durationTicks) {
    return timeline.barCount;
  }
  let lo = 0;
  let hi = timeline.barCount - 1;
  while (lo <= hi) {
    const mid = Math.floor((lo + hi) / 2);
    const start = timeline.barBoundaries[mid];
    const end = timeline.barBoundaries[mid + 1];
    if (tick >= start && tick < end) {
      return mid + 1;
    }
    if (tick < start) {
      hi = mid - 1;
    } else {
      lo = mid + 1;
    }
  }
  return timeline.barCount;
}

export function activeTempo(timeline, tick) {
  return activeFromChanges(timeline.rootTempo, timeline.tempoChanges, tick);
}

export function activeTimeSignature(timeline, tick) {
  return activeFromChanges(timeline.rootTimeSignature, timeline.timeSignatureChanges, tick);
}

export function activeKey(timeline, tick) {
  return activeFromChanges(timeline.rootKey, timeline.keyChanges, tick);
}

function integrateTicks(timeline, startTick, endTick) {
  if (endTick < startTick) {
    throw new Error('endTick must be >= startTick');
  }
  if (startTick === endTick) {
    return 0;
  }
  let total = 0;
  let cursor = startTick;
  let tempo = activeTempo(timeline, startTick);
  const upcoming = timeline.tempoChanges.filter((change) => change.tick > startTick).map((change) => change.tick);
  let changeIndex = 0;
  while (cursor < endTick) {
    const nextBoundary = changeIndex < upcoming.length ? upcoming[changeIndex] : endTick;
    const segmentEnd = Math.min(nextBoundary, endTick);
    const secondsPerTick = 60 / tempo / timeline.ticksPerQuarter;
    total += (segmentEnd - cursor) * secondsPerTick;
    cursor = segmentEnd;
    if (changeIndex < upcoming.length && upcoming[changeIndex] === cursor) {
      tempo = activeTempo(timeline, cursor);
      changeIndex += 1;
    }
  }
  return total;
}

export function tickToSeconds(timeline, tick) {
  if (!timeline || tick < 0 || tick > timeline.durationTicks) {
    return null;
  }
  return integrateTicks(timeline, 0, tick);
}

export function totalDurationSeconds(timeline) {
  if (!timeline) {
    return null;
  }
  return integrateTicks(timeline, 0, timeline.durationTicks);
}

export function secondsToTick(timeline, seconds) {
  if (!timeline || seconds < 0) {
    return null;
  }
  const total = totalDurationSeconds(timeline);
  if (seconds >= total) {
    return timeline.durationTicks;
  }

  let cursorTick = 0;
  let remaining = seconds;
  let tempo = timeline.rootTempo;
  let changeIndex = 0;
  while (cursorTick < timeline.durationTicks) {
    const nextChange = changeIndex < timeline.tempoChanges.length
      ? timeline.tempoChanges[changeIndex].tick
      : timeline.durationTicks;
    const segmentEnd = Math.min(nextChange, timeline.durationTicks);
    if (segmentEnd <= cursorTick) {
      if (changeIndex < timeline.tempoChanges.length && nextChange === cursorTick) {
        tempo = timeline.tempoChanges[changeIndex].value;
        changeIndex += 1;
        continue;
      }
      break;
    }
    const secondsPerTick = 60 / tempo / timeline.ticksPerQuarter;
    const segmentTicks = segmentEnd - cursorTick;
    const segmentSeconds = segmentTicks * secondsPerTick;
    if (remaining <= segmentSeconds) {
      return cursorTick + (remaining / secondsPerTick);
    }
    remaining -= segmentSeconds;
    cursorTick = segmentEnd;
    if (changeIndex < timeline.tempoChanges.length && nextChange === cursorTick) {
      tempo = timeline.tempoChanges[changeIndex].value;
      changeIndex += 1;
    }
  }
  return timeline.durationTicks;
}

/**
 * Map pointer X to a 1-based bar using variable-width bar boundaries.
 */
export function pointerXToBarFromTimeline(pointerX, {
  timeline,
  pixelsPerTick,
  scrollLeft = 0,
} = {}) {
  if (!timeline || !Number.isFinite(Number(pixelsPerTick)) || Number(pixelsPerTick) <= 0) {
    return { bar: null, warning: 'timeline and pixelsPerTick are required' };
  }
  const x = Number(pointerX) + Number(scrollLeft || 0);
  if (!Number.isFinite(x)) {
    return { bar: null, warning: 'pointerX must be a number' };
  }
  const tick = Math.max(0, Math.min(timeline.durationTicks, x / Number(pixelsPerTick)));
  const bar = barAtTick(timeline, tick);
  return { bar };
}
