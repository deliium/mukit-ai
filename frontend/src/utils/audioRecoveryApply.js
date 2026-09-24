/**
 * Apply a reviewed audio.recovery.preview.v1 into composition.v2.
 * Strips confidence (V2 extra=forbid). Uses ensureRecoveryRoleTracks — never jam.
 *
 * Logging: createAppLogger('audioRecovery') — applied/excluded/stems only.
 */

import { createAppLogger } from './appLogger.js';
import { ensureRecoveryRoleTracks } from './audioRecoveryEnsureTracks.js';
import {
  DEFAULT_RECOVERY_CONFIDENCE_THRESHOLD,
  defaultScaffoldingInstallFlags,
  filterScaffoldingSpans,
  recoveryNotesToTakeNotes,
  selectRecoveryNotesForApply,
} from './audioRecoveryGates.js';
import {
  MIDI_TAKE_ERROR_CODES,
  applyMidiTakeToComposition,
  extendCompositionToTick,
} from './midiTakeApply.js';
import { validateMusicJson } from './musicJsonValidation.js';

const log = createAppLogger('audioRecovery');

export const AUDIO_RECOVERY_APPLY_ERROR_CODES = Object.freeze({
  NO_COMPOSITION: 'audio_recovery_no_composition',
  EMPTY_SELECTION: 'audio_recovery_empty_selection',
  ENSURE_FAILED: 'audio_recovery_ensure_failed',
  TRACK_MISSING: 'audio_recovery_track_missing',
  VALIDATION_FAILED: 'audio_recovery_validation_failed',
});

/**
 * @param {object|null} composition
 * @param {{
 *   preview: object,
 *   stemRoleMap?: Record<string, string|null|undefined>,
 *   instrumentSet?: Record<string, object>,
 *   ensureMissingTracks?: boolean,
 *   includeLowConfidence?: boolean,
 *   selectedIds?: string[]|Set<string>|null,
 *   threshold?: number,
 *   lockedTrackIds?: iterable,
 *   installFlags?: {
 *     installTempo?: boolean,
 *     installSections?: boolean,
 *     installHarmony?: boolean,
 *     includeLowConfidenceHarmony?: boolean,
 *     includeLowConfidenceSections?: boolean,
 *   },
 * }} options
 * @returns {{
 *   ok: boolean,
 *   code?: string,
 *   composition: object|null,
 *   eventMap: Array<{ provisional_id: string, event_id: string, track_id: string }>,
 *   noteRefs: Array<{ trackId: string, eventId: string }>,
 *   excludedLow?: number,
 *   stemsApplied?: string[],
 *   message?: string,
 * }}
 */
export function applyAudioRecoveryToComposition(composition, options = {}) {
  if (!composition || typeof composition !== 'object') {
    return {
      ok: false,
      code: AUDIO_RECOVERY_APPLY_ERROR_CODES.NO_COMPOSITION,
      composition,
      eventMap: [],
      noteRefs: [],
    };
  }

  const preview = options.preview;
  const selected = selectRecoveryNotesForApply(preview, {
    includeLowConfidence: options.includeLowConfidence,
    threshold: options.threshold,
    selectedIds: options.selectedIds,
  });

  if (!selected.notes.length) {
    log.warn('Audio recovery apply rejected — empty selection', {
      code: AUDIO_RECOVERY_APPLY_ERROR_CODES.EMPTY_SELECTION,
    });
    return {
      ok: false,
      code: AUDIO_RECOVERY_APPLY_ERROR_CODES.EMPTY_SELECTION,
      composition,
      eventMap: [],
      noteRefs: [],
      excludedLow: selected.excludedLow,
    };
  }

  // Group notes by stem for multi-track Apply.
  /** @type {Map<string, typeof selected.notes>} */
  const byStem = new Map();
  for (const note of selected.notes) {
    const stem = String(note.stem || 'melody');
    if (!byStem.has(stem)) byStem.set(stem, []);
    byStem.get(stem).push(note);
  }

  /** @type {Record<string, string|null|undefined>} */
  const stemRoleMap = { ...(options.stemRoleMap || {}) };
  for (const stem of byStem.keys()) {
    if (!(stem in stemRoleMap)) {
      stemRoleMap[stem] = null;
    }
  }

  const ensured = ensureRecoveryRoleTracks(
    composition,
    stemRoleMap,
    options.instrumentSet || {},
    { ensure_missing_tracks: options.ensureMissingTracks !== false },
  );
  if (!ensured.ok) {
    return {
      ok: false,
      code: AUDIO_RECOVERY_APPLY_ERROR_CODES.ENSURE_FAILED,
      composition,
      eventMap: [],
      noteRefs: [],
      message: ensured.message,
    };
  }

  let working = ensured.composition;
  const locked = options.lockedTrackIds;
  /** @type {Array<{ provisional_id: string, event_id: string, track_id: string }>} */
  const eventMap = [];
  /** @type {Array<{ trackId: string, eventId: string }>} */
  const noteRefs = [];
  const stemsApplied = [];

  for (const [stem, stemNotes] of byStem.entries()) {
    const trackId = ensured.stemRoleMap[stem];
    if (!trackId) {
      log.warn('Stem missing track after ensure', { stem });
      return {
        ok: false,
        code: AUDIO_RECOVERY_APPLY_ERROR_CODES.TRACK_MISSING,
        composition,
        eventMap: [],
        noteRefs: [],
      };
    }
    const takeNotes = recoveryNotesToTakeNotes(stemNotes);
    if (!takeNotes.length) continue;

    const applied = applyMidiTakeToComposition(working, {
      trackId,
      notes: takeNotes,
      sustainPedals: [],
      lockedTrackIds: locked,
      idPrefix: `rec-${stem}`,
    });
    if (!applied.ok) {
      const mapped =
        applied.code === MIDI_TAKE_ERROR_CODES.VALIDATION_FAILED
          ? AUDIO_RECOVERY_APPLY_ERROR_CODES.VALIDATION_FAILED
          : applied.code === MIDI_TAKE_ERROR_CODES.NO_TRACK
            ? AUDIO_RECOVERY_APPLY_ERROR_CODES.TRACK_MISSING
            : applied.code;
      log.warn('Recovery stem apply failed', { code: mapped, stem });
      return {
        ok: false,
        code: mapped,
        composition,
        eventMap: [],
        noteRefs: [],
        message: applied.message,
      };
    }
    working = applied.composition;
    stemsApplied.push(stem);
    // Pair provisional ids with created event ids in apply order.
    for (let i = 0; i < applied.noteRefs.length; i += 1) {
      const ref = applied.noteRefs[i];
      const provisionalId = takeNotes[i]?.provisional_id || stemNotes[i]?.provisional_id;
      if (!provisionalId) continue;
      eventMap.push({
        provisional_id: String(provisionalId),
        event_id: String(ref.eventId),
        track_id: String(ref.trackId),
      });
      noteRefs.push(ref);
    }
  }

  if (!eventMap.length) {
    return {
      ok: false,
      code: AUDIO_RECOVERY_APPLY_ERROR_CODES.EMPTY_SELECTION,
      composition,
      eventMap: [],
      noteRefs: [],
      excludedLow: selected.excludedLow,
    };
  }

  const flags = {
    ...defaultScaffoldingInstallFlags(
      preview?.scaffolding,
      options.threshold ?? DEFAULT_RECOVERY_CONFIDENCE_THRESHOLD,
    ),
    ...(options.installFlags || {}),
  };
  working = installRecoveryScaffolding(working, preview?.scaffolding, flags);

  const validation = validateMusicJson(working);
  if (!validation.valid) {
    log.warn('Recovery apply validation failed', {
      code: AUDIO_RECOVERY_APPLY_ERROR_CODES.VALIDATION_FAILED,
      message: validation.message,
    });
    return {
      ok: false,
      code: AUDIO_RECOVERY_APPLY_ERROR_CODES.VALIDATION_FAILED,
      composition,
      eventMap: [],
      noteRefs: [],
      message: validation.message,
    };
  }

  log.info('Audio recovery applied', {
    applied: eventMap.length,
    excludedLow: selected.excludedLow,
    stems: stemsApplied,
  });

  return {
    ok: true,
    composition: working,
    eventMap,
    noteRefs,
    excludedLow: selected.excludedLow,
    stemsApplied,
  };
}

/**
 * Install optional scaffolding as V2 metadata only (never playable from spans alone).
 *
 * @param {object} composition
 * @param {object|null|undefined} scaffolding
 * @param {object} flags
 */
export function installRecoveryScaffolding(composition, scaffolding, flags = {}) {
  if (!scaffolding || typeof scaffolding !== 'object') {
    return composition;
  }
  let next = { ...composition };

  if (flags.installTempo && Number.isFinite(Number(scaffolding.tempo_bpm))) {
    const bpm = Math.max(20, Math.min(400, Math.round(Number(scaffolding.tempo_bpm))));
    next = {
      ...next,
      tempo: bpm,
      tempo_bpm: bpm,
      tempo_map: Array.isArray(next.tempo_map) && next.tempo_map.length
        ? next.tempo_map.map((entry, index) => (
          index === 0 ? { ...entry, bpm } : entry
        ))
        : [{ tick: 0, bpm }],
    };
  }

  if (flags.installSections) {
    const sections = filterScaffoldingSpans(scaffolding.structure, {
      includeLowConfidence: flags.includeLowConfidenceSections,
      threshold: flags.threshold,
    }).map((sec, index) => ({
      id: `rec-sec-${index + 1}`,
      name: String(sec.label || `Section ${index + 1}`).slice(0, 64),
      start_tick: Math.max(0, Math.round(Number(sec.start_tick) || 0)),
      end_tick: Math.max(1, Math.round(Number(sec.end_tick) || 1)),
    }));
    if (sections.length) {
      next = { ...next, sections };
      const maxEnd = Math.max(...sections.map((s) => s.end_tick));
      const extended = extendCompositionToTick(next, maxEnd);
      if (!extended.error) {
        next = extended.composition;
      }
    }
  }

  if (flags.installHarmony) {
    const harmony = filterScaffoldingSpans(scaffolding.harmony, {
      includeLowConfidence: flags.includeLowConfidenceHarmony,
      threshold: flags.threshold,
    }).map((span) => ({
      symbol: String(span.symbol || 'N.C.').slice(0, 32),
      start_tick: Math.max(0, Math.round(Number(span.start_tick) || 0)),
      end_tick: Math.max(1, Math.round(Number(span.end_tick) || 1)),
    }));
    if (harmony.length) {
      next = { ...next, harmony };
    }
  }

  return next;
}
