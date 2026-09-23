/**
 * Commit co-performance / AI Jam ephemeral stream/accompaniment into composition.v2.
 * One transaction; never invents notes from harmony alone.
 */

import { createAppLogger } from './appLogger.js';
import { ensureNoteId, midiToPitch } from './pianoRollEvents.js';
import {
  JAM_COMMIT_MAP_INCOMPLETE,
  isJamMode,
  resolveJamRolePartition,
} from './liveJamContracts.js';
import { ensureJamRoleTracks } from './liveJamEnsureTracks.js';
import {
  MIDI_TAKE_ERROR_CODES,
  applyMidiTakeToComposition,
  extendCompositionToTick,
} from './midiTakeApply.js';
import { validateMusicJson } from './musicJsonValidation.js';

const log = createAppLogger('liveAccompaniment');
const jamLog = createAppLogger('liveJam');

/**
 * @param {Array<{ midi?: number, pitch?: number|string, start_tick: number, duration_ticks: number, velocity?: number, channel?: number }>} notes
 */
export function normalizeLiveNotesForCommit(notes) {
  const out = [];
  for (const note of notes || []) {
    if (typeof note.pitch === 'string' && note.pitch.length > 0 && Number.isNaN(Number(note.pitch))) {
      out.push({
        pitch: note.pitch,
        midi: Number.isFinite(Number(note.midi)) ? Math.round(Number(note.midi)) : undefined,
        channel: Number(note.channel) || 0,
        start_tick: Math.max(0, Math.round(Number(note.start_tick) || 0)),
        duration_ticks: Math.max(1, Math.round(Number(note.duration_ticks) || 1)),
        velocity: Math.max(1, Math.min(127, Math.round(Number(note.velocity) || 80))),
      });
      continue;
    }
    const midi = Number(note.midi ?? note.pitch);
    if (!Number.isFinite(midi) || midi < 0 || midi > 127) continue;
    const { pitch } = midiToPitch(Math.round(midi));
    if (!pitch) continue;
    out.push({
      pitch,
      midi: Math.round(midi),
      channel: Number(note.channel) || 0,
      start_tick: Math.max(0, Math.round(Number(note.start_tick) || 0)),
      duration_ticks: Math.max(1, Math.round(Number(note.duration_ticks) || 1)),
      velocity: Math.max(1, Math.min(127, Math.round(Number(note.velocity) || 80))),
    });
  }
  return out;
}

/**
 * Single-track Commit (non-jam / explicit opt-in).
 * @param {object|null} composition
 * @param {{
 *   trackId: string,
 *   streamNotes?: array,
 *   accompanimentEvents?: array,
 *   lockedTrackIds?: iterable,
 * }} opts
 */
export function applyCoPerformanceTakeToComposition(composition, opts = {}) {
  const streamNotes = normalizeLiveNotesForCommit(opts.streamNotes || []);
  const accomp = normalizeLiveNotesForCommit(
    (opts.accompanimentEvents || []).map((ev) => ({
      midi: ev.pitch,
      start_tick: ev.start_tick,
      duration_ticks: ev.duration_ticks,
      velocity: ev.velocity,
    })),
  );
  const notes = [...streamNotes, ...accomp];
  log.info('co-performance commit prepare', {
    streamCount: streamNotes.length,
    accompCount: accomp.length,
    trackId: opts.trackId ? String(opts.trackId).slice(0, 24) : null,
  });
  return applyMidiTakeToComposition(composition, {
    trackId: opts.trackId,
    notes,
    sustainPedals: [],
    lockedTrackIds: opts.lockedTrackIds,
  });
}

/**
 * Default for optional harmony-span Commit by jam mode.
 * @param {string|null|undefined} jamMode
 * @returns {boolean}
 */
export function defaultCommitHarmonySpans(jamMode) {
  return jamMode === 'user_chords';
}

/**
 * Multi-track AI Jam Commit: user stream → user-role track; AI buffer by track_role → mapped tracks.
 * Optional belief/planned_window → harmony[] metadata only (never invents notes from spans).
 *
 * @param {object|null} composition
 * @param {{
 *   jamMode: string,
 *   userTrackId?: string|null,
 *   roleTracks?: Record<string, string|null|undefined>,
 *   streamNotes?: array,
 *   accompanimentEvents?: array,
 *   ensureMissingTracks?: boolean,
 *   commitHarmonySpans?: boolean,
 *   plannedWindow?: Array<{ start_tick?: number, end_tick?: number, symbol?: string|null }>,
 *   instrumentSet?: Record<string, object>,
 *   complexity?: string,
 *   lockedTrackIds?: iterable,
 * }} opts
 */
export function applyAiJamTakeToComposition(composition, opts = {}) {
  if (!composition || typeof composition !== 'object') {
    return {
      ok: false,
      code: MIDI_TAKE_ERROR_CODES.NO_COMPOSITION,
      composition,
      noteRefs: [],
    };
  }

  const jamMode = opts.jamMode;
  if (!isJamMode(jamMode)) {
    return {
      ok: false,
      code: 'jam_mode_invalid',
      composition,
      noteRefs: [],
    };
  }

  const streamNotes = normalizeLiveNotesForCommit(opts.streamNotes || []);
  const accompanimentEvents = Array.isArray(opts.accompanimentEvents)
    ? opts.accompanimentEvents
    : [];
  const locked = new Set((opts.lockedTrackIds || []).map(String));
  const ensureMissing = Boolean(opts.ensureMissingTracks);
  const commitHarmony = opts.commitHarmonySpans != null
    ? Boolean(opts.commitHarmonySpans)
    : defaultCommitHarmonySpans(jamMode);

  const partition = resolveJamRolePartition(jamMode, opts.complexity || 'medium');
  const userRole = partition.user_roles[0] || 'melody';

  /** @type {Record<string, Array<object>>} */
  const eventsByRole = {};
  for (const ev of accompanimentEvents) {
    const role = typeof ev.track_role === 'string' && ev.track_role
      ? ev.track_role
      : 'accompaniment';
    if (!eventsByRole[role]) eventsByRole[role] = [];
    eventsByRole[role].push(ev);
  }

  /** @type {Record<string, string|null|undefined>} */
  let roleMap = { ...(opts.roleTracks || {}) };
  const instrumentSet = opts.instrumentSet || {};
  // Prefer instrument_set.track_id when role map omits a destination.
  for (const [role, entry] of Object.entries(instrumentSet)) {
    if (roleMap[role]) continue;
    if (entry && typeof entry === 'object' && entry.track_id) {
      roleMap[role] = String(entry.track_id);
    }
  }

  const userTrackId = opts.userTrackId != null && String(opts.userTrackId).trim()
    ? String(opts.userTrackId).trim()
    : (roleMap[userRole] ? String(roleMap[userRole]) : null);

  if (streamNotes.length && !userTrackId) {
    jamLog.warn('jam commit rejected — user track missing', { code: JAM_COMMIT_MAP_INCOMPLETE });
    return {
      ok: false,
      code: JAM_COMMIT_MAP_INCOMPLETE,
      composition,
      noteRefs: [],
      message: 'Map a destination track for the user role before commit.',
    };
  }

  // Roles that have AI events must be mapped (or ensureable).
  const rolesNeedingTracks = new Set();
  for (const role of Object.keys(eventsByRole)) {
    if (eventsByRole[role].length > 0) {
      rolesNeedingTracks.add(role);
    }
  }

  const trackIdsPresent = new Set(
    (composition.tracks || []).map((t) => String(t.id)),
  );

  /** @type {Record<string, string|null>} */
  const ensureRequest = {};
  for (const role of rolesNeedingTracks) {
    const mapped = roleMap[role] != null && String(roleMap[role]).trim()
      ? String(roleMap[role]).trim()
      : null;
    if (mapped && trackIdsPresent.has(mapped)) {
      roleMap[role] = mapped;
      continue;
    }
    if (!ensureMissing) {
      jamLog.warn('jam commit map incomplete', {
        code: JAM_COMMIT_MAP_INCOMPLETE,
        role: String(role).slice(0, 24),
      });
      return {
        ok: false,
        code: JAM_COMMIT_MAP_INCOMPLETE,
        composition,
        noteRefs: [],
        message: `Missing destination track for AI role: ${role}`,
      };
    }
    ensureRequest[role] = null;
  }

  let working = composition;
  let tracksEnsured = [];
  if (Object.keys(ensureRequest).length > 0) {
    const ensured = ensureJamRoleTracks(working, ensureRequest, instrumentSet);
    if (!ensured.ok) {
      return {
        ok: false,
        code: ensured.code || 'jam_ensure_failed',
        composition,
        noteRefs: [],
        message: ensured.message,
      };
    }
    working = ensured.composition;
    tracksEnsured = ensured.tracksEnsured;
    roleMap = { ...roleMap, ...ensured.roleMap };
  }

  if (userTrackId) {
    roleMap[userRole] = userTrackId;
  }

  // Locked-track guard for every destination.
  const destinations = new Set();
  if (userTrackId) destinations.add(userTrackId);
  for (const role of rolesNeedingTracks) {
    if (roleMap[role]) destinations.add(String(roleMap[role]));
  }
  for (const dest of destinations) {
    if (locked.has(dest)) {
      return {
        ok: false,
        code: MIDI_TAKE_ERROR_CODES.TRACK_LOCKED,
        composition,
        noteRefs: [],
      };
    }
  }

  if (!streamNotes.length && rolesNeedingTracks.size === 0 && !commitHarmony) {
    return {
      ok: false,
      code: MIDI_TAKE_ERROR_CODES.EMPTY_TAKE,
      composition,
      noteRefs: [],
    };
  }

  // Extend once for max end tick across all notes.
  let takeEnd = 0;
  for (const note of streamNotes) {
    takeEnd = Math.max(
      takeEnd,
      (Number(note.start_tick) || 0) + (Number(note.duration_ticks) || 0),
    );
  }
  for (const role of Object.keys(eventsByRole)) {
    for (const ev of eventsByRole[role]) {
      takeEnd = Math.max(
        takeEnd,
        (Number(ev.start_tick) || 0) + (Number(ev.duration_ticks) || 0),
      );
    }
  }
  if (commitHarmony && Array.isArray(opts.plannedWindow)) {
    for (const span of opts.plannedWindow) {
      takeEnd = Math.max(takeEnd, Number(span.end_tick) || 0);
    }
  }

  const extended = extendCompositionToTick(working, takeEnd);
  if (extended.error) {
    return {
      ok: false,
      code: extended.error,
      composition,
      noteRefs: [],
    };
  }
  working = extended.composition;
  const durationTicks = Number(working.duration_ticks);

  /** @type {Array<{ trackId: string, eventId: string }>} */
  const noteRefs = [];
  /** @type {Record<string, number>} */
  const roleCounts = {};
  let userCount = 0;

  const appendNotes = (trackId, notes, idPrefix) => {
    const trackIndex = (working.tracks || []).findIndex((t) => String(t.id) === String(trackId));
    if (trackIndex < 0) {
      return { ok: false, code: MIDI_TAKE_ERROR_CODES.NO_TRACK };
    }
    const existingEvents = Array.isArray(working.tracks[trackIndex].events)
      ? working.tracks[trackIndex].events
      : [];
    const baseIndex = existingEvents.length;
    const newEvents = [];
    notes.forEach((note, index) => {
      const start = Math.round(Number(note.start_tick) || 0);
      const duration = Math.max(1, Math.round(Number(note.duration_ticks) || 1));
      if (start < 0 || start + duration > durationTicks) return;
      if (typeof note.pitch !== 'string' || !note.pitch) return;
      const velocity = Math.max(1, Math.min(127, Math.round(Number(note.velocity) || 1)));
      const draft = {
        type: 'note',
        pitch: note.pitch,
        start_tick: start,
        duration_ticks: duration,
        velocity,
      };
      const { id } = ensureNoteId(draft, {
        trackId,
        index: baseIndex + index,
        prefix: idPrefix,
      });
      newEvents.push({ ...draft, id });
      noteRefs.push({ trackId, eventId: id });
    });
    if (!newEvents.length) {
      return { ok: true, added: 0 };
    }
    working = {
      ...working,
      tracks: working.tracks.map((track, index) => {
        if (index !== trackIndex) return track;
        return {
          ...track,
          events: [...existingEvents, ...newEvents],
          sustain_pedals: Array.isArray(track.sustain_pedals) ? track.sustain_pedals : [],
        };
      }),
    };
    return { ok: true, added: newEvents.length };
  };

  if (streamNotes.length && userTrackId) {
    const result = appendNotes(userTrackId, streamNotes, 'jam-user');
    if (!result.ok) {
      return { ok: false, code: result.code, composition, noteRefs: [] };
    }
    userCount = result.added || 0;
  }

  for (const role of Object.keys(eventsByRole)) {
    const dest = roleMap[role];
    if (!dest) continue;
    const normalized = normalizeLiveNotesForCommit(
      eventsByRole[role].map((ev) => ({
        midi: ev.pitch ?? ev.midi,
        start_tick: ev.start_tick,
        duration_ticks: ev.duration_ticks,
        velocity: ev.velocity,
      })),
    );
    const result = appendNotes(dest, normalized, `jam-${role}`);
    if (!result.ok) {
      return { ok: false, code: result.code, composition, noteRefs: [] };
    }
    roleCounts[role] = (roleCounts[role] || 0) + (result.added || 0);
  }

  let harmonySpansCommitted = 0;
  if (commitHarmony) {
    const merged = mergeHarmonySpansFromPlannedWindow(
      working.harmony,
      opts.plannedWindow,
      durationTicks,
    );
    working = { ...working, harmony: merged.spans };
    harmonySpansCommitted = merged.added;
  }

  // Normalize required V2 arrays before validation (markers / pedals).
  working = {
    ...working,
    markers: Array.isArray(working.markers) ? working.markers : [],
    tracks: (working.tracks || []).map((track) => ({
      ...track,
      sustain_pedals: Array.isArray(track.sustain_pedals) ? track.sustain_pedals : [],
    })),
  };

  if (!noteRefs.length && harmonySpansCommitted === 0) {
    return {
      ok: false,
      code: MIDI_TAKE_ERROR_CODES.EMPTY_TAKE,
      composition,
      noteRefs: [],
    };
  }

  const validation = validateMusicJson(working);
  if (!validation.valid) {
    jamLog.error('jam commit validation failed', {
      code: MIDI_TAKE_ERROR_CODES.VALIDATION_FAILED,
      message: validation.message,
    });
    return {
      ok: false,
      code: MIDI_TAKE_ERROR_CODES.VALIDATION_FAILED,
      composition,
      noteRefs: [],
      message: validation.message,
    };
  }

  const trackIdPrefixes = [...destinations].map((id) => String(id).slice(0, 16));
  jamLog.info('jam commit summary', {
    userCount,
    roleCounts,
    tracksEnsured: tracksEnsured.length,
    harmonySpansCommitted,
    trackIdPrefixes,
  });

  return {
    ok: true,
    composition: working,
    noteRefs,
    barsAdded: extended.barsAdded,
    previousDuration: extended.previousDuration,
    nextDuration: extended.nextDuration,
    userCount,
    roleCounts,
    tracksEnsured,
    harmonySpansCommitted,
    userTrackId,
    roleMap,
  };
}

/**
 * Merge planned_window into V2 harmony[] metadata (no note invention).
 * @param {array|null|undefined} existing
 * @param {array|null|undefined} plannedWindow
 * @param {number} durationTicks
 */
export function mergeHarmonySpansFromPlannedWindow(existing, plannedWindow, durationTicks) {
  const base = Array.isArray(existing)
    ? existing.map((span) => ({
      start_tick: Math.round(Number(span.start_tick) || 0),
      duration_ticks: Math.max(1, Math.round(Number(span.duration_ticks) || 1)),
      chord: typeof span.chord === 'string' ? span.chord.trim().slice(0, 32) : '',
    })).filter((s) => s.chord)
    : [];

  /** @type {Array<{ start_tick: number, duration_ticks: number, chord: string }>} */
  const incoming = [];
  if (Array.isArray(plannedWindow)) {
    for (const entry of plannedWindow) {
      const symbol = typeof entry?.symbol === 'string' ? entry.symbol.trim().slice(0, 32) : '';
      if (!symbol) continue;
      const start = Math.max(0, Math.round(Number(entry.start_tick) || 0));
      const end = Math.max(start + 1, Math.round(Number(entry.end_tick) || start + 1));
      const duration = end - start;
      if (start + duration > durationTicks) continue;
      incoming.push({ start_tick: start, duration_ticks: duration, chord: symbol });
    }
  }

  // Prefer existing spans; add incoming that do not overlap.
  const merged = [...base];
  let added = 0;
  for (const span of incoming) {
    const overlaps = merged.some((ex) => {
      const exEnd = ex.start_tick + ex.duration_ticks;
      const spanEnd = span.start_tick + span.duration_ticks;
      return span.start_tick < exEnd && ex.start_tick < spanEnd;
    });
    if (overlaps) continue;
    // Also skip duplicate start_tick
    if (merged.some((ex) => ex.start_tick === span.start_tick)) continue;
    merged.push(span);
    added += 1;
  }

  merged.sort((a, b) => a.start_tick - b.start_tick);
  return { spans: merged, added };
}
