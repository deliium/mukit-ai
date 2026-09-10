/**
 * Pure piano-roll viewport geometry: visible tick/pitch ranges, note culling,
 * zoom anchoring, and scroll clamping. Logging is caller-owned.
 */

/** Default horizontal buffer in ticks (~1 bar at 480 TPQ / 4/4). */
export const DEFAULT_TICK_BUFFER = 1920;

/** Extra pitch rows above/below the visible lane. */
export const DEFAULT_PITCH_BUFFER_ROWS = 2;

/** Fallback pixel buffer when converting to ticks without an explicit tick buffer. */
export const DEFAULT_PIXEL_BUFFER = 96;

/**
 * @param {number} ms
 * @returns {(fn: (...args: any[]) => void) => (...args: any[]) => void}
 */
export function createThrottledFn(ms) {
  const interval = Math.max(0, Number(ms) || 0);
  let last = 0;
  return (fn) => (...args) => {
    const now = Date.now();
    if (now - last < interval) {
      return;
    }
    last = now;
    fn(...args);
  };
}

/**
 * Derive inclusive-exclusive tick window visible in the scroll container.
 * @returns {{ startTick: number, endTick: number, bufferTicks: number }}
 */
export function computeVisibleTickRange({
  scrollLeft = 0,
  clientWidth = 0,
  pixelsPerTick = 0.05,
  durationTicks = 0,
  bufferTicks = DEFAULT_TICK_BUFFER,
  bufferPixels,
} = {}) {
  const ppt = Number(pixelsPerTick);
  const duration = Math.max(0, Number(durationTicks) || 0);
  const left = Math.max(0, Number(scrollLeft) || 0);
  const width = Math.max(0, Number(clientWidth) || 0);

  if (!Number.isFinite(ppt) || ppt <= 0) {
    return { startTick: 0, endTick: duration, bufferTicks: 0 };
  }

  const resolvedBuffer = bufferPixels != null && Number.isFinite(Number(bufferPixels))
    ? Math.max(0, Math.ceil((Number(bufferPixels) || 0) / ppt))
    : Math.max(0, Number(bufferTicks) || 0);

  const rawStart = left / ppt - resolvedBuffer;
  const rawEnd = (left + width) / ppt + resolvedBuffer;
  const startTick = Math.max(0, Math.floor(rawStart));
  const endTick = Math.min(duration, Math.ceil(Math.max(rawEnd, startTick)));

  return { startTick, endTick, bufferTicks: resolvedBuffer };
}

/**
 * Derive inclusive MIDI pitch window for the vertical viewport.
 * Row 0 at scrollTop=0 is maxMidi (highest pitch at top).
 * @returns {{ minMidi: number, maxMidi: number, bufferRows: number }}
 */
export function computeVisiblePitchRange({
  scrollTop = 0,
  clientHeight = 0,
  rowHeight = 14,
  minMidi = 0,
  maxMidi = 127,
  bufferRows = DEFAULT_PITCH_BUFFER_ROWS,
} = {}) {
  const height = Number(rowHeight);
  const low = Number(minMidi);
  const high = Number(maxMidi);
  const top = Math.max(0, Number(scrollTop) || 0);
  const client = Math.max(0, Number(clientHeight) || 0);
  const buffer = Math.max(0, Math.floor(Number(bufferRows) || 0));

  if (!Number.isFinite(height) || height <= 0 || !Number.isInteger(low) || !Number.isInteger(high) || high < low) {
    return { minMidi: low || 0, maxMidi: high || 127, bufferRows: buffer };
  }

  const topRow = Math.floor(top / height) - buffer;
  const bottomRow = Math.ceil((top + client) / height) + buffer;
  const visibleMax = Math.min(high, high - topRow);
  const visibleMin = Math.max(low, high - (bottomRow - 1));

  return {
    minMidi: Math.min(visibleMin, visibleMax),
    maxMidi: Math.max(visibleMin, visibleMax),
    bufferRows: buffer,
  };
}

/**
 * Axis-aligned intersection of note geometry with tick + pitch windows.
 * @param {{ startTick: number, endTick: number, pitchMidi: number }} noteGeom
 * @param {{ startTick: number, endTick: number }} visibleTicks
 * @param {{ minMidi: number, maxMidi: number }} visiblePitches
 */
export function noteIntersectsViewport(noteGeom, visibleTicks, visiblePitches) {
  if (!noteGeom || !visibleTicks || !visiblePitches) {
    return false;
  }
  const start = Number(noteGeom.startTick);
  const end = Number(noteGeom.endTick);
  const pitch = Number(noteGeom.pitchMidi);
  if (!Number.isFinite(start) || !Number.isFinite(end) || !Number.isFinite(pitch)) {
    return false;
  }
  if (end <= visibleTicks.startTick || start >= visibleTicks.endTick) {
    return false;
  }
  if (pitch < visiblePitches.minMidi || pitch > visiblePitches.maxMidi) {
    return false;
  }
  return true;
}

/**
 * Filter notes/events that intersect the buffered viewport.
 * Accepts either precomputed geometry fields or raw V2 events (+ optional trackId).
 * @param {Array<object>} notesOrEventsWithTrack
 * @param {{
 *   visibleTicks?: { startTick: number, endTick: number },
 *   visiblePitches?: { minMidi: number, maxMidi: number },
 *   pitchOf?: (note: object) => number|null,
 * }} viewport
 */
export function filterNotesInViewport(notesOrEventsWithTrack, viewport = {}) {
  const list = Array.isArray(notesOrEventsWithTrack) ? notesOrEventsWithTrack : [];
  const ticks = viewport.visibleTicks || { startTick: 0, endTick: Number.POSITIVE_INFINITY };
  const pitches = viewport.visiblePitches || { minMidi: 0, maxMidi: 127 };
  const pitchOf = typeof viewport.pitchOf === 'function' ? viewport.pitchOf : null;

  return list.filter((note) => {
    const startTick = Number(
      note?.startTick ?? note?.start_tick ?? 0,
    );
    const duration = Number(
      note?.durationTicks ?? note?.duration_ticks ?? 0,
    );
    const endTick = Number.isFinite(Number(note?.endTick))
      ? Number(note.endTick)
      : startTick + Math.max(0, duration);
    let pitchMidi = Number(note?.pitchMidi);
    if (!Number.isFinite(pitchMidi)) {
      pitchMidi = pitchOf ? pitchOf(note) : null;
      pitchMidi = Number(pitchMidi);
    }
    return noteIntersectsViewport(
      { startTick, endTick, pitchMidi },
      ticks,
      pitches,
    );
  });
}

/**
 * Keep the content point under `pointerX` stable when zoom (pixelsPerTick) changes.
 * @returns {{ scrollLeft: number, clamped: boolean, contentTick: number }}
 */
export function anchorZoom({
  scrollLeft = 0,
  pointerX,
  oldPixelsPerTick = 0.05,
  newPixelsPerTick = 0.05,
  contentWidth = 0,
  clientWidth = 0,
} = {}) {
  const oldPpt = Number(oldPixelsPerTick);
  const newPpt = Number(newPixelsPerTick);
  const left = Number(scrollLeft) || 0;
  const px = Number(pointerX);
  const width = Math.max(0, Number(contentWidth) || 0);
  const client = Math.max(0, Number(clientWidth) || 0);

  if (!Number.isFinite(oldPpt) || oldPpt <= 0 || !Number.isFinite(newPpt) || newPpt <= 0) {
    const clamped = clampScrollLeft(left, { contentWidth: width, clientWidth: client });
    return { scrollLeft: clamped.scrollLeft, clamped: clamped.clamped, contentTick: 0 };
  }

  const anchorX = pointerX != null && Number.isFinite(px) ? px : client / 2;
  const contentTick = (left + Math.max(0, anchorX)) / oldPpt;
  const nextLeft = contentTick * newPpt - Math.max(0, anchorX);
  const clamped = clampScrollLeft(nextLeft, { contentWidth: width, clientWidth: client });
  return {
    scrollLeft: clamped.scrollLeft,
    clamped: clamped.clamped,
    contentTick,
  };
}

/**
 * Clamp horizontal scroll into [0, max(0, contentWidth - clientWidth)].
 * @returns {{ scrollLeft: number, clamped: boolean, maxScrollLeft: number }}
 */
export function clampScrollLeft(scrollLeft, { contentWidth = 0, clientWidth = 0 } = {}) {
  const width = Math.max(0, Number(contentWidth) || 0);
  const client = Math.max(0, Number(clientWidth) || 0);
  const maxScrollLeft = Math.max(0, width - client);
  const raw = Number(scrollLeft);
  const value = Number.isFinite(raw) ? raw : 0;
  const next = Math.min(maxScrollLeft, Math.max(0, value));
  return {
    scrollLeft: next,
    clamped: next !== value,
    maxScrollLeft,
  };
}

/**
 * Clamp vertical scroll into [0, max(0, contentHeight - clientHeight)].
 * @returns {{ scrollTop: number, clamped: boolean, maxScrollTop: number }}
 */
export function clampScrollTop(scrollTop, { contentHeight = 0, clientHeight = 0 } = {}) {
  const height = Math.max(0, Number(contentHeight) || 0);
  const client = Math.max(0, Number(clientHeight) || 0);
  const maxScrollTop = Math.max(0, height - client);
  const raw = Number(scrollTop);
  const value = Number.isFinite(raw) ? raw : 0;
  const next = Math.min(maxScrollTop, Math.max(0, value));
  return {
    scrollTop: next,
    clamped: next !== value,
    maxScrollTop,
  };
}
