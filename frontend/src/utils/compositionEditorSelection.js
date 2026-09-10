/**
 * Pure editor-domain helpers for multi-track note selection.
 * Note identity is `{ trackId, eventId }` because event IDs are not globally unique.
 * Keep log-free; callers own bounded DEBUG/WARN logging via appLogger.
 */

import { pitchToMidi } from './pianoRollEvents.js';
import { selectedTickBoundaries } from './pianoRollSelection.js';

/** Stable separator for note-ref keys (trackId + eventId). */
const NOTE_REF_KEY_SEP = '\u0001';

/**
 * @typedef {{ trackId: string, eventId: string }} NoteRef
 * @typedef {{ startTick: number, endTick: number, pitchMin: number, pitchMax: number }} TickPitchBox
 * @typedef {{ startTick: number, endTick: number, pitchMidi: number, trackId: string, eventId: string }} NoteGeometry
 */

/**
 * Normalize a note reference to string track/event IDs.
 * @returns {NoteRef|null}
 */
export function normalizeNoteRef(ref) {
  if (!ref || typeof ref !== 'object') {
    return null;
  }
  const trackId = ref.trackId == null ? '' : String(ref.trackId).trim();
  const eventId = ref.eventId == null ? '' : String(ref.eventId).trim();
  if (!trackId || !eventId) {
    return null;
  }
  return { trackId, eventId };
}

/**
 * Build a note reference.
 * @returns {NoteRef|null}
 */
export function makeNoteRef(trackId, eventId) {
  return normalizeNoteRef({ trackId, eventId });
}

/**
 * Stable key for Set/Map membership across tracks.
 * @param {NoteRef|{trackId: string, eventId: string}|null} ref
 * @returns {string|null}
 */
export function noteRefKey(ref) {
  const normalized = normalizeNoteRef(ref);
  if (!normalized) {
    return null;
  }
  return `${normalized.trackId}${NOTE_REF_KEY_SEP}${normalized.eventId}`;
}

/**
 * Parse a key produced by noteRefKey.
 * @returns {NoteRef|null}
 */
export function parseNoteRefKey(key) {
  if (typeof key !== 'string' || !key.includes(NOTE_REF_KEY_SEP)) {
    return null;
  }
  const sep = key.indexOf(NOTE_REF_KEY_SEP);
  return makeNoteRef(key.slice(0, sep), key.slice(sep + NOTE_REF_KEY_SEP.length));
}

/**
 * Deduplicate refs while preserving first-seen order.
 * @param {Array<NoteRef|null|undefined>} refs
 * @returns {NoteRef[]}
 */
export function uniqueNoteRefs(refs) {
  if (!Array.isArray(refs) || refs.length === 0) {
    return [];
  }
  const seen = new Set();
  const out = [];
  for (const raw of refs) {
    const ref = normalizeNoteRef(raw);
    if (!ref) {
      continue;
    }
    const key = noteRefKey(ref);
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    out.push(ref);
  }
  return out;
}

/**
 * @param {Iterable<string>|Set<string>|null|undefined} ids
 * @returns {Set<string>}
 */
export function toIdSet(ids) {
  if (ids instanceof Set) {
    return new Set([...ids].map((id) => String(id)));
  }
  if (!ids) {
    return new Set();
  }
  return new Set([...ids].map((id) => String(id)));
}

export function isTrackHidden(trackId, hiddenTrackIds) {
  if (trackId == null) {
    return false;
  }
  return toIdSet(hiddenTrackIds).has(String(trackId));
}

export function isTrackLocked(trackId, lockedTrackIds) {
  if (trackId == null) {
    return false;
  }
  return toIdSet(lockedTrackIds).has(String(trackId));
}

/**
 * Tracks that participate in hit-testing / box selection (not hidden).
 * Locked tracks remain selectable but are rejected as edit targets elsewhere.
 */
export function visibleTracks(composition, hiddenTrackIds = null) {
  const tracks = Array.isArray(composition?.tracks) ? composition.tracks : [];
  const hidden = toIdSet(hiddenTrackIds);
  return tracks.filter((track) => track?.id != null && !hidden.has(String(track.id)));
}

/**
 * Track IDs that may receive mutating edits (visible and unlocked).
 */
export function editableTrackIds(composition, { hiddenTrackIds = null, lockedTrackIds = null } = {}) {
  const locked = toIdSet(lockedTrackIds);
  return visibleTracks(composition, hiddenTrackIds)
    .map((track) => String(track.id))
    .filter((id) => !locked.has(id));
}

/**
 * Inclusive-start / exclusive-end tick bounds for a note event.
 * @returns {{ startTick: number, endTick: number }|null}
 */
export function noteTickBounds(event) {
  const startTick = Number(event?.start_tick);
  const durationTicks = Number(event?.duration_ticks);
  if (!Number.isInteger(startTick) || startTick < 0) {
    return null;
  }
  if (!Number.isInteger(durationTicks) || durationTicks <= 0) {
    return null;
  }
  return { startTick, endTick: startTick + durationTicks };
}

/**
 * @returns {number|null}
 */
export function notePitchMidi(event) {
  const { midi } = pitchToMidi(event?.pitch);
  return midi;
}

/**
 * Geometry for box intersection in tick × MIDI space.
 * Pitch is treated as a unit-height interval [midi, midi+1).
 * @returns {NoteGeometry|null}
 */
export function noteGeometry(trackId, event) {
  const ref = makeNoteRef(trackId, event?.id);
  const ticks = noteTickBounds(event);
  const pitchMidi = notePitchMidi(event);
  if (!ref || !ticks || pitchMidi === null) {
    return null;
  }
  return {
    trackId: ref.trackId,
    eventId: ref.eventId,
    startTick: ticks.startTick,
    endTick: ticks.endTick,
    pitchMidi,
  };
}

/**
 * Normalize a tick/pitch selection box. Pitch edges are inclusive integers.
 * @returns {TickPitchBox|null}
 */
export function normalizeTickPitchBox(box) {
  if (!box || typeof box !== 'object') {
    return null;
  }
  let startTick = Number(box.startTick);
  let endTick = Number(box.endTick);
  let pitchMin = Number(box.pitchMin ?? box.lowMidi ?? box.midiMin);
  let pitchMax = Number(box.pitchMax ?? box.highMidi ?? box.midiMax);
  if (![startTick, endTick, pitchMin, pitchMax].every(Number.isFinite)) {
    return null;
  }
  if (endTick < startTick) {
    const swap = startTick;
    startTick = endTick;
    endTick = swap;
  }
  if (pitchMax < pitchMin) {
    const swap = pitchMin;
    pitchMin = pitchMax;
    pitchMax = swap;
  }
  startTick = Math.round(startTick);
  endTick = Math.round(endTick);
  pitchMin = Math.round(pitchMin);
  pitchMax = Math.round(pitchMax);
  if (endTick <= startTick) {
    return null;
  }
  return { startTick, endTick, pitchMin, pitchMax };
}

/**
 * Axis-aligned intersection in tick × pitch space.
 * Tick intervals are half-open [start, end); pitch uses inclusive integer overlap
 * with note height [midi, midi+1).
 */
export function boxesIntersect(a, b) {
  const left = normalizeTickPitchBox(a);
  const right = normalizeTickPitchBox(b);
  if (!left || !right) {
    return false;
  }
  if (left.endTick <= right.startTick || right.endTick <= left.startTick) {
    return false;
  }
  // Note-as-box uses pitchMin=pitchMax=midi; overlap if ranges touch inclusively.
  if (left.pitchMax < right.pitchMin || right.pitchMax < left.pitchMin) {
    return false;
  }
  return true;
}

/**
 * Whether a note geometry intersects a tick/pitch box (including edge contact).
 */
export function noteIntersectsBox(geometry, box) {
  if (!geometry) {
    return false;
  }
  return boxesIntersect(
    {
      startTick: geometry.startTick,
      endTick: geometry.endTick,
      pitchMin: geometry.pitchMidi,
      pitchMax: geometry.pitchMidi,
    },
    box,
  );
}

/**
 * Collect note refs whose geometry intersects the box across visible tracks.
 * Hidden tracks are excluded; locked tracks may still be selected.
 * @returns {NoteRef[]}
 */
export function collectNotesInBox(composition, box, {
  hiddenTrackIds = null,
  trackIds = null,
} = {}) {
  const normalizedBox = normalizeTickPitchBox(box);
  if (!normalizedBox) {
    return [];
  }
  const allowedTracks = trackIds == null ? null : toIdSet(trackIds);
  const refs = [];
  for (const track of visibleTracks(composition, hiddenTrackIds)) {
    const trackId = String(track.id);
    if (allowedTracks && !allowedTracks.has(trackId)) {
      continue;
    }
    const events = Array.isArray(track.events) ? track.events : [];
    for (const event of events) {
      const geometry = noteGeometry(trackId, event);
      if (geometry && noteIntersectsBox(geometry, normalizedBox)) {
        refs.push({ trackId: geometry.trackId, eventId: geometry.eventId });
      }
    }
  }
  return uniqueNoteRefs(refs);
}

/**
 * Aggregate tick range over selected notes (inclusive start / exclusive end union).
 * @returns {{ startTick: number, endTick: number, noteCount: number }|null}
 */
export function selectionTickRange(composition, refs) {
  const resolved = resolveNoteRefs(composition, refs, { includeHidden: true, includeLocked: true });
  if (!resolved.length) {
    return null;
  }
  let startTick = Number.POSITIVE_INFINITY;
  let endTick = Number.NEGATIVE_INFINITY;
  for (const item of resolved) {
    startTick = Math.min(startTick, item.geometry.startTick);
    endTick = Math.max(endTick, item.geometry.endTick);
  }
  return { startTick, endTick, noteCount: resolved.length };
}

/**
 * Aggregate MIDI pitch range over selected notes (inclusive).
 * @returns {{ pitchMin: number, pitchMax: number, noteCount: number }|null}
 */
export function selectionPitchRange(composition, refs) {
  const resolved = resolveNoteRefs(composition, refs, { includeHidden: true, includeLocked: true });
  if (!resolved.length) {
    return null;
  }
  let pitchMin = Number.POSITIVE_INFINITY;
  let pitchMax = Number.NEGATIVE_INFINITY;
  for (const item of resolved) {
    pitchMin = Math.min(pitchMin, item.geometry.pitchMidi);
    pitchMax = Math.max(pitchMax, item.geometry.pitchMidi);
  }
  return { pitchMin, pitchMax, noteCount: resolved.length };
}

/**
 * Tick window for notes overlapping an inclusive bar range (variable-meter aware).
 * Reuses piano-roll bar primitives without merging AI-region UI state.
 */
export function selectionTicksForBarRange(startBar, endBar, {
  composition = null,
  timeSignature = null,
  ticksPerQuarter = null,
  durationTicks = null,
} = {}) {
  return selectedTickBoundaries(startBar, endBar, {
    composition,
    timeSignature: timeSignature ?? composition?.time_signature ?? '4/4',
    ticksPerQuarter: ticksPerQuarter ?? composition?.ticks_per_quarter ?? 480,
    durationTicks: durationTicks ?? composition?.duration_ticks ?? null,
  });
}

/**
 * Notes that overlap a bar range on visible tracks.
 * @returns {NoteRef[]}
 */
export function collectNotesInBarRange(composition, startBar, endBar, {
  hiddenTrackIds = null,
} = {}) {
  const bounds = selectionTicksForBarRange(startBar, endBar, { composition });
  if (bounds.startTick == null || bounds.endTick == null) {
    return [];
  }
  return collectNotesInBox(composition, {
    startTick: bounds.startTick,
    endTick: bounds.endTick,
    pitchMin: 0,
    pitchMax: 127,
  }, { hiddenTrackIds });
}

/**
 * Resolve refs against the current composition.
 * @returns {Array<{ ref: NoteRef, track: object, event: object, geometry: NoteGeometry, locked: boolean, hidden: boolean }>}
 */
export function resolveNoteRefs(composition, refs, {
  hiddenTrackIds = null,
  lockedTrackIds = null,
  includeHidden = false,
  includeLocked = true,
} = {}) {
  const tracks = Array.isArray(composition?.tracks) ? composition.tracks : [];
  const trackById = new Map(tracks.map((track) => [String(track.id), track]));
  const hidden = toIdSet(hiddenTrackIds);
  const locked = toIdSet(lockedTrackIds);
  const out = [];
  for (const raw of uniqueNoteRefs(refs)) {
    const track = trackById.get(raw.trackId);
    if (!track) {
      continue;
    }
    const isHidden = hidden.has(raw.trackId);
    const isLocked = locked.has(raw.trackId);
    if (!includeHidden && isHidden) {
      continue;
    }
    if (!includeLocked && isLocked) {
      continue;
    }
    const events = Array.isArray(track.events) ? track.events : [];
    const event = events.find((item) => String(item?.id) === raw.eventId);
    if (!event) {
      continue;
    }
    const geometry = noteGeometry(raw.trackId, event);
    if (!geometry) {
      continue;
    }
    out.push({
      ref: raw,
      track,
      event,
      geometry,
      locked: isLocked,
      hidden: isHidden,
    });
  }
  return out;
}

/**
 * Drop stale refs after composition changes; optionally drop hidden-track hits.
 * Primary is preserved when still present, else first remaining ref.
 * @returns {{ refs: NoteRef[], primary: NoteRef|null, droppedCount: number }}
 */
export function reconcileSelection(composition, selection, {
  hiddenTrackIds = null,
  dropHidden = true,
} = {}) {
  const inputRefs = Array.isArray(selection?.refs)
    ? selection.refs
    : Array.isArray(selection)
      ? selection
      : [];
  const primaryIn = normalizeNoteRef(selection?.primary ?? null);
  const resolved = resolveNoteRefs(composition, inputRefs, {
    hiddenTrackIds,
    includeHidden: !dropHidden,
    includeLocked: true,
  });
  const refs = resolved.map((item) => item.ref);
  const droppedCount = uniqueNoteRefs(inputRefs).length - refs.length;
  let primary = null;
  if (primaryIn) {
    const primaryKey = noteRefKey(primaryIn);
    primary = refs.find((ref) => noteRefKey(ref) === primaryKey) || null;
  }
  if (!primary && refs.length) {
    primary = refs[0];
  }
  return { refs, primary, droppedCount };
}

/**
 * Shift-range extension between two anchors using tick then pitch then track order.
 * Includes notes on visible tracks whose sort key lies between the anchors (inclusive).
 * @returns {NoteRef[]}
 */
export function rangeSelectBetween(composition, fromRef, toRef, {
  hiddenTrackIds = null,
} = {}) {
  const from = normalizeNoteRef(fromRef);
  const to = normalizeNoteRef(toRef);
  if (!from || !to) {
    return uniqueNoteRefs([from, to].filter(Boolean));
  }
  const resolved = [];
  for (const track of visibleTracks(composition, hiddenTrackIds)) {
    const trackId = String(track.id);
    const events = Array.isArray(track.events) ? track.events : [];
    for (const event of events) {
      const geometry = noteGeometry(trackId, event);
      if (geometry) {
        resolved.push(geometry);
      }
    }
  }
  if (!resolved.length) {
    return [];
  }
  const sortKey = (geometry) => (
    `${String(geometry.startTick).padStart(12, '0')}`
    + `:${String(127 - geometry.pitchMidi).padStart(3, '0')}`
    + `:${geometry.trackId}`
    + `:${geometry.eventId}`
  );
  resolved.sort((a, b) => {
    const ka = sortKey(a);
    const kb = sortKey(b);
    return ka < kb ? -1 : ka > kb ? 1 : 0;
  });
  const fromKey = noteRefKey(from);
  const toKey = noteRefKey(to);
  let fromIndex = resolved.findIndex((item) => noteRefKey(item) === fromKey);
  let toIndex = resolved.findIndex((item) => noteRefKey(item) === toKey);
  if (fromIndex < 0 && toIndex < 0) {
    return [];
  }
  if (fromIndex < 0) {
    fromIndex = toIndex;
  }
  if (toIndex < 0) {
    toIndex = fromIndex;
  }
  const start = Math.min(fromIndex, toIndex);
  const end = Math.max(fromIndex, toIndex);
  return resolved.slice(start, end + 1).map((item) => ({
    trackId: item.trackId,
    eventId: item.eventId,
  }));
}

/**
 * Compact summary safe for logging (no pitches / event payloads).
 */
export function selectionSummary(composition, refs, {
  hiddenTrackIds = null,
  lockedTrackIds = null,
} = {}) {
  const resolved = resolveNoteRefs(composition, refs, {
    hiddenTrackIds,
    lockedTrackIds,
    includeHidden: true,
    includeLocked: true,
  });
  const trackIds = new Set(resolved.map((item) => item.ref.trackId));
  const lockedCount = resolved.filter((item) => item.locked).length;
  const hiddenCount = resolved.filter((item) => item.hidden).length;
  const ticks = selectionTickRange(composition, resolved.map((item) => item.ref));
  return {
    selectedCount: resolved.length,
    trackCount: trackIds.size,
    lockedCount,
    hiddenCount,
    startTick: ticks?.startTick ?? null,
    endTick: ticks?.endTick ?? null,
  };
}
