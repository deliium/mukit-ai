/**
 * Pure, log-free bulk note operations for the V2 composition editor.
 * Callers own validation commits, history, and appLogger summaries.
 */

import {
  isTrackLocked,
  makeNoteRef,
  noteRefKey,
  resolveNoteRefs,
  selectionTickRange,
  uniqueNoteRefs,
} from './compositionEditorSelection.js';
import { applyMotifReconciliation } from './compositionMotifs.js';
import {
  ARTICULATION_VALUES,
  DEFAULT_NOTE_VELOCITY,
  MIN_NOTE_DURATION_TICKS,
  clampNoteTiming,
  ensureNoteId,
  midiToPitch,
  pitchToMidi,
  snapIntervalTicks,
  snapTick,
} from './pianoRollEvents.js';

export const CLIPBOARD_VERSION = 1;

export const REJECT = Object.freeze({
  empty_selection: 'empty_selection',
  missing_targets: 'missing_targets',
  locked_targets: 'locked_targets',
  pitch_out_of_range: 'pitch_out_of_range',
  invalid_timing: 'invalid_timing',
  empty_clipboard: 'empty_clipboard',
  invalid_options: 'invalid_options',
  no_op: 'no_op',
});

const GATE_SHORTENING = new Set(['staccato', 'staccatissimo', 'marcato']);
const ATTACK = new Set(['accent', 'marcato']);

function fail(code, message, summary = undefined) {
  const result = { ok: false, code, message };
  if (summary !== undefined) {
    result.summary = summary;
  }
  return result;
}

function okResult(composition, selection, summary) {
  return {
    ok: true,
    composition,
    selection: {
      refs: selection?.refs ?? [],
      primary: selection?.primary ?? null,
    },
    summary: summary ?? {},
  };
}

function selectionFromRefs(refs, primary = null) {
  const list = uniqueNoteRefs(refs);
  let primaryRef = primary ? makeNoteRef(primary.trackId, primary.eventId) : null;
  if (primaryRef) {
    const key = noteRefKey(primaryRef);
    if (!list.some((ref) => noteRefKey(ref) === key)) {
      primaryRef = null;
    }
  }
  if (!primaryRef && list.length) {
    primaryRef = list[0];
  }
  return { refs: list, primary: primaryRef };
}

function compositionDuration(composition) {
  const value = Number(composition?.duration_ticks);
  return Number.isInteger(value) && value > 0 ? value : null;
}

function trackIndexById(composition) {
  const map = new Map();
  const tracks = Array.isArray(composition?.tracks) ? composition.tracks : [];
  tracks.forEach((track, index) => {
    if (track?.id != null) {
      map.set(String(track.id), index);
    }
  });
  return map;
}

function collectEventIds(composition) {
  const ids = new Set();
  for (const track of composition?.tracks || []) {
    for (const event of track.events || []) {
      if (event?.id != null && String(event.id).trim()) {
        ids.add(String(event.id));
      }
    }
  }
  return ids;
}

function allocateEventId(draft, usedIds, trackId, index, prefix = 'note') {
  let { id } = ensureNoteId({ ...draft, id: undefined }, { trackId, index, prefix });
  if (!usedIds.has(id)) {
    usedIds.add(id);
    return id;
  }
  let suffix = 0;
  while (usedIds.has(`${id}-${suffix}`)) {
    suffix += 1;
  }
  const next = `${id}-${suffix}`;
  usedIds.add(next);
  return next;
}

function clampVelocity(value, fallback = DEFAULT_NOTE_VELOCITY) {
  const n = Number(value);
  if (!Number.isFinite(n)) {
    return Math.max(1, Math.min(127, fallback));
  }
  return Math.max(1, Math.min(127, Math.round(n)));
}

function articulationConflict(names) {
  if (names.includes('staccato') && names.includes('staccatissimo')) {
    return 'staccato and staccatissimo cannot combine';
  }
  if ((names.includes('staccato') || names.includes('staccatissimo')) && names.includes('tenuto')) {
    return 'short articulations cannot combine with tenuto';
  }
  if (names.includes('accent') && names.includes('marcato')) {
    return 'accent and marcato cannot combine';
  }
  return null;
}

function validateArticulationSet(names, tieType = null) {
  const unique = [...new Set(names)];
  const conflict = articulationConflict(unique);
  if (conflict) {
    return conflict;
  }
  for (const name of unique) {
    if (!ARTICULATION_VALUES.includes(name)) {
      return `unsupported articulation: ${name}`;
    }
  }
  if (tieType) {
    if (unique.some((name) => GATE_SHORTENING.has(name))) {
      return 'gate-shortening articulations are not allowed in a tie chain';
    }
    if (unique.some((name) => ATTACK.has(name)) && tieType !== 'start') {
      return 'attack articulations are only allowed on the tie chain head';
    }
  }
  return null;
}

/**
 * Resolve refs for mutation: reject empty, missing, or locked targets atomically.
 * @returns {{ ok: true, resolved: Array, refs: Array } | { ok: false, code: string, message: string }}
 */
export function resolveMutationTargets(composition, refs, lockedTrackIds = null) {
  const unique = uniqueNoteRefs(refs);
  if (!unique.length) {
    return fail(REJECT.empty_selection, 'No notes selected');
  }
  const resolved = resolveNoteRefs(composition, unique, {
    lockedTrackIds,
    includeHidden: true,
    includeLocked: true,
  });
  if (resolved.length !== unique.length) {
    return fail(REJECT.missing_targets, 'One or more selected notes are missing');
  }
  if (resolved.some((item) => item.locked || isTrackLocked(item.ref.trackId, lockedTrackIds))) {
    return fail(REJECT.locked_targets, 'Selection includes locked tracks');
  }
  return { ok: true, resolved, refs: unique };
}

function eventVoiceKey(event) {
  const staff = event?.staff == null ? '' : String(event.staff);
  const voice = event?.voice == null ? '' : String(event.voice);
  return `${staff}\u0001${voice}`;
}

function notesCompatibleForTie(left, right) {
  return left.pitch === right.pitch
    && left.staff === right.staff
    && left.voice === right.voice;
}

function isValidTieChain(events) {
  if (!Array.isArray(events) || events.length < 2) {
    return false;
  }
  const sorted = [...events].sort(
    (a, b) => a.start_tick - b.start_tick || a.duration_ticks - b.duration_ticks,
  );
  const head = sorted[0];
  for (let i = 1; i < sorted.length; i += 1) {
    const previous = sorted[i - 1];
    const current = sorted[i];
    if (!notesCompatibleForTie(head, current)) {
      return false;
    }
    if (current.start_tick !== previous.start_tick + previous.duration_ticks) {
      return false;
    }
  }
  const types = sorted.map((event) => event.tie?.type);
  if (types[0] !== 'start' || types[types.length - 1] !== 'stop') {
    return false;
  }
  for (let i = 1; i < types.length - 1; i += 1) {
    if (types[i] !== 'continue') {
      return false;
    }
  }
  return true;
}

function cloneClipboardEventFields(event) {
  const note = {
    type: event.type || 'note',
    pitch: event.pitch,
    duration_ticks: event.duration_ticks,
    velocity: event.velocity,
    articulations: Array.isArray(event.articulations) ? [...event.articulations] : [],
    tie: event.tie
      ? { group_id: event.tie.group_id, type: event.tie.type }
      : null,
  };
  if (event.staff !== undefined) {
    note.staff = event.staff;
  }
  if (event.voice !== undefined) {
    note.voice = event.voice;
  }
  return note;
}

/**
 * Build a clipboard payload from selected notes (no motif metadata).
 * @returns {{ ok: true, clipboard: object, summary: object } | { ok: false, code: string, message: string, summary?: object }}
 */
export function copyNotes(composition, refs) {
  const unique = uniqueNoteRefs(refs);
  if (!unique.length) {
    return fail(REJECT.empty_selection, 'No notes selected');
  }
  const resolved = resolveNoteRefs(composition, unique, {
    includeHidden: true,
    includeLocked: true,
  });
  if (resolved.length !== unique.length) {
    return fail(REJECT.missing_targets, 'One or more selected notes are missing');
  }

  const trackOrder = trackIndexById(composition);
  const sorted = [...resolved].sort((a, b) => {
    const ta = trackOrder.get(a.ref.trackId) ?? 0;
    const tb = trackOrder.get(b.ref.trackId) ?? 0;
    if (ta !== tb) {
      return ta - tb;
    }
    return a.geometry.startTick - b.geometry.startTick
      || a.geometry.endTick - b.geometry.endTick
      || a.ref.eventId.localeCompare(b.ref.eventId);
  });

  const anchorTick = Math.min(...sorted.map((item) => item.geometry.startTick));
  const trackIds = [];
  const seenTracks = new Set();
  for (const item of sorted) {
    if (!seenTracks.has(item.ref.trackId)) {
      seenTracks.add(item.ref.trackId);
      trackIds.push(item.ref.trackId);
    }
  }
  const trackIndex = new Map(trackIds.map((id, index) => [id, index]));

  const notes = sorted.map((item) => {
    const fields = cloneClipboardEventFields(item.event);
    return {
      trackId: item.ref.trackId,
      relativeStartTick: item.geometry.startTick - anchorTick,
      relativeTrackIndex: trackIndex.get(item.ref.trackId),
      ...fields,
    };
  });

  const clipboard = {
    notes,
    anchorTick,
    trackIds,
    version: CLIPBOARD_VERSION,
  };

  return {
    ok: true,
    clipboard,
    summary: {
      noteCount: notes.length,
      trackCount: trackIds.length,
      anchorTick,
    },
  };
}

function clearBrokenTieGroups(events, removedGroupIds) {
  if (!removedGroupIds.size) {
    return events;
  }
  let changed = false;
  const next = events.map((event) => {
    if (event.tie?.group_id && removedGroupIds.has(event.tie.group_id)) {
      changed = true;
      return { ...event, tie: null };
    }
    return event;
  });
  return changed ? next : events;
}

/**
 * Atomically delete notes; reconcile motifs for removed event IDs.
 */
export function deleteNotes(composition, refs, { lockedTrackIds = null } = {}) {
  const targets = resolveMutationTargets(composition, refs, lockedTrackIds);
  if (!targets.ok) {
    return targets;
  }

  const removeByTrack = new Map();
  const removedEventIds = [];
  const affectedGroupIds = new Set();

  for (const item of targets.resolved) {
    if (!removeByTrack.has(item.ref.trackId)) {
      removeByTrack.set(item.ref.trackId, new Set());
    }
    removeByTrack.get(item.ref.trackId).add(item.ref.eventId);
    removedEventIds.push(item.ref.eventId);
    if (item.event.tie?.group_id) {
      affectedGroupIds.add(item.event.tie.group_id);
    }
  }

  const tracks = composition.tracks.map((track) => {
    const removeIds = removeByTrack.get(String(track.id));
    if (!removeIds) {
      return track;
    }
    const events = Array.isArray(track.events) ? track.events : [];
    const remaining = [];
    const groupsTouched = new Set();
    for (const event of events) {
      if (removeIds.has(String(event.id))) {
        if (event.tie?.group_id) {
          groupsTouched.add(event.tie.group_id);
        }
        continue;
      }
      remaining.push(event);
    }
    const cleared = clearBrokenTieGroups(remaining, groupsTouched);
    return { ...track, events: cleared };
  });

  let nextComposition = { ...composition, tracks };
  if (Array.isArray(composition.motifs) && composition.motifs.length && removedEventIds.length) {
    nextComposition = applyMotifReconciliation(nextComposition, removedEventIds).composition;
  }

  return okResult(nextComposition, selectionFromRefs([]), {
    deletedCount: targets.resolved.length,
    trackCount: removeByTrack.size,
    removedEventIds: removedEventIds.length,
  });
}

/**
 * Copy then atomically delete.
 */
export function cutNotes(composition, refs, { lockedTrackIds = null } = {}) {
  const copied = copyNotes(composition, refs);
  if (!copied.ok) {
    return copied;
  }
  const locked = resolveMutationTargets(composition, refs, lockedTrackIds);
  if (!locked.ok) {
    return locked;
  }
  const deleted = deleteNotes(composition, refs, { lockedTrackIds });
  if (!deleted.ok) {
    return deleted;
  }
  return {
    ...deleted,
    clipboard: copied.clipboard,
    summary: {
      ...deleted.summary,
      noteCount: copied.summary.noteCount,
      trackCount: copied.summary.trackCount,
    },
  };
}

function remapClipboardTies(pastedNotes) {
  const byGroup = new Map();
  for (const note of pastedNotes) {
    const groupId = note.tie?.group_id;
    if (!groupId) {
      continue;
    }
    if (!byGroup.has(groupId)) {
      byGroup.set(groupId, []);
    }
    byGroup.get(groupId).push(note);
  }

  let droppedTieCount = 0;
  for (const [oldGroupId, members] of byGroup) {
    const trackIds = new Set(members.map((note) => note._targetTrackId));
    if (trackIds.size !== 1 || !isValidTieChain(members)) {
      for (const note of members) {
        note.tie = null;
      }
      droppedTieCount += members.length;
      continue;
    }
    const trackId = members[0]._targetTrackId;
    const newGroupId = `tie-${trackId}-${oldGroupId}-${members.length}`;
    const sorted = [...members].sort(
      (a, b) => a.start_tick - b.start_tick || a.duration_ticks - b.duration_ticks,
    );
    sorted.forEach((note, index) => {
      let type = 'continue';
      if (index === 0) {
        type = 'start';
      } else if (index === sorted.length - 1) {
        type = 'stop';
      }
      note.tie = { group_id: newGroupId, type };
    });
  }
  return droppedTieCount;
}

/**
 * Paste clipboard notes at pasteTick with collision-free IDs and rebuilt ties.
 */
export function pasteNotes(composition, clipboard, {
  pasteTick = 0,
  activeTrackId = null,
  lockedTrackIds = null,
  preferActiveTrackForSingleTrackClip = false,
} = {}) {
  if (!clipboard || !Array.isArray(clipboard.notes) || clipboard.notes.length === 0) {
    return fail(REJECT.empty_clipboard, 'Clipboard is empty');
  }
  if (clipboard.version != null && Number(clipboard.version) !== CLIPBOARD_VERSION) {
    return fail(REJECT.invalid_options, 'Unsupported clipboard version');
  }

  const durationLimit = compositionDuration(composition);
  if (durationLimit == null) {
    return fail(REJECT.invalid_timing, 'Composition duration_ticks is invalid');
  }

  const tick = Math.round(Number(pasteTick));
  if (!Number.isInteger(tick) || tick < 0) {
    return fail(REJECT.invalid_options, 'pasteTick must be a non-negative integer');
  }

  const trackOrder = trackIndexById(composition);
  const clipTrackIds = Array.isArray(clipboard.trackIds) && clipboard.trackIds.length
    ? clipboard.trackIds.map(String)
    : [...new Set(clipboard.notes.map((note) => String(note.trackId)))];

  const singleTrack = clipTrackIds.length === 1;
  const useActive = Boolean(
    preferActiveTrackForSingleTrackClip
    && singleTrack
    && activeTrackId
    && trackOrder.has(String(activeTrackId)),
  );

  const targetTrackIds = [];
  if (useActive) {
    targetTrackIds.push(String(activeTrackId));
  } else {
    for (const trackId of clipTrackIds) {
      if (!trackOrder.has(trackId)) {
        return fail(REJECT.missing_targets, `Paste target track missing: ${trackId}`);
      }
      targetTrackIds.push(trackId);
    }
  }

  for (const trackId of targetTrackIds) {
    if (isTrackLocked(trackId, lockedTrackIds)) {
      return fail(REJECT.locked_targets, 'Paste target includes locked tracks');
    }
  }

  const sourceToTarget = new Map();
  if (useActive) {
    sourceToTarget.set(clipTrackIds[0], targetTrackIds[0]);
  } else {
    clipTrackIds.forEach((id, index) => {
      sourceToTarget.set(id, targetTrackIds[index]);
    });
  }

  const usedIds = collectEventIds(composition);
  const insertsByTrack = new Map();
  const selectionRefs = [];
  const draftNotes = [];

  let indexCounter = 0;
  for (const raw of clipboard.notes) {
    const sourceTrackId = String(raw.trackId ?? clipTrackIds[raw.relativeTrackIndex] ?? '');
    const targetTrackId = sourceToTarget.get(sourceTrackId) || sourceToTarget.get(clipTrackIds[0]);
    if (!targetTrackId) {
      return fail(REJECT.missing_targets, 'Unable to resolve paste target track');
    }

    const relativeStart = Number(raw.relativeStartTick);
    if (!Number.isInteger(relativeStart) || relativeStart < 0) {
      return fail(REJECT.invalid_timing, 'Clipboard note has invalid relativeStartTick');
    }
    const durationTicks = Number(raw.duration_ticks);
    if (!Number.isInteger(durationTicks) || durationTicks < MIN_NOTE_DURATION_TICKS) {
      return fail(REJECT.invalid_timing, 'Clipboard note has invalid duration_ticks');
    }

    const timing = clampNoteTiming({
      startTick: tick + relativeStart,
      durationTicks,
      compositionDurationTicks: durationLimit,
    });
    if (timing.startTick + timing.durationTicks > durationLimit) {
      return fail(REJECT.invalid_timing, 'Pasted notes would exceed composition duration');
    }

    const pitchCheck = pitchToMidi(raw.pitch);
    if (pitchCheck.midi === null) {
      return fail(REJECT.pitch_out_of_range, pitchCheck.warning || 'Invalid pitch in clipboard');
    }

    const draft = {
      type: 'note',
      pitch: String(raw.pitch).trim(),
      start_tick: timing.startTick,
      duration_ticks: timing.durationTicks,
      velocity: clampVelocity(raw.velocity),
      articulations: Array.isArray(raw.articulations) ? [...raw.articulations] : [],
      tie: raw.tie ? { group_id: raw.tie.group_id, type: raw.tie.type } : null,
      ...(raw.staff !== undefined ? { staff: raw.staff } : {}),
      ...(raw.voice !== undefined ? { voice: raw.voice } : {}),
      _targetTrackId: targetTrackId,
    };
    draftNotes.push(draft);
    indexCounter += 1;
  }

  const droppedTieCount = remapClipboardTies(draftNotes);

  for (const draft of draftNotes) {
    const targetTrackId = draft._targetTrackId;
    const id = allocateEventId(draft, usedIds, targetTrackId, indexCounter, 'paste');
    indexCounter += 1;
    const { _targetTrackId, ...rest } = draft;
    const event = { ...rest, id };
    if (!insertsByTrack.has(targetTrackId)) {
      insertsByTrack.set(targetTrackId, []);
    }
    insertsByTrack.get(targetTrackId).push(event);
    selectionRefs.push(makeNoteRef(targetTrackId, id));
  }

  const tracks = composition.tracks.map((track) => {
    const trackId = String(track.id);
    const inserts = insertsByTrack.get(trackId);
    if (!inserts) {
      return track;
    }
    const events = Array.isArray(track.events) ? [...track.events, ...inserts] : [...inserts];
    return { ...track, events };
  });

  return okResult(
    { ...composition, tracks },
    selectionFromRefs(selectionRefs),
    {
      pastedCount: selectionRefs.length,
      trackCount: insertsByTrack.size,
      droppedTieCount,
      pasteTick: tick,
    },
  );
}

/**
 * Duplicate selection after its tick range, aligned to snap.
 */
export function duplicateNotes(composition, refs, {
  snapValue = '1/8',
  lockedTrackIds = null,
} = {}) {
  const targets = resolveMutationTargets(composition, refs, lockedTrackIds);
  if (!targets.ok) {
    return targets;
  }

  const copied = copyNotes(composition, refs);
  if (!copied.ok) {
    return copied;
  }

  const range = selectionTickRange(composition, refs);
  if (!range) {
    return fail(REJECT.empty_selection, 'No notes selected');
  }

  const tpq = Number(composition?.ticks_per_quarter) || 480;
  const { snapTicks, warning } = snapIntervalTicks(snapValue, tpq);
  if (!snapTicks) {
    return fail(REJECT.invalid_options, warning || 'Invalid snap value');
  }

  const snapped = snapTick(range.endTick, snapTicks, {
    maxTick: compositionDuration(composition) ?? Number.POSITIVE_INFINITY,
    mode: 'ceil',
  });
  let pasteTick = snapped.tick;
  if (pasteTick < range.endTick) {
    pasteTick = range.endTick;
  }
  // Prefer next snap boundary at/after the selection end.
  if (pasteTick === range.endTick && range.endTick % snapTicks === 0) {
    // already aligned
  } else if (pasteTick < range.endTick) {
    pasteTick = Math.ceil(range.endTick / snapTicks) * snapTicks;
  }

  const durationLimit = compositionDuration(composition);
  if (durationLimit != null) {
    const span = Math.max(...copied.clipboard.notes.map(
      (note) => note.relativeStartTick + note.duration_ticks,
    ));
    if (pasteTick + span > durationLimit) {
      return fail(REJECT.invalid_timing, 'Duplicated notes would exceed composition duration');
    }
  }

  const pasted = pasteNotes(composition, copied.clipboard, {
    pasteTick,
    lockedTrackIds,
    preferActiveTrackForSingleTrackClip: false,
  });
  if (!pasted.ok) {
    return pasted;
  }
  return {
    ...pasted,
    summary: {
      ...pasted.summary,
      duplicatedCount: pasted.summary.pastedCount,
      pasteTick,
    },
  };
}

/**
 * Transpose selected notes by semitones; reject if any pitch leaves MIDI 0-127.
 */
export function transposeNotes(composition, refs, {
  semitones,
  lockedTrackIds = null,
} = {}) {
  const delta = Number(semitones);
  if (!Number.isInteger(delta)) {
    return fail(REJECT.invalid_options, 'semitones must be an integer');
  }
  if (delta === 0) {
    return fail(REJECT.no_op, 'Transpose by 0 semitones is a no-op');
  }

  const targets = resolveMutationTargets(composition, refs, lockedTrackIds);
  if (!targets.ok) {
    return targets;
  }

  const updates = new Map();
  for (const item of targets.resolved) {
    const { midi } = pitchToMidi(item.event.pitch);
    if (midi === null) {
      return fail(REJECT.pitch_out_of_range, 'Selected note has invalid pitch');
    }
    const nextMidi = midi + delta;
    if (nextMidi < 0 || nextMidi > 127) {
      return fail(REJECT.pitch_out_of_range, 'Transpose would leave MIDI range 0-127');
    }
    const { pitch } = midiToPitch(nextMidi);
    updates.set(noteRefKey(item.ref), pitch);
  }

  const nextComposition = applyEventUpdates(composition, targets.resolved, (event, item) => {
    const pitch = updates.get(noteRefKey(item.ref));
    return pitch === event.pitch ? event : { ...event, pitch };
  });

  return okResult(nextComposition, selectionFromRefs(targets.refs), {
    transposedCount: targets.resolved.length,
    semitones: delta,
  });
}

function applyEventUpdates(composition, resolved, updater) {
  const byTrack = new Map();
  for (const item of resolved) {
    if (!byTrack.has(item.ref.trackId)) {
      byTrack.set(item.ref.trackId, new Map());
    }
    byTrack.get(item.ref.trackId).set(item.ref.eventId, item);
  }

  const tracks = composition.tracks.map((track) => {
    const trackUpdates = byTrack.get(String(track.id));
    if (!trackUpdates) {
      return track;
    }
    const events = Array.isArray(track.events) ? track.events : [];
    let changed = false;
    const nextEvents = events.map((event) => {
      const item = trackUpdates.get(String(event.id));
      if (!item) {
        return event;
      }
      const next = updater(event, item);
      if (next !== event) {
        changed = true;
      }
      return next;
    });
    return changed ? { ...track, events: nextEvents } : track;
  });

  return { ...composition, tracks };
}

function mutateVelocities(composition, refs, lockedTrackIds, mapVelocity) {
  const targets = resolveMutationTargets(composition, refs, lockedTrackIds);
  if (!targets.ok) {
    return targets;
  }

  const nextComposition = applyEventUpdates(composition, targets.resolved, (event) => {
    const nextVelocity = clampVelocity(mapVelocity(event.velocity));
    if (nextVelocity === event.velocity) {
      return event;
    }
    return { ...event, velocity: nextVelocity };
  });

  return okResult(nextComposition, selectionFromRefs(targets.refs), {
    affectedCount: targets.resolved.length,
  });
}

export function setNoteVelocities(composition, refs, {
  velocity,
  lockedTrackIds = null,
} = {}) {
  const value = Number(velocity);
  if (!Number.isFinite(value)) {
    return fail(REJECT.invalid_options, 'velocity must be a number');
  }
  return mutateVelocities(composition, refs, lockedTrackIds, () => value);
}

export function deltaNoteVelocities(composition, refs, {
  delta,
  lockedTrackIds = null,
} = {}) {
  const value = Number(delta);
  if (!Number.isFinite(value)) {
    return fail(REJECT.invalid_options, 'delta must be a number');
  }
  if (value === 0) {
    return fail(REJECT.no_op, 'Velocity delta of 0 is a no-op');
  }
  return mutateVelocities(composition, refs, lockedTrackIds, (current) => Number(current) + value);
}

export function scaleNoteVelocities(composition, refs, {
  factor,
  lockedTrackIds = null,
} = {}) {
  const value = Number(factor);
  if (!Number.isFinite(value) || value < 0) {
    return fail(REJECT.invalid_options, 'factor must be a non-negative number');
  }
  if (value === 1) {
    return fail(REJECT.no_op, 'Velocity scale of 1 is a no-op');
  }
  return mutateVelocities(composition, refs, lockedTrackIds, (current) => Number(current) * value);
}

function blendTick(current, target, strength) {
  if (strength <= 0) {
    return current;
  }
  if (strength >= 100) {
    return target;
  }
  return Math.round(current + (target - current) * (strength / 100));
}

/**
 * Quantize note starts (and optionally ends) toward the snap grid by strength 0-100.
 */
export function quantizeNotes(composition, refs, {
  mode = 'start',
  snapValue = '1/8',
  strength = 100,
  lockedTrackIds = null,
} = {}) {
  if (mode !== 'start' && mode !== 'start_end') {
    return fail(REJECT.invalid_options, 'mode must be start or start_end');
  }
  const strengthValue = Number(strength);
  if (!Number.isFinite(strengthValue) || strengthValue < 0 || strengthValue > 100) {
    return fail(REJECT.invalid_options, 'strength must be between 0 and 100');
  }

  const targets = resolveMutationTargets(composition, refs, lockedTrackIds);
  if (!targets.ok) {
    return targets;
  }

  const durationLimit = compositionDuration(composition);
  if (durationLimit == null) {
    return fail(REJECT.invalid_timing, 'Composition duration_ticks is invalid');
  }

  const tpq = Number(composition?.ticks_per_quarter) || 480;
  const { snapTicks, warning } = snapIntervalTicks(snapValue, tpq);
  if (!snapTicks) {
    return fail(REJECT.invalid_options, warning || 'Invalid snap value');
  }

  if (strengthValue === 0) {
    return okResult(composition, selectionFromRefs(targets.refs), {
      quantizedCount: 0,
      strength: 0,
      mode,
      noOp: true,
    });
  }

  const patches = new Map();
  for (const item of targets.resolved) {
    const start = item.event.start_tick;
    const duration = item.event.duration_ticks;
    const end = start + duration;

    const snappedStart = snapTick(start, snapTicks, { maxTick: durationLimit - MIN_NOTE_DURATION_TICKS }).tick;
    let nextStart = blendTick(start, snappedStart, strengthValue);
    let nextDuration = duration;

    if (mode === 'start_end') {
      const snappedEnd = snapTick(end, snapTicks, { maxTick: durationLimit }).tick;
      let nextEnd = blendTick(end, snappedEnd, strengthValue);
      if (nextEnd <= nextStart) {
        nextEnd = nextStart + MIN_NOTE_DURATION_TICKS;
      }
      nextDuration = nextEnd - nextStart;
    }

    const timing = clampNoteTiming({
      startTick: nextStart,
      durationTicks: nextDuration,
      compositionDurationTicks: durationLimit,
    });
    if (timing.durationTicks < MIN_NOTE_DURATION_TICKS) {
      return fail(REJECT.invalid_timing, 'Quantize produced non-positive duration');
    }
    if (timing.startTick + timing.durationTicks > durationLimit) {
      return fail(REJECT.invalid_timing, 'Quantize would exceed composition duration');
    }

    patches.set(noteRefKey(item.ref), {
      start_tick: timing.startTick,
      duration_ticks: timing.durationTicks,
    });
  }

  const nextComposition = applyEventUpdates(composition, targets.resolved, (event, item) => {
    const patch = patches.get(noteRefKey(item.ref));
    if (!patch) {
      return event;
    }
    if (event.start_tick === patch.start_tick && event.duration_ticks === patch.duration_ticks) {
      return event;
    }
    return { ...event, ...patch };
  });

  return okResult(nextComposition, selectionFromRefs(targets.refs), {
    quantizedCount: targets.resolved.length,
    strength: strengthValue,
    mode,
  });
}

export function setNoteLengths(composition, refs, {
  durationTicks,
  lockedTrackIds = null,
} = {}) {
  const duration = Number(durationTicks);
  if (!Number.isInteger(duration) || duration < MIN_NOTE_DURATION_TICKS) {
    return fail(REJECT.invalid_options, 'durationTicks must be a positive integer');
  }

  const targets = resolveMutationTargets(composition, refs, lockedTrackIds);
  if (!targets.ok) {
    return targets;
  }

  const durationLimit = compositionDuration(composition);
  if (durationLimit == null) {
    return fail(REJECT.invalid_timing, 'Composition duration_ticks is invalid');
  }

  for (const item of targets.resolved) {
    if (item.event.start_tick + duration > durationLimit) {
      return fail(REJECT.invalid_timing, 'Set length would exceed composition duration');
    }
  }

  const nextComposition = applyEventUpdates(composition, targets.resolved, (event) => {
    if (event.duration_ticks === duration) {
      return event;
    }
    return { ...event, duration_ticks: duration };
  });

  return okResult(nextComposition, selectionFromRefs(targets.refs), {
    affectedCount: targets.resolved.length,
    durationTicks: duration,
  });
}

export function nudgeNoteLengths(composition, refs, {
  snapValue = '1/8',
  steps = 1,
  lockedTrackIds = null,
} = {}) {
  const stepCount = Number(steps);
  if (!Number.isInteger(stepCount) || stepCount === 0) {
    return fail(REJECT.invalid_options, 'steps must be a non-zero integer');
  }

  const tpq = Number(composition?.ticks_per_quarter) || 480;
  const { snapTicks, warning } = snapIntervalTicks(snapValue, tpq);
  if (!snapTicks) {
    return fail(REJECT.invalid_options, warning || 'Invalid snap value');
  }

  const targets = resolveMutationTargets(composition, refs, lockedTrackIds);
  if (!targets.ok) {
    return targets;
  }

  const durationLimit = compositionDuration(composition);
  if (durationLimit == null) {
    return fail(REJECT.invalid_timing, 'Composition duration_ticks is invalid');
  }

  const delta = stepCount * snapTicks;
  const patches = new Map();
  for (const item of targets.resolved) {
    const nextDuration = item.event.duration_ticks + delta;
    if (nextDuration < MIN_NOTE_DURATION_TICKS) {
      return fail(REJECT.invalid_timing, 'Nudge would produce non-positive duration');
    }
    if (item.event.start_tick + nextDuration > durationLimit) {
      return fail(REJECT.invalid_timing, 'Nudge would exceed composition duration');
    }
    patches.set(noteRefKey(item.ref), nextDuration);
  }

  const nextComposition = applyEventUpdates(composition, targets.resolved, (event, item) => {
    const nextDuration = patches.get(noteRefKey(item.ref));
    if (nextDuration === event.duration_ticks) {
      return event;
    }
    return { ...event, duration_ticks: nextDuration };
  });

  return okResult(nextComposition, selectionFromRefs(targets.refs), {
    affectedCount: targets.resolved.length,
    deltaTicks: delta,
  });
}

export function quantizeNoteEnds(composition, refs, {
  snapValue = '1/8',
  strength = 100,
  lockedTrackIds = null,
} = {}) {
  const strengthValue = Number(strength);
  if (!Number.isFinite(strengthValue) || strengthValue < 0 || strengthValue > 100) {
    return fail(REJECT.invalid_options, 'strength must be between 0 and 100');
  }

  const targets = resolveMutationTargets(composition, refs, lockedTrackIds);
  if (!targets.ok) {
    return targets;
  }

  const durationLimit = compositionDuration(composition);
  if (durationLimit == null) {
    return fail(REJECT.invalid_timing, 'Composition duration_ticks is invalid');
  }

  const tpq = Number(composition?.ticks_per_quarter) || 480;
  const { snapTicks, warning } = snapIntervalTicks(snapValue, tpq);
  if (!snapTicks) {
    return fail(REJECT.invalid_options, warning || 'Invalid snap value');
  }

  if (strengthValue === 0) {
    return okResult(composition, selectionFromRefs(targets.refs), {
      quantizedCount: 0,
      strength: 0,
      noOp: true,
    });
  }

  const patches = new Map();
  for (const item of targets.resolved) {
    const start = item.event.start_tick;
    const end = start + item.event.duration_ticks;
    const snappedEnd = snapTick(end, snapTicks, { maxTick: durationLimit }).tick;
    let nextEnd = blendTick(end, snappedEnd, strengthValue);
    if (nextEnd <= start) {
      nextEnd = start + MIN_NOTE_DURATION_TICKS;
    }
    if (nextEnd > durationLimit) {
      return fail(REJECT.invalid_timing, 'Quantize ends would exceed composition duration');
    }
    patches.set(noteRefKey(item.ref), nextEnd - start);
  }

  const nextComposition = applyEventUpdates(composition, targets.resolved, (event, item) => {
    const duration = patches.get(noteRefKey(item.ref));
    if (duration === event.duration_ticks) {
      return event;
    }
    return { ...event, duration_ticks: duration };
  });

  return okResult(nextComposition, selectionFromRefs(targets.refs), {
    quantizedCount: targets.resolved.length,
    strength: strengthValue,
  });
}

/**
 * Extend each selected note to the next note on the same track/voice without
 * extending past composition duration_ticks.
 */
export function legatoNotes(composition, refs, { lockedTrackIds = null } = {}) {
  const targets = resolveMutationTargets(composition, refs, lockedTrackIds);
  if (!targets.ok) {
    return targets;
  }

  const durationLimit = compositionDuration(composition);
  if (durationLimit == null) {
    return fail(REJECT.invalid_timing, 'Composition duration_ticks is invalid');
  }

  const selectedKeys = new Set(targets.resolved.map((item) => noteRefKey(item.ref)));
  const patches = new Map();
  let changedCount = 0;

  const byTrack = new Map();
  for (const item of targets.resolved) {
    if (!byTrack.has(item.ref.trackId)) {
      byTrack.set(item.ref.trackId, []);
    }
    byTrack.get(item.ref.trackId).push(item);
  }

  for (const [trackId, items] of byTrack) {
    const track = composition.tracks.find((entry) => String(entry.id) === trackId);
    const allEvents = Array.isArray(track?.events) ? [...track.events] : [];
    allEvents.sort((a, b) => a.start_tick - b.start_tick || a.duration_ticks - b.duration_ticks);

    for (const item of items) {
      const voiceKey = eventVoiceKey(item.event);
      const start = item.event.start_tick;
      let nextStart = null;
      for (const candidate of allEvents) {
        if (String(candidate.id) === item.ref.eventId) {
          continue;
        }
        if (eventVoiceKey(candidate) !== voiceKey) {
          continue;
        }
        if (candidate.start_tick > start) {
          nextStart = candidate.start_tick;
          break;
        }
      }
      const targetEnd = nextStart == null ? durationLimit : Math.min(nextStart, durationLimit);
      const nextDuration = Math.max(MIN_NOTE_DURATION_TICKS, targetEnd - start);
      if (nextDuration === item.event.duration_ticks) {
        continue;
      }
      if (start + nextDuration > durationLimit) {
        return fail(REJECT.invalid_timing, 'Legato would exceed composition duration');
      }
      patches.set(noteRefKey(item.ref), nextDuration);
      changedCount += 1;
    }
  }

  if (!changedCount) {
    return fail(REJECT.no_op, 'Legato produced no length changes');
  }

  const nextComposition = applyEventUpdates(composition, targets.resolved, (event, item) => {
    const key = noteRefKey(item.ref);
    if (!selectedKeys.has(key) || !patches.has(key)) {
      return event;
    }
    return { ...event, duration_ticks: patches.get(key) };
  });

  return okResult(nextComposition, selectionFromRefs(targets.refs), {
    affectedCount: changedCount,
  });
}

/**
 * Apply articulation to selected notes; skip incompatible (ties / conflicts).
 */
export function setNoteArticulations(composition, refs, {
  articulation = null,
  mode = 'set',
  lockedTrackIds = null,
} = {}) {
  if (mode !== 'set' && mode !== 'toggle' && mode !== 'clear') {
    return fail(REJECT.invalid_options, 'mode must be set, toggle, or clear');
  }
  if (mode !== 'clear') {
    if (!ARTICULATION_VALUES.includes(articulation)) {
      return fail(REJECT.invalid_options, `unsupported articulation: ${articulation}`);
    }
  }

  const targets = resolveMutationTargets(composition, refs, lockedTrackIds);
  if (!targets.ok) {
    return targets;
  }

  const skipped = [];
  const patches = new Map();

  for (const item of targets.resolved) {
    const current = Array.isArray(item.event.articulations) ? [...item.event.articulations] : [];
    const tieType = item.event.tie?.type ?? null;
    let next = current;

    if (mode === 'clear') {
      next = articulation
        ? current.filter((name) => name !== articulation)
        : [];
    } else if (mode === 'toggle') {
      next = current.includes(articulation)
        ? current.filter((name) => name !== articulation)
        : [...current, articulation];
    } else {
      // set: ensure articulation is present (add if missing), keep others if compatible
      next = current.includes(articulation) ? current : [...current, articulation];
    }

    const validation = validateArticulationSet(next, tieType);
    if (validation) {
      skipped.push({
        trackId: item.ref.trackId,
        eventId: item.ref.eventId,
        reason: validation,
      });
      continue;
    }

    if (next.length === current.length && next.every((name, index) => name === current[index])) {
      continue;
    }
    patches.set(noteRefKey(item.ref), next);
  }

  if (!patches.size && skipped.length === targets.resolved.length) {
    return fail(REJECT.no_op, 'No compatible notes for articulation change', {
      skipped,
      skippedCount: skipped.length,
    });
  }

  if (!patches.size) {
    return okResult(composition, selectionFromRefs(targets.refs), {
      affectedCount: 0,
      skipped,
      skippedCount: skipped.length,
    });
  }

  const nextComposition = applyEventUpdates(composition, targets.resolved, (event, item) => {
    const next = patches.get(noteRefKey(item.ref));
    if (!next) {
      return event;
    }
    return { ...event, articulations: next };
  });

  return okResult(nextComposition, selectionFromRefs(targets.refs), {
    affectedCount: patches.size,
    skipped,
    skippedCount: skipped.length,
  });
}

/**
 * Bounded humanize of timing and velocity. Inject `random` for deterministic tests.
 */
export function humanizeNotes(composition, refs, {
  timingAmount = 0,
  velocityAmount = 0,
  random = Math.random,
  lockedTrackIds = null,
} = {}) {
  const timing = Number(timingAmount);
  const velocity = Number(velocityAmount);
  if (!Number.isFinite(timing) || timing < 0 || !Number.isInteger(timing)) {
    return fail(REJECT.invalid_options, 'timingAmount must be a non-negative integer');
  }
  if (!Number.isFinite(velocity) || velocity < 0 || !Number.isInteger(velocity)) {
    return fail(REJECT.invalid_options, 'velocityAmount must be a non-negative integer');
  }
  if (timing === 0 && velocity === 0) {
    return fail(REJECT.no_op, 'Humanize amounts are both zero');
  }
  if (typeof random !== 'function') {
    return fail(REJECT.invalid_options, 'random must be a function');
  }

  const targets = resolveMutationTargets(composition, refs, lockedTrackIds);
  if (!targets.ok) {
    return targets;
  }

  const durationLimit = compositionDuration(composition);
  if (durationLimit == null) {
    return fail(REJECT.invalid_timing, 'Composition duration_ticks is invalid');
  }

  const patches = new Map();
  for (const item of targets.resolved) {
    let start = item.event.start_tick;
    let duration = item.event.duration_ticks;
    let vel = item.event.velocity;

    if (timing > 0) {
      const offset = Math.round((random() * 2 - 1) * timing);
      start += offset;
    }
    if (velocity > 0) {
      const offset = Math.round((random() * 2 - 1) * velocity);
      vel = Number(vel) + offset;
    }

    const timingResult = clampNoteTiming({
      startTick: start,
      durationTicks: duration,
      compositionDurationTicks: durationLimit,
    });
    if (timingResult.durationTicks < MIN_NOTE_DURATION_TICKS) {
      return fail(REJECT.invalid_timing, 'Humanize produced invalid duration');
    }

    patches.set(noteRefKey(item.ref), {
      start_tick: timingResult.startTick,
      duration_ticks: timingResult.durationTicks,
      velocity: clampVelocity(vel),
    });
  }

  const nextComposition = applyEventUpdates(composition, targets.resolved, (event, item) => {
    const patch = patches.get(noteRefKey(item.ref));
    if (!patch) {
      return event;
    }
    if (
      event.start_tick === patch.start_tick
      && event.duration_ticks === patch.duration_ticks
      && event.velocity === patch.velocity
    ) {
      return event;
    }
    return { ...event, ...patch };
  });

  return okResult(nextComposition, selectionFromRefs(targets.refs), {
    affectedCount: targets.resolved.length,
    timingAmount: timing,
    velocityAmount: velocity,
  });
}
