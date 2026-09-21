/**
 * Apply a raw MIDI performance take into composition.v2.
 * Extends the timeline when needed; never invents notes from harmony.
 */

import { createAppLogger } from './appLogger.js';
import {
  activeTimeSignature,
  barDurationTicks,
  compileTimeline,
} from './compositionTimeline.js';
import { validateMusicJson } from './musicJsonValidation.js';
import { ensureNoteId } from './pianoRollEvents.js';

const log = createAppLogger('midiCapture');

export const MIDI_TAKE_ERROR_CODES = Object.freeze({
  NO_COMPOSITION: 'midi_no_composition',
  NO_TRACK: 'midi_track_missing',
  TRACK_LOCKED: 'midi_track_locked',
  EMPTY_TAKE: 'midi_empty_take',
  EXTEND_FAILED: 'midi_extend_failed',
  VALIDATION_FAILED: 'midi_validation_failed',
});

/**
 * Extend composition duration/bar_count/sections to cover endTick (complete bars).
 * Uses the meter active at the current end; mixed-meter mid-extension keeps that
 * last meter for new bars (no new time_signature_changes invented).
 *
 * @param {object} composition
 * @param {number} endTick
 * @returns {{ composition: object, barsAdded: number, previousDuration: number, nextDuration: number, error?: string }}
 */
export function extendCompositionToTick(composition, endTick) {
  if (!composition || typeof composition !== 'object') {
    return {
      composition,
      barsAdded: 0,
      previousDuration: 0,
      nextDuration: 0,
      error: MIDI_TAKE_ERROR_CODES.NO_COMPOSITION,
    };
  }

  const target = Math.max(0, Math.round(Number(endTick) || 0));
  const previousDuration = Number(composition.duration_ticks) || 0;
  if (target <= previousDuration) {
    return {
      composition,
      barsAdded: 0,
      previousDuration,
      nextDuration: previousDuration,
    };
  }

  const timeline = compileTimeline(composition);
  if (!timeline) {
    log.error('Timeline compile failed before extend', {
      code: MIDI_TAKE_ERROR_CODES.EXTEND_FAILED,
    });
    return {
      composition,
      barsAdded: 0,
      previousDuration,
      nextDuration: previousDuration,
      error: MIDI_TAKE_ERROR_CODES.EXTEND_FAILED,
    };
  }

  const meterAtEnd = activeTimeSignature(timeline, Math.max(0, previousDuration - 1));
  const barTicks = barDurationTicks(meterAtEnd, timeline.ticksPerQuarter);
  if (!barTicks) {
    log.error('Cannot compute bar duration for extend', {
      code: MIDI_TAKE_ERROR_CODES.EXTEND_FAILED,
      meter: meterAtEnd,
    });
    return {
      composition,
      barsAdded: 0,
      previousDuration,
      nextDuration: previousDuration,
      error: MIDI_TAKE_ERROR_CODES.EXTEND_FAILED,
    };
  }

  let nextDuration = previousDuration;
  let barsAdded = 0;
  while (nextDuration < target) {
    nextDuration += barTicks;
    barsAdded += 1;
    if (barsAdded > 10_000) {
      return {
        composition,
        barsAdded: 0,
        previousDuration,
        nextDuration: previousDuration,
        error: MIDI_TAKE_ERROR_CODES.EXTEND_FAILED,
      };
    }
  }

  const nextBarCount = (Number(composition.bar_count) || timeline.barCount) + barsAdded;
  const sections = Array.isArray(composition.sections)
    ? composition.sections.map((section, index, list) => {
      if (index !== list.length - 1) {
        return { ...section };
      }
      const extraBars = barsAdded;
      const extraTicks = barsAdded * barTicks;
      return {
        ...section,
        bar_count: (Number(section.bar_count) || 0) + extraBars,
        duration_ticks: (Number(section.duration_ticks) || 0) + extraTicks,
      };
    })
    : composition.sections;

  const next = {
    ...composition,
    duration_ticks: nextDuration,
    bar_count: nextBarCount,
    sections,
  };

  if (!compileTimeline(next)) {
    log.error('Extended composition failed timeline compile', {
      code: MIDI_TAKE_ERROR_CODES.EXTEND_FAILED,
      barsAdded,
    });
    return {
      composition,
      barsAdded: 0,
      previousDuration,
      nextDuration: previousDuration,
      error: MIDI_TAKE_ERROR_CODES.EXTEND_FAILED,
    };
  }

  log.info('Composition timeline extended', {
    barsAdded,
    previousDuration,
    nextDuration,
    meter: meterAtEnd,
  });

  return {
    composition: next,
    barsAdded,
    previousDuration,
    nextDuration,
  };
}

/**
 * Merge new pedal spans into existing non-overlapping sustain_pedals.
 * Overlapping new pedals are skipped with a warning code.
 *
 * @param {Array<{ start_tick: number, duration_ticks: number }>} existing
 * @param {Array<{ start_tick: number, duration_ticks: number }>} incoming
 * @param {number} durationTicks
 */
export function mergeSustainPedals(existing, incoming, durationTicks) {
  const merged = [];
  const warnings = [];
  const all = [
    ...(Array.isArray(existing) ? existing : []),
    ...(Array.isArray(incoming) ? incoming : []),
  ]
    .map((pedal) => ({
      start_tick: Math.max(0, Math.round(Number(pedal.start_tick) || 0)),
      duration_ticks: Math.max(1, Math.round(Number(pedal.duration_ticks) || 1)),
    }))
    .filter((pedal) => pedal.start_tick + pedal.duration_ticks <= durationTicks)
    .sort((a, b) => a.start_tick - b.start_tick || a.duration_ticks - b.duration_ticks);

  let previousEnd = -1;
  for (const pedal of all) {
    if (pedal.start_tick < previousEnd || pedal.start_tick === previousEnd) {
      warnings.push('sustain_overlap_skipped');
      continue;
    }
    merged.push(pedal);
    previousEnd = pedal.start_tick + pedal.duration_ticks;
  }
  return { pedals: merged, warnings };
}

/**
 * @param {object} composition
 * @param {{
 *   trackId: string,
 *   notes?: Array<object>,
 *   sustainPedals?: Array<object>,
 *   lockedTrackIds?: string[],
 *   idPrefix?: string,
 * }} options
 */
export function applyMidiTakeToComposition(composition, options = {}) {
  if (!composition || typeof composition !== 'object') {
    return {
      ok: false,
      code: MIDI_TAKE_ERROR_CODES.NO_COMPOSITION,
      composition,
      noteRefs: [],
    };
  }

  const trackId = options.trackId == null ? null : String(options.trackId);
  const notes = Array.isArray(options.notes) ? options.notes : [];
  const sustainPedals = Array.isArray(options.sustainPedals) ? options.sustainPedals : [];
  const locked = new Set((options.lockedTrackIds || []).map(String));

  if (!trackId) {
    return {
      ok: false,
      code: MIDI_TAKE_ERROR_CODES.NO_TRACK,
      composition,
      noteRefs: [],
    };
  }
  if (locked.has(trackId)) {
    log.warn('MIDI take rejected — locked track', { code: MIDI_TAKE_ERROR_CODES.TRACK_LOCKED });
    return {
      ok: false,
      code: MIDI_TAKE_ERROR_CODES.TRACK_LOCKED,
      composition,
      noteRefs: [],
    };
  }
  if (!notes.length && !sustainPedals.length) {
    return {
      ok: false,
      code: MIDI_TAKE_ERROR_CODES.EMPTY_TAKE,
      composition,
      noteRefs: [],
    };
  }

  const trackIndex = (composition.tracks || []).findIndex(
    (track) => String(track.id) === trackId,
  );
  if (trackIndex < 0) {
    return {
      ok: false,
      code: MIDI_TAKE_ERROR_CODES.NO_TRACK,
      composition,
      noteRefs: [],
    };
  }

  let takeEnd = 0;
  for (const note of notes) {
    takeEnd = Math.max(
      takeEnd,
      (Number(note.start_tick) || 0) + (Number(note.duration_ticks) || 0),
    );
  }
  for (const pedal of sustainPedals) {
    takeEnd = Math.max(
      takeEnd,
      (Number(pedal.start_tick) || 0) + (Number(pedal.duration_ticks) || 0),
    );
  }

  const extended = extendCompositionToTick(composition, takeEnd);
  if (extended.error) {
    return {
      ok: false,
      code: extended.error,
      composition,
      noteRefs: [],
    };
  }

  let working = extended.composition;
  const durationTicks = Number(working.duration_ticks);
  const prefix = options.idPrefix || 'midi';
  const existingEvents = Array.isArray(working.tracks[trackIndex].events)
    ? working.tracks[trackIndex].events
    : [];
  const baseIndex = existingEvents.length;
  const newEvents = [];
  const noteRefs = [];
  const skipWarnings = [];

  notes.forEach((note, index) => {
    const start = Math.round(Number(note.start_tick) || 0);
    const duration = Math.max(1, Math.round(Number(note.duration_ticks) || 1));
    if (start < 0 || start + duration > durationTicks) {
      skipWarnings.push('note_out_of_range');
      return;
    }
    if (typeof note.pitch !== 'string' || !note.pitch) {
      skipWarnings.push('note_missing_pitch');
      return;
    }
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
      prefix,
    });
    newEvents.push({ ...draft, id });
    noteRefs.push({ trackId, eventId: id });
  });

  if (!newEvents.length && !sustainPedals.length) {
    return {
      ok: false,
      code: MIDI_TAKE_ERROR_CODES.EMPTY_TAKE,
      composition,
      noteRefs: [],
      warnings: skipWarnings,
    };
  }

  const existingPedals = working.tracks[trackIndex].sustain_pedals;
  const mergedPedals = mergeSustainPedals(existingPedals, sustainPedals, durationTicks);
  if (mergedPedals.warnings.length) {
    log.warn('Sustain pedal merge skipped overlaps', {
      skipCount: mergedPedals.warnings.length,
    });
  }

  const tracks = working.tracks.map((track, index) => {
    if (index !== trackIndex) {
      return track;
    }
    const nextTrack = {
      ...track,
      events: [...existingEvents, ...newEvents],
    };
    if (mergedPedals.pedals.length) {
      nextTrack.sustain_pedals = mergedPedals.pedals;
    } else if ('sustain_pedals' in track) {
      nextTrack.sustain_pedals = Array.isArray(track.sustain_pedals) ? track.sustain_pedals : [];
    }
    return nextTrack;
  });

  working = { ...working, tracks };

  const validation = validateMusicJson(working);
  if (!validation.valid) {
    log.error('MIDI take validation failed', {
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

  log.info('MIDI take applied', {
    noteCount: newEvents.length,
    pedalCount: sustainPedals.length,
    barsAdded: extended.barsAdded,
    trackId,
  });

  return {
    ok: true,
    composition: working,
    noteRefs,
    barsAdded: extended.barsAdded,
    previousDuration: extended.previousDuration,
    nextDuration: extended.nextDuration,
    warnings: [...skipWarnings, ...mergedPedals.warnings],
  };
}
