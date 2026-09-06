/**
 * Pure Composition V1 piano-roll geometry and immutable event helpers.
 * Logging is caller-owned; helpers return metadata for WARN/DEBUG summaries.
 */

import { barDurationTicks as sharedBarDurationTicks } from './playbackPosition.js';

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
export const SNAP_VALUES = Object.freeze(['1/4', '1/8', '1/16']);
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
  const note = { ...draft, id };

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
  const tracks = composition.tracks.map((track, index) => {
    if (index !== trackIndex) {
      return track;
    }
    const events = Array.isArray(track.events) ? track.events : [];
    const nextEvents = [];
    events.forEach((event) => {
      if (String(event.id) === String(noteId) && !deleted) {
        deleted = event;
        return;
      }
      nextEvents.push(event);
    });
    return { ...track, events: nextEvents };
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

  return {
    ticksPerQuarter: tpq,
    barTicks: barResult.barTicks || tpq * 4,
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
  };
}
