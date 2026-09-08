/**
 * Pure helpers for AI region bar-range selection on the piano roll.
 * Keep log-free; callers own logging.
 */

import { barDurationTicks } from './playbackPosition.js';
import { barRangeTicks, compileTimeline, pointerXToBarFromTimeline } from './compositionTimeline.js';

/**
 * Normalize an inclusive bar range and clamp to composition bounds.
 * @returns {{ startBar: number|null, endBar: number|null, warning?: string }}
 */
export function normalizeBarRange(startBar, endBar, barCount) {
  const totalBars = Number(barCount);
  if (!Number.isInteger(totalBars) || totalBars < 1) {
    return { startBar: null, endBar: null, warning: 'bar_count must be a positive integer' };
  }

  let start = Number(startBar);
  let end = Number(endBar);
  if (!Number.isFinite(start) || !Number.isFinite(end)) {
    return { startBar: null, endBar: null, warning: 'start_bar and end_bar must be numbers' };
  }

  start = Math.round(start);
  end = Math.round(end);
  if (end < start) {
    const swap = start;
    start = end;
    end = swap;
  }

  start = Math.max(1, Math.min(totalBars, start));
  end = Math.max(1, Math.min(totalBars, end));
  if (end < start) {
    end = start;
  }
  return { startBar: start, endBar: end };
}

/**
 * Map a pointer X position within the grid to a 1-based bar number.
 * @returns {{ bar: number|null, warning?: string }}
 */
export function pointerXToBar(pointerX, {
  pixelsPerTick,
  barTicks,
  barCount,
  scrollLeft = 0,
  timeline = null,
  barBoundaries = null,
} = {}) {
  if (timeline || barBoundaries) {
    const compiled = timeline || {
      barCount: Number(barCount),
      durationTicks: Array.isArray(barBoundaries) ? barBoundaries[barBoundaries.length - 1] : 0,
      barBoundaries,
    };
    return pointerXToBarFromTimeline(pointerX, {
      timeline: compiled,
      pixelsPerTick,
      scrollLeft,
    });
  }
  const ppt = Number(pixelsPerTick);
  const ticks = Number(barTicks);
  const totalBars = Number(barCount);
  if (!Number.isFinite(ppt) || ppt <= 0 || !Number.isFinite(ticks) || ticks <= 0) {
    return { bar: null, warning: 'pixelsPerTick and barTicks must be positive' };
  }
  if (!Number.isInteger(totalBars) || totalBars < 1) {
    return { bar: null, warning: 'bar_count must be a positive integer' };
  }
  const x = Number(pointerX) + Number(scrollLeft || 0);
  if (!Number.isFinite(x)) {
    return { bar: null, warning: 'pointerX must be a number' };
  }
  const tick = Math.max(0, x / ppt);
  const zeroBased = Math.floor(tick / ticks);
  const bar = Math.max(1, Math.min(totalBars, zeroBased + 1));
  return { bar };
}

/**
 * Derive inclusive-start / exclusive-end tick bounds for a bar range.
 * @returns {{ startTick: number|null, endTick: number|null, barTicks: number|null, warning?: string }}
 */
export function selectedTickBoundaries(startBar, endBar, {
  timeSignature = '4/4',
  ticksPerQuarter = 480,
  durationTicks = null,
  composition = null,
} = {}) {
  const normalized = normalizeBarRange(startBar, endBar, Number.MAX_SAFE_INTEGER);
  if (normalized.startBar === null) {
    return { startTick: null, endTick: null, barTicks: null, warning: normalized.warning };
  }

  const timeline = composition ? compileTimeline(composition) : null;
  if (timeline) {
    const range = barRangeTicks(timeline, normalized.startBar, normalized.endBar);
    if (!range) {
      return { startTick: null, endTick: null, barTicks: null, warning: 'invalid bar range for timeline' };
    }
    let endTick = range.endTick;
    if (Number.isFinite(Number(durationTicks))) {
      endTick = Math.min(endTick, Number(durationTicks));
    }
    const firstBarTicks = timeline.barBoundaries[1] - timeline.barBoundaries[0];
    return { startTick: range.startTick, endTick, barTicks: firstBarTicks };
  }

  const barTicks = barDurationTicks(timeSignature, ticksPerQuarter);
  if (!barTicks) {
    return { startTick: null, endTick: null, barTicks: null, warning: 'unsupported time signature or ticks' };
  }
  const startTick = (normalized.startBar - 1) * barTicks;
  let endTick = normalized.endBar * barTicks;
  if (Number.isFinite(Number(durationTicks))) {
    endTick = Math.min(endTick, Number(durationTicks));
  }
  return { startTick, endTick, barTicks };
}

/**
 * Default target track IDs for AI region edits.
 * @param {'current'|'all'} mode
 */
export function defaultTargetTrackIds(composition, {
  mode = 'current',
  currentTrackId = null,
} = {}) {
  const tracks = Array.isArray(composition?.tracks) ? composition.tracks : [];
  const allIds = tracks.map((track) => String(track.id));
  if (mode === 'all') {
    return allIds;
  }
  if (currentTrackId && allIds.includes(String(currentTrackId))) {
    return [String(currentTrackId)];
  }
  return allIds.length ? [allIds[0]] : [];
}

/**
 * Pixel overlay geometry for a selected inclusive bar range.
 */
export function selectionOverlayRect(startBar, endBar, {
  pixelsPerTick,
  barTicks,
  totalHeight,
  barBoundaries = null,
} = {}) {
  const ppt = Number(pixelsPerTick);
  const normalized = normalizeBarRange(startBar, endBar, Number.MAX_SAFE_INTEGER);
  if (normalized.startBar === null) {
    return null;
  }
  if (Array.isArray(barBoundaries) && barBoundaries.length > normalized.endBar) {
    const startTick = barBoundaries[normalized.startBar - 1];
    const endTick = barBoundaries[normalized.endBar];
    return {
      left: startTick * ppt,
      width: (endTick - startTick) * ppt,
      top: 0,
      height: Number(totalHeight) || 0,
      startBar: normalized.startBar,
      endBar: normalized.endBar,
    };
  }
  const ticks = Number(barTicks);
  if (!Number.isFinite(ppt) || ppt <= 0 || !Number.isFinite(ticks) || ticks <= 0) {
    return null;
  }
  const left = (normalized.startBar - 1) * ticks * ppt;
  const width = (normalized.endBar - normalized.startBar + 1) * ticks * ppt;
  return {
    left,
    width,
    top: 0,
    height: Number(totalHeight) || 0,
    startBar: normalized.startBar,
    endBar: normalized.endBar,
  };
}
