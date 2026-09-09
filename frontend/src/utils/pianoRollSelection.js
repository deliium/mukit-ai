/**
 * Pure helpers for AI region bar-range selection on the piano roll.
 * Keep log-free; callers own logging.
 */

import { barDurationTicks } from './playbackPosition.js';
import { barRangeTicks, compileTimeline, pointerXToBarFromTimeline } from './compositionTimeline.js';

export const MOTIF_MAX_BAR_SPAN = 2;
export const MOTIF_BAR_SPAN_EXCEEDED_CODE = 'motif_bar_span_exceeded';

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
export function motifBarSpanCount(startBar, endBar) {
  const start = Number(startBar);
  const end = Number(endBar);
  if (!Number.isFinite(start) || !Number.isFinite(end)) {
    return null;
  }
  return Math.abs(Math.round(end) - Math.round(start)) + 1;
}

/**
 * Validate an inclusive motif source/destination bar range (max two bars).
 * Rejects spans wider than two bars instead of truncating.
 * @returns {{ startBar: number|null, endBar: number|null, barSpan: number|null, valid: boolean, warning?: string, code?: string }}
 */
export function validateMotifBarRange(startBar, endBar, barCount) {
  const normalized = normalizeBarRange(startBar, endBar, barCount);
  if (normalized.startBar === null) {
    return {
      startBar: null,
      endBar: null,
      barSpan: null,
      valid: false,
      warning: normalized.warning,
      code: 'motif_invalid_bar_range',
    };
  }
  const barSpan = motifBarSpanCount(normalized.startBar, normalized.endBar);
  if (barSpan > MOTIF_MAX_BAR_SPAN) {
    return {
      startBar: normalized.startBar,
      endBar: normalized.endBar,
      barSpan,
      valid: false,
      warning: `Motif range must span at most ${MOTIF_MAX_BAR_SPAN} bars`,
      code: MOTIF_BAR_SPAN_EXCEEDED_CODE,
    };
  }
  return {
    startBar: normalized.startBar,
    endBar: normalized.endBar,
    barSpan,
    valid: true,
  };
}

/**
 * Tick bounds for a motif source selection using the compiled variable-meter timeline.
 */
export function motifSourceTickRange(startBar, endBar, { composition = null } = {}) {
  const barCount = Number(composition?.bar_count);
  if (!Number.isInteger(barCount) || barCount < 1) {
    return {
      startTick: null,
      endTick: null,
      startBar: null,
      endBar: null,
      barSpan: null,
      valid: false,
      warning: 'composition bar_count must be a positive integer',
      code: 'motif_invalid_composition',
    };
  }
  const validated = validateMotifBarRange(startBar, endBar, barCount);
  if (!validated.valid) {
    return {
      startTick: null,
      endTick: null,
      startBar: validated.startBar,
      endBar: validated.endBar,
      barSpan: validated.barSpan,
      valid: false,
      warning: validated.warning,
      code: validated.code,
    };
  }
  const timeline = compileTimeline(composition);
  if (!timeline) {
    return {
      startTick: null,
      endTick: null,
      startBar: validated.startBar,
      endBar: validated.endBar,
      barSpan: validated.barSpan,
      valid: false,
      warning: 'Unable to compile composition timeline',
      code: 'motif_timeline_unavailable',
    };
  }
  const range = barRangeTicks(timeline, validated.startBar, validated.endBar);
  if (!range) {
    return {
      startTick: null,
      endTick: null,
      startBar: validated.startBar,
      endBar: validated.endBar,
      barSpan: validated.barSpan,
      valid: false,
      warning: 'Invalid bar range for compiled timeline',
      code: 'motif_invalid_bar_range',
    };
  }
  return {
    startTick: range.startTick,
    endTick: range.endTick,
    startBar: validated.startBar,
    endBar: validated.endBar,
    barSpan: validated.barSpan,
    valid: true,
  };
}

/**
 * Tick bounds for a destination placement starting at startBar over barSpan bars.
 */
export function motifDestinationTickRange(startBar, {
  composition = null,
  barSpan = 1,
} = {}) {
  const span = Number(barSpan);
  if (!Number.isInteger(span) || span < 1 || span > MOTIF_MAX_BAR_SPAN) {
    return {
      startTick: null,
      endTick: null,
      startBar: null,
      endBar: null,
      barSpan: span,
      valid: false,
      warning: `Destination span must be 1–${MOTIF_MAX_BAR_SPAN} bars`,
      code: 'motif_invalid_destination_span',
    };
  }
  const start = Number(startBar);
  if (!Number.isFinite(start)) {
    return {
      startTick: null,
      endTick: null,
      startBar: null,
      endBar: null,
      barSpan: span,
      valid: false,
      warning: 'start_bar must be a number',
      code: 'motif_invalid_bar_range',
    };
  }
  const endBar = Math.round(start) + span - 1;
  return motifSourceTickRange(Math.round(start), endBar, { composition });
}

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
