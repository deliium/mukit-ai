/**
 * Durable audio.recovery.result.v1 overlay lifecycle helpers.
 * Confidence / stem provenance live here — never on composition.v2 note events.
 *
 * Logging: createAppLogger('audioRecovery') — counts / status only.
 */

import { createAppLogger } from './appLogger.js';
import { pitchToMidi } from './pianoRollEvents.js';

const log = createAppLogger('audioRecovery');

export const OVERLAY_STATUS = Object.freeze({
  RECOVERED: 'recovered',
  USER_EDITED: 'user_edited',
  USER: 'user',
  EXCLUDED: 'excluded',
});

/**
 * @typedef {{
 *   event_id: string,
 *   track_id?: string|null,
 *   confidence: number,
 *   stem?: string|null,
 *   provisional_id?: string|null,
 *   status?: string,
 * }} OverlayEntry
 */

/**
 * @param {OverlayEntry[]|null|undefined} overlay
 * @returns {Map<string, OverlayEntry>}
 */
export function overlayByEventId(overlay) {
  /** @type {Map<string, OverlayEntry>} */
  const map = new Map();
  for (const entry of Array.isArray(overlay) ? overlay : []) {
    const id = entry?.event_id != null ? String(entry.event_id) : '';
    if (!id) continue;
    map.set(id, { ...entry, event_id: id });
  }
  return map;
}

/**
 * Build overlay entries from Apply event_map + preview notes (confidence/stem).
 *
 * @param {Array<{ provisional_id: string, event_id: string, track_id?: string }>} eventMap
 * @param {Array<{ provisional_id?: string, confidence?: number, stem?: string }>} previewNotes
 * @returns {OverlayEntry[]}
 */
export function buildOverlayFromEventMap(eventMap, previewNotes) {
  const byProv = new Map();
  for (const note of Array.isArray(previewNotes) ? previewNotes : []) {
    const pid = note?.provisional_id != null ? String(note.provisional_id) : '';
    if (pid) byProv.set(pid, note);
  }
  const overlay = [];
  for (const row of Array.isArray(eventMap) ? eventMap : []) {
    const provisionalId = row?.provisional_id != null ? String(row.provisional_id) : '';
    const eventId = row?.event_id != null ? String(row.event_id) : '';
    if (!provisionalId || !eventId) continue;
    const note = byProv.get(provisionalId);
    if (!note) continue;
    overlay.push({
      event_id: eventId,
      track_id: row.track_id != null ? String(row.track_id) : null,
      confidence: Number(note.confidence),
      stem: note.stem != null ? String(note.stem) : null,
      provisional_id: provisionalId,
      status: OVERLAY_STATUS.RECOVERED,
    });
  }
  log.debug('Overlay built from event map', {
    eventMapCount: Array.isArray(eventMap) ? eventMap.length : 0,
    overlayCount: overlay.length,
  });
  return overlay;
}

/**
 * Prune overlay entries whose event_id is no longer present in composition.
 *
 * @param {OverlayEntry[]|null|undefined} overlay
 * @param {object|null} composition
 * @returns {{ overlay: OverlayEntry[], prunedCount: number }}
 */
export function pruneOverlayForComposition(overlay, composition) {
  const alive = new Set();
  for (const track of Array.isArray(composition?.tracks) ? composition.tracks : []) {
    for (const ev of Array.isArray(track?.events) ? track.events : []) {
      if (ev?.id != null) alive.add(String(ev.id));
    }
  }
  const next = [];
  let prunedCount = 0;
  for (const entry of Array.isArray(overlay) ? overlay : []) {
    const id = entry?.event_id != null ? String(entry.event_id) : '';
    if (!id || !alive.has(id)) {
      prunedCount += 1;
      continue;
    }
    next.push(entry);
  }
  if (prunedCount > 0) {
    log.info('Recovery overlay pruned', { prunedCount, remaining: next.length });
  }
  return { overlay: next, prunedCount };
}

/**
 * Mark recovered overlay entries as user_edited when pitch/time changed.
 *
 * @param {OverlayEntry[]|null|undefined} overlay
 * @param {Array<{ eventId: string, pitchChanged?: boolean, timeChanged?: boolean }>} changes
 * @returns {{ overlay: OverlayEntry[], markedCount: number }}
 */
export function markOverlayUserEdited(overlay, changes) {
  const changeMap = new Map();
  for (const ch of Array.isArray(changes) ? changes : []) {
    const id = ch?.eventId != null ? String(ch.eventId) : '';
    if (!id) continue;
    if (ch.pitchChanged || ch.timeChanged) {
      changeMap.set(id, true);
    }
  }
  if (!changeMap.size) {
    return { overlay: Array.isArray(overlay) ? overlay.map((e) => ({ ...e })) : [], markedCount: 0 };
  }
  let markedCount = 0;
  const next = (Array.isArray(overlay) ? overlay : []).map((entry) => {
    const id = entry?.event_id != null ? String(entry.event_id) : '';
    if (!id || !changeMap.has(id)) return entry;
    if (entry.status === OVERLAY_STATUS.USER_EDITED) return entry;
    markedCount += 1;
    return { ...entry, status: OVERLAY_STATUS.USER_EDITED };
  });
  if (markedCount > 0) {
    log.info('Recovery overlay marked user_edited', { markedCount });
  }
  return { overlay: next, markedCount };
}

/**
 * New user-drawn notes have no recovery confidence (optional status:user stub omitted by default).
 *
 * @param {OverlayEntry[]|null|undefined} overlay
 * @param {string} eventId
 * @returns {OverlayEntry|null}
 */
export function getOverlayEntry(overlay, eventId) {
  const id = eventId != null ? String(eventId) : '';
  if (!id) return null;
  return overlayByEventId(overlay).get(id) || null;
}

/** Product stem → ghost fill (provisional piano-roll). */
export const RECOVERY_STEM_COLORS = Object.freeze({
  vocals: 'rgba(168, 85, 247, 0.55)',
  melody: 'rgba(245, 158, 11, 0.55)',
  bass: 'rgba(59, 130, 246, 0.55)',
  drums: 'rgba(239, 68, 68, 0.45)',
  harmonic: 'rgba(16, 185, 129, 0.5)',
  other: 'rgba(107, 114, 128, 0.5)',
});

/**
 * @param {string|null|undefined} stem
 * @returns {string}
 */
export function recoveryStemFill(stem) {
  const key = stem != null ? String(stem) : '';
  return RECOVERY_STEM_COLORS[key] || RECOVERY_STEM_COLORS.other;
}

/**
 * Index note events by id across all tracks.
 *
 * @param {object|null|undefined} composition
 * @returns {Map<string, { pitch: string|number, start_tick: number, duration_ticks: number, track_id: string }>}
 */
export function indexCompositionEventsById(composition) {
  /** @type {Map<string, { pitch: string|number, start_tick: number, duration_ticks: number, track_id: string }>} */
  const map = new Map();
  for (const track of Array.isArray(composition?.tracks) ? composition.tracks : []) {
    const trackId = track?.id != null ? String(track.id) : '';
    for (const ev of Array.isArray(track?.events) ? track.events : []) {
      const id = ev?.id != null ? String(ev.id) : '';
      if (!id) continue;
      map.set(id, {
        pitch: ev.pitch,
        start_tick: Number(ev.start_tick),
        duration_ticks: Number(ev.duration_ticks),
        track_id: trackId,
      });
    }
  }
  return map;
}

/**
 * Detect pitch/time edits on recovered event ids still present in both compositions.
 *
 * @param {object|null|undefined} prevComposition
 * @param {object|null|undefined} nextComposition
 * @param {OverlayEntry[]|null|undefined} overlay
 * @returns {Array<{ eventId: string, pitchChanged: boolean, timeChanged: boolean }>}
 */
export function detectOverlayPitchTimeChanges(prevComposition, nextComposition, overlay) {
  if (!Array.isArray(overlay) || !overlay.length) return [];
  const prevById = indexCompositionEventsById(prevComposition);
  const nextById = indexCompositionEventsById(nextComposition);
  /** @type {Array<{ eventId: string, pitchChanged: boolean, timeChanged: boolean }>} */
  const changes = [];
  for (const entry of overlay) {
    const id = entry?.event_id != null ? String(entry.event_id) : '';
    if (!id) continue;
    const prev = prevById.get(id);
    const next = nextById.get(id);
    if (!prev || !next) continue;
    const pitchChanged = String(prev.pitch) !== String(next.pitch);
    const timeChanged = prev.start_tick !== next.start_tick
      || prev.duration_ticks !== next.duration_ticks;
    if (pitchChanged || timeChanged) {
      changes.push({ eventId: id, pitchChanged, timeChanged });
    }
  }
  return changes;
}

/**
 * Prune deleted recovered ids and mark pitch/time edits as user_edited.
 * Call from composition transactions when a Bind overlay is present.
 *
 * @param {OverlayEntry[]|null|undefined} overlay
 * @param {object|null|undefined} prevComposition
 * @param {object|null|undefined} nextComposition
 * @returns {{ overlay: OverlayEntry[], prunedCount: number, markedCount: number }}
 */
export function syncOverlayAfterCompositionEdit(overlay, prevComposition, nextComposition) {
  if (!Array.isArray(overlay) || !overlay.length) {
    return { overlay: [], prunedCount: 0, markedCount: 0 };
  }
  const changes = detectOverlayPitchTimeChanges(prevComposition, nextComposition, overlay);
  const pruned = pruneOverlayForComposition(overlay, nextComposition);
  const marked = markOverlayUserEdited(pruned.overlay, changes);
  if (pruned.prunedCount || marked.markedCount) {
    log.info('Recovery overlay synced after composition edit', {
      prunedCount: pruned.prunedCount,
      markedCount: marked.markedCount,
      remaining: marked.overlay.length,
    });
  }
  return {
    overlay: marked.overlay,
    prunedCount: pruned.prunedCount,
    markedCount: marked.markedCount,
  };
}

/**
 * Geometry for pre-Apply recovery provisional ghosts (multi-stem).
 *
 * @param {Array<object>|null|undefined} notes
 * @param {string[]|Set<string>|null|undefined} selectedIds
 * @param {{
 *   pixelsPerTick: number,
 *   pitchMidiMax: number,
 *   pitchMidiMin: number,
 *   rowHeight?: number,
 *   threshold?: number,
 * }} layout
 * @returns {Array<{
 *   id: string,
 *   left: number,
 *   top: number,
 *   width: number,
 *   height: number,
 *   low: boolean,
 *   stem: string|null,
 *   fill: string,
 * }>}
 */
export function buildRecoveryProvisionalGeoms(notes, selectedIds, layout) {
  const {
    pixelsPerTick,
    pitchMidiMax,
    pitchMidiMin,
    rowHeight = 16,
    threshold = 0.5,
  } = layout || {};
  if (
    !Number.isFinite(pixelsPerTick)
    || pixelsPerTick <= 0
    || pitchMidiMax == null
    || pitchMidiMin == null
  ) {
    return [];
  }
  const selected = selectedIds instanceof Set
    ? selectedIds
    : new Set((Array.isArray(selectedIds) ? selectedIds : []).map(String));
  const height = Math.max(4, Number(rowHeight) || 16);
  const thr = Number.isFinite(Number(threshold)) ? Number(threshold) : 0.5;
  return (Array.isArray(notes) ? notes : [])
    .filter((note) => selected.has(String(note.provisional_id)))
    .map((note) => {
      const midi = Math.round(Number(note.pitch));
      if (midi < pitchMidiMin || midi > pitchMidiMax) return null;
      const start = Math.max(0, Number(note.start_tick) || 0);
      const duration = Math.max(1, Number(note.duration_ticks) || 1);
      const stem = note.stem != null ? String(note.stem) : null;
      return {
        id: String(note.provisional_id),
        left: start * pixelsPerTick,
        width: duration * pixelsPerTick,
        top: (pitchMidiMax - midi) * height,
        height: height - 2,
        low: Number(note.confidence) < thr,
        stem,
        fill: recoveryStemFill(stem),
      };
    })
    .filter(Boolean);
}

/**
 * Geometry for post-Bind confidence indicators over applied V2 notes.
 *
 * @param {object|null|undefined} composition
 * @param {OverlayEntry[]|null|undefined} overlay
 * @param {{
 *   pixelsPerTick: number,
 *   pitchMidiMax: number,
 *   pitchMidiMin: number,
 *   rowHeight?: number,
 *   threshold?: number,
 * }} layout
 * @returns {Array<{
 *   id: string,
 *   left: number,
 *   top: number,
 *   width: number,
 *   height: number,
 *   low: boolean,
 *   status: string,
 *   stem: string|null,
 * }>}
 */
export function buildRecoveryBoundConfidenceGeoms(composition, overlay, layout) {
  const {
    pixelsPerTick,
    pitchMidiMax,
    pitchMidiMin,
    rowHeight = 16,
    threshold = 0.5,
  } = layout || {};
  if (
    !Array.isArray(overlay)
    || !overlay.length
    || !Number.isFinite(pixelsPerTick)
    || pixelsPerTick <= 0
    || pitchMidiMax == null
    || pitchMidiMin == null
  ) {
    return [];
  }
  const byId = indexCompositionEventsById(composition);
  const height = Math.max(4, Number(rowHeight) || 16);
  const thr = Number.isFinite(Number(threshold)) ? Number(threshold) : 0.5;
  const geoms = [];
  for (const entry of overlay) {
    const id = entry?.event_id != null ? String(entry.event_id) : '';
    if (!id) continue;
    const status = entry.status != null ? String(entry.status) : OVERLAY_STATUS.RECOVERED;
    if (status === OVERLAY_STATUS.EXCLUDED || status === OVERLAY_STATUS.USER) continue;
    const ev = byId.get(id);
    if (!ev) continue;
    let midi;
    if (typeof ev.pitch === 'number' && Number.isFinite(ev.pitch)) {
      midi = Math.round(ev.pitch);
    } else {
      midi = pitchToMidi(String(ev.pitch)).midi;
    }
    if (midi == null || midi < pitchMidiMin || midi > pitchMidiMax) continue;
    const start = Math.max(0, Number(ev.start_tick) || 0);
    const duration = Math.max(1, Number(ev.duration_ticks) || 1);
    geoms.push({
      id,
      left: start * pixelsPerTick,
      width: duration * pixelsPerTick,
      top: (pitchMidiMax - midi) * height,
      height: height - 2,
      low: Number(entry.confidence) < thr,
      status,
      stem: entry.stem != null ? String(entry.stem) : null,
    });
  }
  return geoms;
}
