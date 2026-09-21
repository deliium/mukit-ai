/**
 * Apply a reviewed audio transcription preview into composition.v2.
 * Reuses MIDI take extend/append path; strips confidence (V2 extra=forbid).
 */

import { createAppLogger } from './appLogger.js';
import { applyMidiTakeToComposition } from './midiTakeApply.js';
import { midiToPitch, snapIntervalTicks, snapTick } from './pianoRollEvents.js';

const log = createAppLogger('audioTranscription');

export const AUDIO_APPLY_ERROR_CODES = Object.freeze({
  NO_COMPOSITION: 'audio_no_composition',
  NO_TRACK: 'audio_track_missing',
  TRACK_LOCKED: 'audio_track_locked',
  EMPTY_SELECTION: 'audio_empty_selection',
  VALIDATION_FAILED: 'audio_validation_failed',
});

export const DEFAULT_AUDIO_CONFIDENCE_THRESHOLD = 0.5;

/**
 * @param {object} preview transcription.preview.v1-like
 * @param {{ includeLowConfidence?: boolean, threshold?: number, selectedIds?: Set<string>|string[]|null }} options
 */
export function selectPreviewNotesForApply(preview, options = {}) {
  const notes = Array.isArray(preview?.notes) ? preview.notes : [];
  const threshold = Number.isFinite(Number(options.threshold))
    ? Number(options.threshold)
    : Number(preview?.summary?.include_threshold) || DEFAULT_AUDIO_CONFIDENCE_THRESHOLD;
  const includeLow = Boolean(options.includeLowConfidence);
  const selected = options.selectedIds
    ? new Set(Array.from(options.selectedIds).map(String))
    : null;

  const clean = [];
  let excludedLow = 0;
  let excludedUnselected = 0;

  for (const note of notes) {
    const id = String(note.provisional_id || '');
    const confidence = Number(note.confidence);
    const isLow = Number.isFinite(confidence) && confidence < threshold;
    if (selected) {
      if (!selected.has(id)) {
        excludedUnselected += 1;
        continue;
      }
    } else if (isLow && !includeLow) {
      excludedLow += 1;
      continue;
    }
    clean.push(note);
  }

  log.debug('Preview notes selected for apply', {
    total: notes.length,
    included: clean.length,
    excludedLow,
    excludedUnselected,
    includeLow,
    threshold,
  });

  return {
    notes: clean,
    excludedLow,
    excludedUnselected,
    threshold,
  };
}

/**
 * Default selected provisional ids: all notes at/above threshold.
 */
export function defaultSelectedProvisionalIds(preview, threshold = DEFAULT_AUDIO_CONFIDENCE_THRESHOLD) {
  const notes = Array.isArray(preview?.notes) ? preview.notes : [];
  const thr = Number.isFinite(Number(threshold))
    ? Number(threshold)
    : DEFAULT_AUDIO_CONFIDENCE_THRESHOLD;
  return notes
    .filter((note) => Number(note.confidence) >= thr)
    .map((note) => String(note.provisional_id));
}

/**
 * Snap provisional tick times toward the composition grid (pre-Apply quantize).
 */
export function quantizeProvisionalNotes(notes, composition, {
  snapValue = '1/8',
  strength = 100,
} = {}) {
  const tpq = Number(composition?.ticks_per_quarter) || 480;
  const durationLimit = Number(composition?.duration_ticks) || Number.MAX_SAFE_INTEGER;
  const { snapTicks } = snapIntervalTicks(snapValue, tpq);
  if (!snapTicks || strength <= 0) {
    return notes.map((n) => ({ ...n }));
  }
  const factor = Math.max(0, Math.min(100, Number(strength) || 100)) / 100;
  return notes.map((note) => {
    const start = Math.round(Number(note.start_tick) || 0);
    const duration = Math.max(1, Math.round(Number(note.duration_ticks) || 1));
    const snapped = snapTick(start, snapTicks, {
      maxTick: Math.max(0, durationLimit - 1),
    }).tick;
    const nextStart = Math.round(start + (snapped - start) * factor);
    return {
      ...note,
      start_tick: Math.max(0, nextStart),
      duration_ticks: duration,
    };
  });
}

/**
 * Convert preview notes (MIDI int pitch + ticks) to MIDI-take note shape (string pitch).
 */
export function previewNotesToTakeNotes(notes) {
  const out = [];
  for (const note of notes) {
    const midi = Math.round(Number(note.pitch));
    const { pitch } = midiToPitch(midi);
    if (!pitch) {
      continue;
    }
    out.push({
      pitch,
      start_tick: Math.max(0, Math.round(Number(note.start_tick) || 0)),
      duration_ticks: Math.max(1, Math.round(Number(note.duration_ticks) || 1)),
      velocity: Math.max(1, Math.min(127, Math.round(Number(note.velocity) || 80))),
    });
  }
  return out;
}

/**
 * @param {object} composition
 * @param {{
 *   trackId: string,
 *   preview: object,
 *   includeLowConfidence?: boolean,
 *   selectedIds?: string[]|Set<string>|null,
 *   quantize?: boolean,
 *   snapValue?: string,
 *   lockedTrackIds?: string[],
 *   threshold?: number,
 * }} options
 */
export function applyAudioTranscriptionToComposition(composition, options = {}) {
  if (!composition || typeof composition !== 'object') {
    return {
      ok: false,
      code: AUDIO_APPLY_ERROR_CODES.NO_COMPOSITION,
      composition,
      noteRefs: [],
    };
  }

  const selected = selectPreviewNotesForApply(options.preview, {
    includeLowConfidence: options.includeLowConfidence,
    threshold: options.threshold,
    selectedIds: options.selectedIds,
  });

  let provisional = selected.notes;
  if (!provisional.length) {
    log.warn('Audio apply rejected — empty selection', {
      code: AUDIO_APPLY_ERROR_CODES.EMPTY_SELECTION,
    });
    return {
      ok: false,
      code: AUDIO_APPLY_ERROR_CODES.EMPTY_SELECTION,
      composition,
      noteRefs: [],
      excludedLow: selected.excludedLow,
    };
  }

  if (options.quantize) {
    provisional = quantizeProvisionalNotes(provisional, composition, {
      snapValue: options.snapValue || '1/8',
      strength: 100,
    });
    log.info('Pre-apply quantize applied to provisional notes', {
      noteCount: provisional.length,
      snapValue: options.snapValue || '1/8',
    });
  }

  const takeNotes = previewNotesToTakeNotes(provisional);
  if (!takeNotes.length) {
    return {
      ok: false,
      code: AUDIO_APPLY_ERROR_CODES.EMPTY_SELECTION,
      composition,
      noteRefs: [],
    };
  }

  const applied = applyMidiTakeToComposition(composition, {
    trackId: options.trackId,
    notes: takeNotes,
    sustainPedals: [],
    lockedTrackIds: options.lockedTrackIds,
    idPrefix: 'audio',
  });

  if (!applied.ok) {
    const mapped =
      applied.code === 'midi_track_locked'
        ? AUDIO_APPLY_ERROR_CODES.TRACK_LOCKED
        : applied.code === 'midi_track_missing' || applied.code === 'midi_no_composition'
          ? applied.code === 'midi_no_composition'
            ? AUDIO_APPLY_ERROR_CODES.NO_COMPOSITION
            : AUDIO_APPLY_ERROR_CODES.NO_TRACK
          : applied.code === 'midi_validation_failed'
            ? AUDIO_APPLY_ERROR_CODES.VALIDATION_FAILED
            : applied.code;
    log.warn('Audio apply failed via take path', { code: mapped });
    return {
      ...applied,
      code: mapped,
      excludedLow: selected.excludedLow,
    };
  }

  log.info('Audio transcription applied', {
    noteCount: applied.noteRefs.length,
    excludedLow: selected.excludedLow,
    quantize: Boolean(options.quantize),
    trackId: options.trackId,
  });

  return {
    ...applied,
    excludedLow: selected.excludedLow,
    includedCount: applied.noteRefs.length,
  };
}
