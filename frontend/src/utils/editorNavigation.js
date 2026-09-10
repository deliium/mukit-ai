/**
 * Pure edit-cursor / bar / section / zoom navigation helpers for the V2 piano roll.
 * Navigation never mutates composition data. Logging is caller-owned.
 */

import { makeAnalysisSectionKey } from './compositionAnalysis.js';
import { selectionTickRange } from './compositionEditorSelection.js';
import {
  barAtTick,
  barStartTick,
  compileTimeline,
} from './compositionTimeline.js';
import { clampScrollLeft } from './pianoRollViewport.js';

export const DEFAULT_NAV_MIN_ZOOM = 0.01;
export const DEFAULT_NAV_MAX_ZOOM = 0.25;
export const DEFAULT_SELECTION_PADDING_TICKS = 240;
export const DEFAULT_ZOOM_STEP = 1.25;

/**
 * Clamp the ephemeral edit cursor into [0, duration_ticks].
 * @param {object|null|undefined} composition
 * @param {number} tick
 * @returns {number}
 */
export function clampEditCursorTick(composition, tick) {
  const raw = Number(tick);
  const value = Number.isFinite(raw) ? Math.max(0, Math.round(raw)) : 0;
  const duration = Number(composition?.duration_ticks);
  if (!Number.isInteger(duration) || duration < 0) {
    return value;
  }
  return Math.min(value, duration);
}

/**
 * @param {object|null|undefined} compositionOrTimeline
 * @returns {object|null}
 */
export function resolveTimeline(compositionOrTimeline) {
  if (!compositionOrTimeline || typeof compositionOrTimeline !== 'object') {
    return null;
  }
  if (Array.isArray(compositionOrTimeline.barBoundaries)
    && Number.isInteger(compositionOrTimeline.barCount)
    && Number.isInteger(compositionOrTimeline.durationTicks)) {
    return compositionOrTimeline;
  }
  return compileTimeline(compositionOrTimeline);
}

/**
 * Map a tick to a 1-based bar index (variable-meter safe).
 * @param {object|null|undefined} compositionOrTimeline
 * @param {number} tick
 * @returns {number|null}
 */
export function tickToBar(compositionOrTimeline, tick) {
  const timeline = resolveTimeline(compositionOrTimeline);
  if (!timeline) {
    return null;
  }
  const duration = timeline.durationTicks;
  const raw = Number(tick);
  if (!Number.isFinite(raw)) {
    return null;
  }
  const clamped = Math.max(0, Math.min(duration, Math.round(raw)));
  return barAtTick(timeline, clamped);
}

/**
 * Start tick of a 1-based bar, or null when out of range / uncompilable.
 * @param {object|null|undefined} composition
 * @param {number} bar
 * @returns {number|null}
 */
export function barToStartTick(composition, bar) {
  const timeline = resolveTimeline(composition);
  if (!timeline) {
    return null;
  }
  const barNumber = Number(bar);
  if (!Number.isInteger(barNumber)) {
    return null;
  }
  return barStartTick(timeline, barNumber);
}

/**
 * Destination tick for previous-bar navigation (start of previous or current first bar).
 * @returns {number}
 */
export function gotoPrevBar(composition, tick) {
  const timeline = resolveTimeline(composition);
  const cursor = clampEditCursorTick(composition, tick);
  if (!timeline) {
    return cursor;
  }
  const bar = barAtTick(timeline, cursor);
  if (bar == null) {
    return cursor;
  }
  const start = barStartTick(timeline, bar);
  if (start != null && cursor > start) {
    return clampEditCursorTick(composition, start);
  }
  const prevStart = barStartTick(timeline, Math.max(1, bar - 1));
  return clampEditCursorTick(composition, prevStart ?? 0);
}

/**
 * Destination tick for next-bar navigation (start of next bar, clamped).
 * @returns {number}
 */
export function gotoNextBar(composition, tick) {
  const timeline = resolveTimeline(composition);
  const cursor = clampEditCursorTick(composition, tick);
  if (!timeline) {
    return cursor;
  }
  const bar = barAtTick(timeline, cursor);
  if (bar == null) {
    return cursor;
  }
  const nextStart = barStartTick(timeline, Math.min(timeline.barCount, bar + 1));
  if (nextStart == null || bar >= timeline.barCount) {
    return clampEditCursorTick(composition, timeline.durationTicks);
  }
  // When already at the last bar start, stay; when mid-bar, jump to next.
  if (bar === timeline.barCount) {
    return clampEditCursorTick(composition, timeline.durationTicks);
  }
  return clampEditCursorTick(composition, nextStart);
}

/**
 * @param {object} section
 * @param {number} index
 * @returns {string}
 */
function formatNavigationSectionLabel(section, index) {
  const explicit = typeof section?.label === 'string' ? section.label.trim() : '';
  if (explicit) {
    return explicit;
  }
  const type = typeof section?.type === 'string' ? section.type.trim() : '';
  if (type && type !== 'unsectioned') {
    const start = Number(section?.start_bar);
    const count = Number(section?.bar_count);
    if (Number.isInteger(start) && Number.isInteger(count) && count > 0) {
      return `${type} (bars ${start}–${start + count - 1})`;
    }
    return type;
  }
  if (type === 'unsectioned') {
    return `Unsectioned ${index + 1}`;
  }
  return `Section ${index + 1}`;
}

/**
 * List navigable sections with generated labels for unlabeled / synthetic coverage.
 * @returns {Array<{
 *   id: string|null,
 *   key: string,
 *   label: string,
 *   startBar: number,
 *   endBar: number,
 *   startTick: number,
 *   endTick: number,
 *   index: number,
 * }>}
 */
export function listSectionsForNavigation(composition) {
  const timeline = resolveTimeline(composition);
  const duration = Number(composition?.duration_ticks);
  const barCount = Number(composition?.bar_count);
  const sections = Array.isArray(composition?.sections) ? composition.sections : [];

  if (!sections.length) {
    if (!timeline || !Number.isInteger(duration) || duration < 0) {
      return [];
    }
    return [{
      id: null,
      key: 'synthetic:unsectioned:0',
      label: 'Unsectioned',
      startBar: 1,
      endBar: Number.isInteger(barCount) && barCount > 0 ? barCount : 1,
      startTick: 0,
      endTick: duration,
      index: 0,
    }];
  }

  const listed = [];
  for (let index = 0; index < sections.length; index += 1) {
    const section = sections[index];
    const key = makeAnalysisSectionKey(section, index) || `idx:${index}`;
    const startTickRaw = Number(section?.start_tick);
    const durationTicks = Number(section?.duration_ticks);
    const startBarRaw = Number(section?.start_bar);
    const barSpan = Number(section?.bar_count);

    let startTick = Number.isFinite(startTickRaw) ? startTickRaw : 0;
    let endTick = Number.isFinite(durationTicks)
      ? startTick + Math.max(0, durationTicks)
      : startTick;

    if (timeline && Number.isInteger(startBarRaw) && startBarRaw >= 1) {
      const compiledStart = barStartTick(timeline, startBarRaw);
      if (compiledStart != null) {
        startTick = compiledStart;
      }
      if (Number.isInteger(barSpan) && barSpan > 0) {
        const endBar = startBarRaw + barSpan - 1;
        const nextStart = barStartTick(timeline, Math.min(timeline.barCount, endBar + 1));
        if (endBar >= timeline.barCount) {
          endTick = timeline.durationTicks;
        } else if (nextStart != null) {
          endTick = nextStart;
        }
      }
    }

    if (Number.isInteger(duration) && duration >= 0) {
      startTick = Math.max(0, Math.min(duration, Math.round(startTick)));
      endTick = Math.max(startTick, Math.min(duration, Math.round(endTick)));
    }

    const startBar = timeline
      ? (barAtTick(timeline, startTick) || startBarRaw || 1)
      : (Number.isInteger(startBarRaw) ? startBarRaw : 1);
    const endBarExclusiveTick = Math.max(startTick, endTick - (endTick > startTick ? 1 : 0));
    const endBar = timeline
      ? (barAtTick(timeline, endBarExclusiveTick) || startBar)
      : (
        Number.isInteger(startBarRaw) && Number.isInteger(barSpan) && barSpan > 0
          ? startBarRaw + barSpan - 1
          : startBar
      );

    listed.push({
      id: typeof section?.id === 'string' ? section.id : null,
      key,
      label: formatNavigationSectionLabel(section, index),
      startBar,
      endBar,
      startTick,
      endTick,
      index,
    });
  }
  return listed;
}

function findSectionIndexAtTick(sections, tick) {
  if (!sections.length) {
    return -1;
  }
  const value = Number(tick);
  for (let index = 0; index < sections.length; index += 1) {
    const section = sections[index];
    if (value >= section.startTick && value < section.endTick) {
      return index;
    }
  }
  // At exact end of score → last section.
  if (value === sections[sections.length - 1].endTick) {
    return sections.length - 1;
  }
  // Between gaps: nearest previous by start.
  let best = 0;
  for (let index = 0; index < sections.length; index += 1) {
    if (sections[index].startTick <= value) {
      best = index;
    }
  }
  return best;
}

/**
 * @returns {number}
 */
export function gotoSection(composition, sectionKey) {
  const sections = listSectionsForNavigation(composition);
  const key = sectionKey == null ? '' : String(sectionKey);
  const match = sections.find((section) => section.key === key)
    || sections.find((section) => section.id != null && section.id === key);
  if (!match) {
    return clampEditCursorTick(composition, 0);
  }
  return clampEditCursorTick(composition, match.startTick);
}

/**
 * DAW-style: if past section start, snap to start; else previous section start.
 * @returns {number}
 */
export function gotoPrevSection(composition, tick) {
  const sections = listSectionsForNavigation(composition);
  const cursor = clampEditCursorTick(composition, tick);
  if (!sections.length) {
    return cursor;
  }
  const index = findSectionIndexAtTick(sections, cursor);
  const current = sections[Math.max(0, index)];
  if (cursor > current.startTick) {
    return clampEditCursorTick(composition, current.startTick);
  }
  const prev = sections[Math.max(0, index - 1)];
  return clampEditCursorTick(composition, prev.startTick);
}

/**
 * @returns {number}
 */
export function gotoNextSection(composition, tick) {
  const sections = listSectionsForNavigation(composition);
  const cursor = clampEditCursorTick(composition, tick);
  if (!sections.length) {
    return cursor;
  }
  const index = findSectionIndexAtTick(sections, cursor);
  const next = sections[Math.min(sections.length - 1, index + 1)];
  return clampEditCursorTick(composition, next.startTick);
}

/**
 * Compute scrollLeft (and optional zoom) so selected note refs fit the viewport.
 * @returns {{
 *   ok: boolean,
 *   scrollLeft: number,
 *   pixelsPerTick: number|null,
 *   startTick: number|null,
 *   endTick: number|null,
 *   centerTick: number|null,
 *   reason?: string,
 * }}
 */
export function selectionZoomWindow(composition, refs, {
  pixelsPerTick = 0.05,
  clientWidth = 0,
  paddingTicks = DEFAULT_SELECTION_PADDING_TICKS,
  minZoom = DEFAULT_NAV_MIN_ZOOM,
  maxZoom = DEFAULT_NAV_MAX_ZOOM,
  adjustZoom = true,
} = {}) {
  const range = selectionTickRange(composition, refs);
  const duration = Number(composition?.duration_ticks);
  const ppt = Number(pixelsPerTick);
  const width = Math.max(0, Number(clientWidth) || 0);
  const pad = Math.max(0, Number(paddingTicks) || 0);

  if (!range || !Number.isInteger(duration) || duration < 0) {
    return {
      ok: false,
      scrollLeft: 0,
      pixelsPerTick: null,
      startTick: null,
      endTick: null,
      centerTick: null,
      reason: 'empty-selection',
    };
  }
  if (!Number.isFinite(ppt) || ppt <= 0 || width <= 0) {
    return {
      ok: false,
      scrollLeft: 0,
      pixelsPerTick: null,
      startTick: range.startTick,
      endTick: range.endTick,
      centerTick: Math.round((range.startTick + range.endTick) / 2),
      reason: 'invalid-viewport',
    };
  }

  const startTick = Math.max(0, range.startTick - pad);
  const endTick = Math.min(duration, Math.max(startTick + 1, range.endTick + pad));
  const span = Math.max(1, endTick - startTick);
  let nextPpt = ppt;
  if (adjustZoom) {
    const fitted = width / span;
    nextPpt = Math.min(maxZoom, Math.max(minZoom, fitted));
  }
  const contentWidth = duration * nextPpt;
  const idealLeft = startTick * nextPpt - Math.max(0, (width - span * nextPpt) / 2);
  const clamped = clampScrollLeft(idealLeft, { contentWidth, clientWidth: width });
  return {
    ok: true,
    scrollLeft: clamped.scrollLeft,
    pixelsPerTick: adjustZoom ? nextPpt : null,
    startTick,
    endTick,
    centerTick: Math.round((startTick + endTick) / 2),
  };
}

/**
 * Zoom that fits the full composition duration into the client width.
 * @returns {{ pixelsPerTick: number, clamped: boolean }}
 */
export function fitCompositionZoom({
  durationTicks = 0,
  clientWidth = 0,
  minZoom = DEFAULT_NAV_MIN_ZOOM,
  maxZoom = DEFAULT_NAV_MAX_ZOOM,
} = {}) {
  const duration = Math.max(0, Number(durationTicks) || 0);
  const width = Math.max(0, Number(clientWidth) || 0);
  const min = Number(minZoom);
  const max = Number(maxZoom);
  if (duration <= 0 || width <= 0 || !Number.isFinite(min) || !Number.isFinite(max) || max < min) {
    return { pixelsPerTick: Number.isFinite(min) ? min : DEFAULT_NAV_MIN_ZOOM, clamped: true };
  }
  const raw = width / duration;
  const next = Math.min(max, Math.max(min, raw));
  return { pixelsPerTick: next, clamped: next !== raw };
}

/**
 * Step zoom toward max/min by a multiplicative factor.
 * @returns {number}
 */
export function stepZoom(currentZoom, direction, {
  step = DEFAULT_ZOOM_STEP,
  minZoom = DEFAULT_NAV_MIN_ZOOM,
  maxZoom = DEFAULT_NAV_MAX_ZOOM,
} = {}) {
  const current = Number(currentZoom);
  const factor = Number(step) > 0 ? Number(step) : DEFAULT_ZOOM_STEP;
  if (!Number.isFinite(current) || current <= 0) {
    return Math.min(maxZoom, Math.max(minZoom, DEFAULT_NAV_MIN_ZOOM));
  }
  const next = direction < 0 ? current / factor : current * factor;
  return Math.min(maxZoom, Math.max(minZoom, next));
}

/**
 * Center the viewport on a tick at the given zoom.
 * @returns {{ scrollLeft: number, centerTick: number }}
 */
export function scrollLeftForCenterTick({
  centerTick = 0,
  pixelsPerTick = 0.05,
  clientWidth = 0,
  durationTicks = 0,
} = {}) {
  const ppt = Number(pixelsPerTick);
  const width = Math.max(0, Number(clientWidth) || 0);
  const duration = Math.max(0, Number(durationTicks) || 0);
  const tick = Math.max(0, Math.min(duration, Number(centerTick) || 0));
  if (!Number.isFinite(ppt) || ppt <= 0) {
    return { scrollLeft: 0, centerTick: tick };
  }
  const contentWidth = duration * ppt;
  const ideal = tick * ppt - width / 2;
  const clamped = clampScrollLeft(ideal, { contentWidth, clientWidth: width });
  return { scrollLeft: clamped.scrollLeft, centerTick: tick };
}
