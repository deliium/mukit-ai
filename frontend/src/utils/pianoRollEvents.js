/**
 * Pure Composition V2 piano-roll geometry and immutable event helpers.
 * Logging is caller-owned; helpers return metadata for WARN/DEBUG summaries.
 */

import { barDurationTicks as sharedBarDurationTicks } from './playbackPosition.js';
import { compileTimeline } from './compositionTimeline.js';

const NOTE_NAMES_SHARP = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
const NOTE_TO_SEMITONE = {
  C: 0,
  'C#': 1,
  Db: 1,
  D: 2,
  'D#': 3,
  Eb: 3,
  E: 4,
  F: 5,
  'F#': 6,
  Gb: 6,
  G: 7,
  'G#': 8,
  Ab: 8,
  A: 9,
  'A#': 10,
  Bb: 10,
  B: 11,
};
const PITCH_PATTERN = /^([A-G])([#b]?)(-?\d+)$/;
export const ARTICULATION_VALUES = Object.freeze(['staccato', 'staccatissimo', 'tenuto', 'accent', 'marcato']);
export const SNAP_VALUES = Object.freeze(['1/4', '1/8', '1/16']);
const GATE_SHORTENING_ARTICULATIONS = new Set(['staccato', 'staccatissimo', 'marcato']);
const ATTACK_ARTICULATIONS = new Set(['accent', 'marcato']);
export const DEFAULT_NOTE_VELOCITY = 90;
export const MIN_NOTE_DURATION_TICKS = 1;
export const DEFAULT_PITCH_MIDI_MIN = 36; // C2
export const DEFAULT_PITCH_MIDI_MAX = 96; // C7
export const MAX_UNDO_HISTORY = 50;

/**
 * Convert scientific pitch notation to MIDI note number (0-127).
 * @returns {{ midi: number|null, warning?: string }}
 */
export function pitchToMidi(pitch) {
  if (!pitch || typeof pitch !== 'string') {
    return { midi: null, warning: 'pitch must be a non-empty string' };
  }
  const match = pitch.trim().match(PITCH_PATTERN);
  if (!match) {
    return { midi: null, warning: `invalid pitch format: ${pitch}` };
  }
  const noteName = `${match[1]}${match[2]}`;
  const octave = Number(match[3]);
  const semitone = NOTE_TO_SEMITONE[noteName];
  if (semitone === undefined || !Number.isInteger(octave)) {
    return { midi: null, warning: `unsupported pitch: ${pitch}` };
  }
  const midi = (octave + 1) * 12 + semitone;
  if (!Number.isInteger(midi) || midi < 0 || midi > 127) {
    return { midi: null, warning: `pitch out of MIDI range: ${pitch}` };
  }
  return { midi };
}

/**
 * Convert MIDI note number to sharp-preferring scientific pitch.
 * @returns {{ pitch: string|null, warning?: string }}
 */
export function midiToPitch(midi) {
  const value = Number(midi);
  if (!Number.isInteger(value) || value < 0 || value > 127) {
    return { pitch: null, warning: `MIDI out of range: ${midi}` };
  }
  const octave = Math.floor(value / 12) - 1;
  const noteName = NOTE_NAMES_SHARP[value % 12];
  return { pitch: `${noteName}${octave}` };
}

/**
 * Choose a visible MIDI pitch range from events, with defaults and padding.
 */
export function selectPitchRange(events, {
  minMidi = DEFAULT_PITCH_MIDI_MIN,
  maxMidi = DEFAULT_PITCH_MIDI_MAX,
  padding = 2,
} = {}) {
  let low = minMidi;
  let high = maxMidi;
  const list = Array.isArray(events) ? events : [];
  if (list.length) {
    let foundLow = 127;
    let foundHigh = 0;
    let validCount = 0;
    list.forEach((event) => {
      const { midi } = pitchToMidi(event?.pitch);
      if (midi === null) {
        return;
      }
      validCount += 1;
      foundLow = Math.min(foundLow, midi);
      foundHigh = Math.max(foundHigh, midi);
    });
    if (validCount > 0) {
      low = Math.max(0, foundLow - padding);
      high = Math.min(127, foundHigh + padding);
      if (high - low < 12) {
        const mid = Math.round((low + high) / 2);
        low = Math.max(0, mid - 6);
        high = Math.min(127, mid + 6);
      }
    }
  }
  return { minMidi: low, maxMidi: high, pitchCount: high - low + 1 };
}

/**
 * Bar duration in ticks for supported meters (4/4, 3/4, 6/8, …).
 * @returns {{ barTicks: number|null, warning?: string }}
 */
export function barDurationTicks(timeSignature, ticksPerQuarter) {
  const tpq = Number(ticksPerQuarter);
  if (!Number.isInteger(tpq) || tpq <= 0) {
    return { barTicks: null, warning: 'ticks_per_quarter must be a positive integer' };
  }
  const ticks = sharedBarDurationTicks(timeSignature, tpq);
  if (!Number.isInteger(ticks) || ticks <= 0) {
    return { barTicks: null, warning: `unsupported or invalid time signature: ${timeSignature}` };
  }
  return { barTicks: ticks };
}

/**
 * Snap interval in ticks for 1/4, 1/8, 1/16 relative to ticks_per_quarter.
 * @returns {{ snapTicks: number|null, warning?: string }}
 */
export function snapIntervalTicks(snapValue, ticksPerQuarter) {
  const tpq = Number(ticksPerQuarter);
  if (!Number.isInteger(tpq) || tpq <= 0) {
    return { snapTicks: null, warning: 'ticks_per_quarter must be a positive integer' };
  }
  if (!SNAP_VALUES.includes(snapValue)) {
    return { snapTicks: null, warning: `invalid snap value: ${snapValue}` };
  }
  const divisor = snapValue === '1/4' ? 1 : snapValue === '1/8' ? 2 : 4;
  const snapTicks = tpq / divisor;
  if (!Number.isInteger(snapTicks) || snapTicks <= 0) {
    return { snapTicks: null, warning: `snap does not divide ticks_per_quarter evenly: ${snapValue}` };
  }
  return { snapTicks };
}

/**
 * Snap a tick value to the nearest multiple of snapTicks within [0, maxTick].
 */
export function snapTick(tick, snapTicks, { maxTick = Number.POSITIVE_INFINITY, mode = 'nearest' } = {}) {
  const value = Number(tick);
  const step = Number(snapTicks);
  if (!Number.isFinite(value) || !Number.isInteger(step) || step <= 0) {
    return {
      tick: 0,
      clamped: true,
      warning: 'invalid tick or snap interval',
    };
  }
  let snapped;
  if (mode === 'floor') {
    snapped = Math.floor(value / step) * step;
  } else if (mode === 'ceil') {
    snapped = Math.ceil(value / step) * step;
  } else {
    snapped = Math.round(value / step) * step;
  }
  const max = Number.isFinite(maxTick) ? Math.max(0, maxTick) : Number.POSITIVE_INFINITY;
  const clamped = Math.max(0, Math.min(snapped, max));
  return {
    tick: clamped,
    clamped: clamped !== snapped || clamped !== value,
  };
}

/**
 * Convert horizontal pixel offset to tick using pixels-per-tick zoom.
 */
export function pixelToTick(pixelX, pixelsPerTick) {
  const x = Number(pixelX);
  const ppt = Number(pixelsPerTick);
  if (!Number.isFinite(x) || !Number.isFinite(ppt) || ppt <= 0) {
    return { tick: 0, warning: 'invalid pixel or pixelsPerTick' };
  }
  return { tick: x / ppt };
}

/**
 * Convert vertical pixel offset to MIDI pitch (row 0 = highest pitch).
 */
export function pixelToPitchMidi(pixelY, {
  minMidi,
  maxMidi,
  rowHeight,
} = {}) {
  const y = Number(pixelY);
  const height = Number(rowHeight);
  const low = Number(minMidi);
  const high = Number(maxMidi);
  if (!Number.isFinite(y) || !Number.isFinite(height) || height <= 0 || !Number.isInteger(low) || !Number.isInteger(high) || high < low) {
    return { midi: null, warning: 'invalid pitch lane metrics' };
  }
  const row = Math.floor(y / height);
  const midi = high - row;
  if (midi < low || midi > high) {
    return { midi: Math.max(low, Math.min(high, midi)), clamped: true };
  }
  return { midi, clamped: false };
}

/**
 * Clamp note timing so start+duration stays within [0, durationTicks].
 */
export function clampNoteTiming({
  startTick,
  durationTicks,
  compositionDurationTicks,
  minDurationTicks = MIN_NOTE_DURATION_TICKS,
}) {
  const durationLimit = Number(compositionDurationTicks);
  let start = Math.max(0, Math.floor(Number(startTick) || 0));
  let duration = Math.max(minDurationTicks, Math.floor(Number(durationTicks) || minDurationTicks));
  const warnings = [];

  if (!Number.isInteger(durationLimit) || durationLimit <= 0) {
    return {
      startTick: start,
      durationTicks: duration,
      clamped: true,
      warnings: ['invalid composition duration_ticks'],
    };
  }

  if (start >= durationLimit) {
    start = Math.max(0, durationLimit - minDurationTicks);
    warnings.push('start_tick clamped to composition end');
  }
  if (start + duration > durationLimit) {
    duration = Math.max(minDurationTicks, durationLimit - start);
    warnings.push('duration_ticks clamped to composition end');
  }
  if (duration < minDurationTicks) {
    duration = minDurationTicks;
    warnings.push('duration_ticks raised to minimum');
  }

  return {
    startTick: start,
    durationTicks: duration,
    clamped: warnings.length > 0,
    warnings,
  };
}

/**
 * Clamp MIDI pitch into 0..127 (optionally within a visible range).
 */
export function clampMidiPitch(midi, { minMidi = 0, maxMidi = 127 } = {}) {
  const value = Number(midi);
  if (!Number.isInteger(value)) {
    return { midi: Math.max(minMidi, Math.min(maxMidi, 60)), clamped: true, warning: 'non-integer MIDI pitch' };
  }
  const clamped = Math.max(minMidi, Math.min(maxMidi, value));
  return {
    midi: clamped,
    clamped: clamped !== value,
    warning: clamped !== value ? 'MIDI pitch clamped' : undefined,
  };
}

/**
 * Generate a stable note ID when an event is missing one.
 */
export function ensureNoteId(event, { trackId, index = 0, prefix = 'note' } = {}) {
  if (event && typeof event.id === 'string' && event.id.trim()) {
    return { id: event.id.trim(), generated: false };
  }
  const pitch = typeof event?.pitch === 'string' ? event.pitch : 'x';
  const start = Number.isFinite(Number(event?.start_tick)) ? event.start_tick : 'x';
  const duration = Number.isFinite(Number(event?.duration_ticks)) ? event.duration_ticks : 'x';
  const track = trackId || 'track';
  return {
    id: `${prefix}-${track}-${pitch}-${start}-${duration}-${index}`,
    generated: true,
  };
}

/**
 * Ensure every event on every track has a stable id; preserves other fields.
 */
export function ensureCompositionNoteIds(composition) {
  if (!composition || !Array.isArray(composition.tracks)) {
    return { composition, generatedCount: 0 };
  }
  let generatedCount = 0;
  const tracks = composition.tracks.map((track) => {
    const events = Array.isArray(track.events)
      ? track.events.map((event, index) => {
        const { id, generated } = ensureNoteId(event, { trackId: track.id, index });
        if (generated) {
          generatedCount += 1;
        }
        return generated ? { ...event, id } : event;
      })
      : [];
    return { ...track, events };
  });
  return {
    composition: { ...composition, tracks },
    generatedCount,
  };
}

/**
 * Immutable create note on a track. Allows overlapping / polyphonic events.
 */
export function createTrackNote(composition, trackId, noteDraft) {
  if (!composition || !Array.isArray(composition.tracks)) {
    return { composition, note: null, warning: 'composition has no tracks' };
  }
  const trackIndex = composition.tracks.findIndex((track) => String(track.id) === String(trackId));
  if (trackIndex < 0) {
    return { composition, note: null, warning: `track not found: ${trackId}` };
  }

  const timing = clampNoteTiming({
    startTick: noteDraft?.start_tick,
    durationTicks: noteDraft?.duration_ticks,
    compositionDurationTicks: composition.duration_ticks,
  });
  let pitch = null;
  let pitchWarning;
  if (typeof noteDraft?.pitch === 'string') {
    const checked = pitchToMidi(noteDraft.pitch);
    if (checked.midi === null) {
      pitchWarning = checked.warning;
    } else {
      pitch = noteDraft.pitch.trim();
    }
  } else {
    const converted = midiToPitch(noteDraft?.midi ?? noteDraft?.pitchMidi);
    pitch = converted.pitch;
    pitchWarning = converted.warning;
  }
  if (!pitch) {
    return {
      composition,
      note: null,
      warning: pitchWarning || 'invalid pitch for create',
    };
  }
  const velocity = Number.isInteger(Number(noteDraft?.velocity))
    ? Math.max(1, Math.min(127, Number(noteDraft.velocity)))
    : DEFAULT_NOTE_VELOCITY;

  const draft = {
    type: 'note',
    pitch,
    start_tick: timing.startTick,
    duration_ticks: timing.durationTicks,
    velocity,
    ...(noteDraft?.staff !== undefined ? { staff: noteDraft.staff } : {}),
    ...(noteDraft?.voice !== undefined ? { voice: noteDraft.voice } : {}),
  };
  const { id } = ensureNoteId(noteDraft?.id ? { ...draft, id: noteDraft.id } : draft, {
    trackId,
    index: composition.tracks[trackIndex].events?.length || 0,
  });
  const note = {
    ...draft,
    id,
    articulations: Array.isArray(noteDraft?.articulations) ? [...noteDraft.articulations] : [],
    tie: noteDraft?.tie ?? null,
  };

  const tracks = composition.tracks.map((track, index) => {
    if (index !== trackIndex) {
      return track;
    }
    const events = Array.isArray(track.events) ? [...track.events, note] : [note];
    return { ...track, events };
  });

  return {
    composition: { ...composition, tracks },
    note,
    clamped: timing.clamped,
    warnings: timing.warnings,
  };
}

/**
 * Immutable update of a note by id on a track. Preserves untouched fields.
 */
export function updateTrackNote(composition, trackId, noteId, patch = {}) {
  if (!composition || !Array.isArray(composition.tracks)) {
    return { composition, note: null, warning: 'composition has no tracks' };
  }
  const trackIndex = composition.tracks.findIndex((track) => String(track.id) === String(trackId));
  if (trackIndex < 0) {
    return { composition, note: null, warning: `track not found: ${trackId}` };
  }

  let updatedNote = null;
  let warning;
  let clamped = false;
  let warnings = [];

  const tracks = composition.tracks.map((track, index) => {
    if (index !== trackIndex) {
      return track;
    }
    const events = Array.isArray(track.events) ? track.events : [];
    const nextEvents = events.map((event) => {
      if (String(event.id) !== String(noteId)) {
        return event;
      }
      let pitch = event.pitch;
      if (patch.pitch !== undefined) {
        pitch = patch.pitch;
      } else if (patch.midi !== undefined || patch.pitchMidi !== undefined) {
        const converted = midiToPitch(patch.midi ?? patch.pitchMidi);
        if (!converted.pitch) {
          warning = converted.warning;
          return event;
        }
        pitch = converted.pitch;
      }
      const pitchCheck = pitchToMidi(pitch);
      if (pitchCheck.midi === null) {
        warning = pitchCheck.warning;
        return event;
      }

      const timing = clampNoteTiming({
        startTick: patch.start_tick !== undefined ? patch.start_tick : event.start_tick,
        durationTicks: patch.duration_ticks !== undefined ? patch.duration_ticks : event.duration_ticks,
        compositionDurationTicks: composition.duration_ticks,
      });
      clamped = timing.clamped;
      warnings = timing.warnings;

      const velocity = patch.velocity !== undefined
        ? Math.max(1, Math.min(127, Number(patch.velocity) || event.velocity))
        : event.velocity;

      updatedNote = {
        ...event,
        ...patch,
        id: event.id,
        type: event.type || 'note',
        pitch,
        start_tick: timing.startTick,
        duration_ticks: timing.durationTicks,
        velocity,
      };
      // Remove transient geometry keys if callers passed them
      delete updatedNote.midi;
      delete updatedNote.pitchMidi;
      return updatedNote;
    });

    if (!updatedNote && !warning) {
      warning = `note not found: ${noteId}`;
    }
    return { ...track, events: nextEvents };
  });

  return {
    composition: updatedNote ? { ...composition, tracks } : composition,
    note: updatedNote,
    warning,
    clamped,
    warnings,
  };
}

/**
 * Immutable delete of a note by id on a track.
 */
export function deleteTrackNote(composition, trackId, noteId) {
  if (!composition || !Array.isArray(composition.tracks)) {
    return { composition, deleted: null, warning: 'composition has no tracks' };
  }
  const trackIndex = composition.tracks.findIndex((track) => String(track.id) === String(trackId));
  if (trackIndex < 0) {
    return { composition, deleted: null, warning: `track not found: ${trackId}` };
  }

  let deleted = null;
  let tieGroupId = null;
  const tracks = composition.tracks.map((track, index) => {
    if (index !== trackIndex) {
      return track;
    }
    const events = Array.isArray(track.events) ? track.events : [];
    const nextEvents = [];
    events.forEach((event) => {
      if (String(event.id) === String(noteId) && !deleted) {
        deleted = event;
        tieGroupId = event.tie?.group_id ?? null;
        return;
      }
      nextEvents.push(event);
    });
    const clearedEvents = tieGroupId
      ? nextEvents.map((event) => (
        event.tie?.group_id === tieGroupId
          ? { ...event, tie: null }
          : event
      ))
      : nextEvents;
    return { ...track, events: clearedEvents };
  });

  if (!deleted) {
    return { composition, deleted: null, warning: `note not found: ${noteId}` };
  }
  return {
    composition: { ...composition, tracks },
    deleted,
  };
}

/**
 * Grid metrics for piano-roll rendering.
 */
export function buildGridMetrics(composition, {
  snapValue = '1/8',
  pixelsPerTick = 0.05,
  rowHeight = 14,
  pitchRange,
} = {}) {
  const tpq = Number(composition?.ticks_per_quarter) || 480;
  const barResult = barDurationTicks(composition?.time_signature, tpq);
  const snapResult = snapIntervalTicks(snapValue, tpq);
  const durationTicks = Number(composition?.duration_ticks) || 0;
  const barCount = Number(composition?.bar_count) || 0;
  const events = Array.isArray(composition?.tracks)
    ? composition.tracks.flatMap((track) => (Array.isArray(track.events) ? track.events : []))
    : [];
  const range = pitchRange || selectPitchRange(events);
  const ppt = Number(pixelsPerTick) > 0 ? Number(pixelsPerTick) : 0.05;
  const height = Number(rowHeight) > 0 ? Number(rowHeight) : 14;
  const warning = barResult.warning || snapResult.warning;
  const timeline = compileTimeline(composition);
  const barBoundaries = timeline?.barBoundaries || null;

  return {
    ticksPerQuarter: tpq,
    barTicks: barResult.barTicks || tpq * 4,
    barBoundaries,
    snapTicks: snapResult.snapTicks || Math.floor(tpq / 2),
    durationTicks,
    barCount,
    pixelsPerTick: ppt,
    rowHeight: height,
    totalWidth: Math.max(0, durationTicks * ppt),
    totalHeight: range.pitchCount * height,
    minMidi: range.minMidi,
    maxMidi: range.maxMidi,
    pitchCount: range.pitchCount,
    warning,
  };
}

/**
 * Default duration for a newly created note based on snap value.
 */
export function defaultDurationForSnap(snapValue, ticksPerQuarter) {
  const { snapTicks, warning } = snapIntervalTicks(snapValue, ticksPerQuarter);
  return {
    durationTicks: snapTicks || Math.floor((Number(ticksPerQuarter) || 480) / 2),
    warning,
  };
}

/**
 * Resolve first available track id, preferring melody role.
 */
export function pickDefaultTrackId(composition, preferredTrackId) {
  const tracks = Array.isArray(composition?.tracks) ? composition.tracks : [];
  if (!tracks.length) {
    return null;
  }
  if (preferredTrackId && tracks.some((track) => String(track.id) === String(preferredTrackId))) {
    return String(preferredTrackId);
  }
  const melody = tracks.find((track) => track.role === 'melody');
  return String((melody || tracks[0]).id);
}

/**
 * Sanitize note fields for logging (no full composition dump).
 */
export function sanitizeNoteSummary(note) {
  if (!note) {
    return null;
  }
  return {
    id: note.id,
    pitch: note.pitch,
    start_tick: note.start_tick,
    duration_ticks: note.duration_ticks,
    velocity: note.velocity,
    articulationCount: Array.isArray(note.articulations) ? note.articulations.length : 0,
    tied: Boolean(note.tie),
  };
}

/**
 * Count V2 expressive features for DEBUG logging (no payload dumps).
 */
export function countV2FeatureSummary(composition) {
  if (!composition || typeof composition !== 'object') {
    return {
      tempoChanges: 0,
      meterChanges: 0,
      keyChanges: 0,
      markers: 0,
      sectionLabels: 0,
      tiedNotes: 0,
      articulatedNotes: 0,
      dynamicMarks: 0,
      sustainPedals: 0,
      automationLanes: 0,
    };
  }
  let tiedNotes = 0;
  let articulatedNotes = 0;
  let dynamicMarks = 0;
  let sustainPedals = 0;
  let automationLanes = 0;
  (composition.tracks || []).forEach((track) => {
    dynamicMarks += Array.isArray(track.dynamic_marks) ? track.dynamic_marks.length : 0;
    sustainPedals += Array.isArray(track.sustain_pedals) ? track.sustain_pedals.length : 0;
    automationLanes += Array.isArray(track.automation) ? track.automation.length : 0;
    (track.events || []).forEach((event) => {
      if (event.tie) {
        tiedNotes += 1;
      }
      if (Array.isArray(event.articulations) && event.articulations.length) {
        articulatedNotes += 1;
      }
    });
  });
  const sections = Array.isArray(composition.sections) ? composition.sections : [];
  return {
    tempoChanges: Array.isArray(composition.tempo_changes) ? composition.tempo_changes.length : 0,
    meterChanges: Array.isArray(composition.time_signature_changes) ? composition.time_signature_changes.length : 0,
    keyChanges: Array.isArray(composition.key_changes) ? composition.key_changes.length : 0,
    markers: Array.isArray(composition.markers) ? composition.markers.length : 0,
    sectionLabels: sections.filter((section) => section?.label).length,
    tiedNotes,
    articulatedNotes,
    dynamicMarks,
    sustainPedals,
    automationLanes,
  };
}

/**
 * Read-only timeline cues for piano-roll header (tempo/meter/key/sections/markers).
 */
export function buildTimelineCues(composition) {
  const timeline = compileTimeline(composition);
  if (!timeline) {
    return [];
  }
  const cues = [
    { tick: 0, kind: 'tempo', label: `${timeline.rootTempo} BPM` },
    { tick: 0, kind: 'meter', label: timeline.rootTimeSignature },
    { tick: 0, kind: 'key', label: timeline.rootKey },
  ];
  timeline.tempoChanges.forEach((change) => {
    cues.push({ tick: change.tick, kind: 'tempo', label: `${change.value} BPM` });
  });
  timeline.timeSignatureChanges.forEach((change) => {
    cues.push({ tick: change.tick, kind: 'meter', label: change.value });
  });
  timeline.keyChanges.forEach((change) => {
    cues.push({ tick: change.tick, kind: 'key', label: change.value });
  });
  (Array.isArray(composition.sections) ? composition.sections : []).forEach((section) => {
    if (section?.label && Number.isInteger(Number(section.start_tick))) {
      cues.push({ tick: section.start_tick, kind: 'section', label: section.label });
    }
  });
  (Array.isArray(composition.markers) ? composition.markers : []).forEach((marker) => {
    if (Number.isInteger(Number(marker?.tick)) && marker?.label) {
      cues.push({
        tick: marker.tick,
        kind: marker.kind === 'rehearsal' ? 'rehearsal' : 'marker',
        label: marker.label,
      });
    }
  });
  return cues.sort((left, right) => left.tick - right.tick || left.kind.localeCompare(right.kind));
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
    if (unique.some((name) => GATE_SHORTENING_ARTICULATIONS.has(name))) {
      return 'gate-shortening articulations are not allowed in a tie chain';
    }
    if (unique.some((name) => ATTACK_ARTICULATIONS.has(name)) && tieType !== 'start') {
      return 'attack articulations are only allowed on the tie chain head';
    }
  }
  return null;
}

/**
 * Toggle one articulation on a note; returns invalid result instead of partial writes.
 */
export function toggleNoteArticulation(composition, trackId, noteId, articulation) {
  if (!composition || !Array.isArray(composition.tracks)) {
    return { composition, note: null, warning: 'composition has no tracks' };
  }
  if (!ARTICULATION_VALUES.includes(articulation)) {
    return { composition, note: null, warning: `unsupported articulation: ${articulation}` };
  }
  const trackIndex = composition.tracks.findIndex((track) => String(track.id) === String(trackId));
  if (trackIndex < 0) {
    return { composition, note: null, warning: `track not found: ${trackId}` };
  }

  let updatedNote = null;
  let warning;
  const tracks = composition.tracks.map((track, index) => {
    if (index !== trackIndex) {
      return track;
    }
    const events = (Array.isArray(track.events) ? track.events : []).map((event) => {
      if (String(event.id) !== String(noteId)) {
        return event;
      }
      const current = Array.isArray(event.articulations) ? [...event.articulations] : [];
      const next = current.includes(articulation)
        ? current.filter((name) => name !== articulation)
        : [...current, articulation];
      const tieType = event.tie?.type ?? null;
      const validationMessage = validateArticulationSet(next, tieType);
      if (validationMessage) {
        warning = validationMessage;
        return event;
      }
      updatedNote = { ...event, articulations: next };
      return updatedNote;
    });
    if (!updatedNote && !warning) {
      warning = `note not found: ${noteId}`;
    }
    return { ...track, events };
  });

  return {
    composition: updatedNote ? { ...composition, tracks } : composition,
    note: updatedNote,
    warning,
  };
}

function notesCompatibleForTie(left, right) {
  return left.pitch === right.pitch
    && left.staff === right.staff
    && left.voice === right.voice;
}

/**
 * From note IDs, return contiguous same-pitch/staff/voice notes sorted by start tick.
 */
export function selectContiguousCompatibleNotes(events, noteIds) {
  const ids = new Set((noteIds || []).map(String));
  const selected = (Array.isArray(events) ? events : [])
    .filter((event) => ids.has(String(event.id)))
    .sort((a, b) => a.start_tick - b.start_tick || a.duration_ticks - b.duration_ticks);
  if (selected.length < 2) {
    return { notes: [], warning: 'at least two notes are required for a tie chain' };
  }
  const head = selected[0];
  for (let index = 1; index < selected.length; index += 1) {
    const previous = selected[index - 1];
    const current = selected[index];
    if (!notesCompatibleForTie(head, current)) {
      return { notes: [], warning: 'selected notes must share pitch, staff, and voice' };
    }
    const previousEnd = previous.start_tick + previous.duration_ticks;
    if (current.start_tick !== previousEnd) {
      return { notes: [], warning: 'selected notes must be contiguous' };
    }
  }
  return { notes: selected };
}

function nextTieGroupId(trackId) {
  return `tie-${trackId}-${Date.now().toString(36)}`;
}

/**
 * Write a complete validated tie chain in one immutable update.
 */
export function applyTieChain(composition, trackId, noteIds) {
  if (!composition || !Array.isArray(composition.tracks)) {
    return { composition, notes: [], warning: 'composition has no tracks' };
  }
  const trackIndex = composition.tracks.findIndex((track) => String(track.id) === String(trackId));
  if (trackIndex < 0) {
    return { composition, notes: [], warning: `track not found: ${trackId}` };
  }
  const track = composition.tracks[trackIndex];
  const { notes, warning: selectionWarning } = selectContiguousCompatibleNotes(track.events, noteIds);
  if (!notes.length) {
    return { composition, notes: [], warning: selectionWarning };
  }
  for (const note of notes) {
    if (Array.isArray(note.articulations) && note.articulations.some((name) => GATE_SHORTENING_ARTICULATIONS.has(name))) {
      return { composition, notes: [], warning: 'gate-shortening articulations are not allowed in a tie chain' };
    }
    if (note !== notes[0] && Array.isArray(note.articulations) && note.articulations.some((name) => ATTACK_ARTICULATIONS.has(name))) {
      return { composition, notes: [], warning: 'attack articulations are only allowed on the tie chain head' };
    }
  }

  const groupId = nextTieGroupId(trackId);
  const tiedById = new Map(
    notes.map((note, index) => {
      let type = 'continue';
      if (index === 0) {
        type = 'start';
      } else if (index === notes.length - 1) {
        type = 'stop';
      }
      return [String(note.id), { group_id: groupId, type }];
    }),
  );

  let updatedNotes = [];
  const tracks = composition.tracks.map((item, index) => {
    if (index !== trackIndex) {
      return item;
    }
    const events = (Array.isArray(item.events) ? item.events : []).map((event) => {
      const tie = tiedById.get(String(event.id));
      if (!tie) {
        return event;
      }
      const next = { ...event, tie };
      updatedNotes.push(next);
      return next;
    });
    return { ...item, events };
  });

  return {
    composition: { ...composition, tracks },
    notes: updatedNotes,
    groupId,
  };
}

/**
 * Remove tie metadata from every selected note's tie group in one update.
 */
export function removeTieChain(composition, trackId, noteIds) {
  if (!composition || !Array.isArray(composition.tracks)) {
    return { composition, clearedCount: 0, warning: 'composition has no tracks' };
  }
  const trackIndex = composition.tracks.findIndex((track) => String(track.id) === String(trackId));
  if (trackIndex < 0) {
    return { composition, clearedCount: 0, warning: `track not found: ${trackId}` };
  }
  const track = composition.tracks[trackIndex];
  const ids = new Set((noteIds || []).map(String));
  const groupIds = new Set(
    (track.events || [])
      .filter((event) => ids.has(String(event.id)) && event.tie?.group_id)
      .map((event) => event.tie.group_id),
  );
  if (!groupIds.size) {
    return { composition, clearedCount: 0, warning: 'no tie groups found for selection' };
  }

  let clearedCount = 0;
  const tracks = composition.tracks.map((item, index) => {
    if (index !== trackIndex) {
      return item;
    }
    const events = (Array.isArray(item.events) ? item.events : []).map((event) => {
      if (event.tie?.group_id && groupIds.has(event.tie.group_id)) {
        clearedCount += 1;
        return { ...event, tie: null };
      }
      return event;
    });
    return { ...item, events };
  });

  return {
    composition: { ...composition, tracks },
    clearedCount,
  };
}
