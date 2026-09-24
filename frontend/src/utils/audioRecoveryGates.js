/**
 * Pure correction + uncertainty gate helpers for audio recovery (no panel UI).
 * Never fabricates notes for empty stems / failed poly — callers emit issues instead.
 *
 * Logging: createAppLogger('audioRecovery') — gate threshold / op counts.
 */

import { createAppLogger } from './appLogger.js';
import { midiToPitch } from './pianoRollEvents.js';

const log = createAppLogger('audioRecovery');

export const DEFAULT_RECOVERY_CONFIDENCE_THRESHOLD = 0.5;

/**
 * Default selected provisional ids: notes at/above threshold (uncertain excluded).
 *
 * @param {object|null|undefined} preview audio.recovery.preview.v1-like
 * @param {number} [threshold]
 * @returns {string[]}
 */
export function defaultSelectedRecoveryProvisionalIds(
  preview,
  threshold = DEFAULT_RECOVERY_CONFIDENCE_THRESHOLD,
) {
  const notes = Array.isArray(preview?.notes) ? preview.notes : [];
  const thr = Number.isFinite(Number(threshold))
    ? Number(threshold)
    : Number(preview?.summary?.include_threshold) || DEFAULT_RECOVERY_CONFIDENCE_THRESHOLD;
  const selected = notes
    .filter((note) => Number(note.confidence) >= thr)
    .map((note) => String(note.provisional_id));
  log.info('Recovery gate default selection', {
    threshold: thr,
    total: notes.length,
    selected: selected.length,
  });
  return selected;
}

/**
 * @param {object|null|undefined} preview
 * @param {{
 *   includeLowConfidence?: boolean,
 *   threshold?: number,
 *   selectedIds?: Set<string>|string[]|null,
 * }} [options]
 */
export function selectRecoveryNotesForApply(preview, options = {}) {
  const notes = Array.isArray(preview?.notes) ? preview.notes : [];
  const threshold = Number.isFinite(Number(options.threshold))
    ? Number(options.threshold)
    : Number(preview?.summary?.include_threshold) || DEFAULT_RECOVERY_CONFIDENCE_THRESHOLD;
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

  log.debug('Recovery notes selected for apply', {
    total: notes.length,
    included: clean.length,
    excludedLow,
    excludedUnselected,
    includeLow,
    threshold,
  });

  if (!clean.length) {
    log.warn('Empty Apply selection attempt', {
      total: notes.length,
      excludedLow,
      excludedUnselected,
      threshold,
    });
  }

  return {
    notes: clean,
    excludedLow,
    excludedUnselected,
    threshold,
  };
}

/**
 * Toggle include/exclude for a provisional id in a selection set.
 *
 * @param {Iterable<string>} selectedIds
 * @param {string} provisionalId
 * @param {boolean} include
 * @returns {Set<string>}
 */
export function toggleProvisionalInclusion(selectedIds, provisionalId, include) {
  const next = new Set(Array.from(selectedIds || []).map(String));
  const id = String(provisionalId || '');
  if (!id) return next;
  if (include) next.add(id);
  else next.delete(id);
  log.debug('Provisional inclusion toggled', { include, selectionSize: next.size });
  return next;
}

/**
 * Pitch nudge (± semitones) on provisional notes — never invents new notes.
 *
 * @param {Array<object>} notes
 * @param {string} provisionalId
 * @param {number} semitoneDelta
 * @returns {Array<object>}
 */
export function nudgeProvisionalPitch(notes, provisionalId, semitoneDelta) {
  const id = String(provisionalId || '');
  const delta = Math.round(Number(semitoneDelta) || 0);
  if (!id || !delta) return Array.isArray(notes) ? notes.map((n) => ({ ...n })) : [];
  let changed = 0;
  const next = (Array.isArray(notes) ? notes : []).map((note) => {
    if (String(note.provisional_id) !== id) return { ...note };
    const pitch = Math.max(0, Math.min(127, Math.round(Number(note.pitch) || 0) + delta));
    changed += 1;
    return { ...note, pitch };
  });
  log.debug('Provisional pitch nudge', { changed, delta });
  return next;
}

/**
 * Time nudge (tick delta) on provisional notes.
 *
 * @param {Array<object>} notes
 * @param {string} provisionalId
 * @param {number} tickDelta
 * @returns {Array<object>}
 */
export function nudgeProvisionalTime(notes, provisionalId, tickDelta) {
  const id = String(provisionalId || '');
  const delta = Math.round(Number(tickDelta) || 0);
  if (!id || !delta) return Array.isArray(notes) ? notes.map((n) => ({ ...n })) : [];
  let changed = 0;
  const next = (Array.isArray(notes) ? notes : []).map((note) => {
    if (String(note.provisional_id) !== id) return { ...note };
    const start = Math.max(0, Math.round(Number(note.start_tick) || 0) + delta);
    changed += 1;
    return { ...note, start_tick: start };
  });
  log.debug('Provisional time nudge', { changed, delta });
  return next;
}

/**
 * Remap a note's product stem (pre-Apply). Does not invent notes.
 *
 * @param {Array<object>} notes
 * @param {string} provisionalId
 * @param {string} newStem
 * @returns {Array<object>}
 */
export function remapProvisionalStem(notes, provisionalId, newStem) {
  const id = String(provisionalId || '');
  const stem = String(newStem || '');
  if (!id || !stem) return Array.isArray(notes) ? notes.map((n) => ({ ...n })) : [];
  let changed = 0;
  const next = (Array.isArray(notes) ? notes : []).map((note) => {
    if (String(note.provisional_id) !== id) return { ...note };
    changed += 1;
    return { ...note, stem };
  });
  log.debug('Provisional stem remap', { changed, stem });
  return next;
}

/**
 * Scaffolding install flags defaults — low-confidence harmony/sections off.
 *
 * @param {object|null|undefined} scaffolding
 * @param {number} [threshold]
 */
export function defaultScaffoldingInstallFlags(
  scaffolding,
  threshold = DEFAULT_RECOVERY_CONFIDENCE_THRESHOLD,
) {
  const thr = Number.isFinite(Number(threshold))
    ? Number(threshold)
    : DEFAULT_RECOVERY_CONFIDENCE_THRESHOLD;
  const tempoOk = Number(scaffolding?.tempo_confidence) >= thr;
  const keyOk = Number(scaffolding?.key?.confidence) >= thr;
  return {
    installTempo: tempoOk,
    installSections: false,
    installHarmony: false,
    includeLowConfidenceHarmony: false,
    includeLowConfidenceSections: false,
    installKey: keyOk,
  };
}

/**
 * Filter harmony/structure candidates by confidence + include flags.
 * Empty → [] with no fabrication.
 *
 * @param {Array<{ confidence?: number }>|null|undefined} spans
 * @param {{ includeLowConfidence?: boolean, threshold?: number }} [options]
 */
export function filterScaffoldingSpans(spans, options = {}) {
  const list = Array.isArray(spans) ? spans : [];
  const thr = Number.isFinite(Number(options.threshold))
    ? Number(options.threshold)
    : DEFAULT_RECOVERY_CONFIDENCE_THRESHOLD;
  const includeLow = Boolean(options.includeLowConfidence);
  if (!list.length) {
    return [];
  }
  return list.filter((span) => {
    const conf = Number(span.confidence);
    if (!Number.isFinite(conf)) return includeLow;
    return includeLow || conf >= thr;
  });
}

/**
 * Convert recovery preview notes (MIDI int pitch) to take notes (string pitch).
 * Skips invalid pitches — never invents replacements.
 *
 * @param {Array<object>} notes
 */
export function recoveryNotesToTakeNotes(notes) {
  const out = [];
  for (const note of Array.isArray(notes) ? notes : []) {
    const midi = Math.round(Number(note.pitch));
    const { pitch } = midiToPitch(midi);
    if (!pitch) continue;
    out.push({
      pitch,
      start_tick: Math.max(0, Math.round(Number(note.start_tick) || 0)),
      duration_ticks: Math.max(1, Math.round(Number(note.duration_ticks) || 1)),
      velocity: Math.max(1, Math.min(127, Math.round(Number(note.velocity) || 80))),
      provisional_id: note.provisional_id != null ? String(note.provisional_id) : undefined,
      stem: note.stem != null ? String(note.stem) : undefined,
    });
  }
  return out;
}
