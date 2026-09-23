/**
 * Thin FE chord-tone helper for co-performance local patterns.
 * Maps common chord symbols → pitch classes (0–11). Unparseable → empty set.
 * Metadata only — never invents playable score notes into composition.v2.
 */

import { createAppLogger } from './appLogger.js';

const log = createAppLogger('liveAccompaniment');

const NOTE_TO_PC = Object.freeze({
  C: 0,
  D: 2,
  E: 4,
  F: 5,
  G: 7,
  A: 9,
  B: 11,
});

/** Exported for live performance harmony template scoring (Task 2). */
export const QUALITY_INTERVALS = Object.freeze({
  maj: [0, 4, 7],
  min: [0, 3, 7],
  dim: [0, 3, 6],
  aug: [0, 4, 8],
  sus2: [0, 2, 7],
  sus4: [0, 5, 7],
  '7': [0, 4, 7, 10],
  maj7: [0, 4, 7, 11],
  min7: [0, 3, 7, 10],
  dim7: [0, 3, 6, 9],
  m7b5: [0, 3, 6, 10],
  '6': [0, 4, 7, 9],
  min6: [0, 3, 7, 9],
  '9': [0, 4, 7, 10, 14],
  maj9: [0, 4, 7, 11, 14],
  min9: [0, 3, 7, 10, 14],
});

/** @type {number} */
let unparseableCount = 0;

/**
 * @param {string|null|undefined} symbol
 * @returns {{ rootPc: number|null, quality: string|null, pitchClasses: number[], parseable: boolean }}
 */
export function parseLiveChordSymbol(symbol) {
  const raw = String(symbol || '').trim();
  if (!raw) {
    return emptyParse();
  }

  // Drop slash bass for tone set (bass handled separately if needed later).
  const body = raw.split('/')[0].trim();
  const match = body.match(/^([A-Ga-g])([#b♯♭]?)(.*)$/);
  if (!match) {
    return emptyParse(raw);
  }

  const letter = match[1].toUpperCase();
  let accidental = match[2].replace('♯', '#').replace('♭', 'b');
  const suffix = (match[3] || '').trim().toLowerCase();

  const rootPc = noteToPc(letter, accidental);
  if (rootPc == null) {
    return emptyParse(raw);
  }

  const quality = normalizeQuality(suffix);
  const intervals = QUALITY_INTERVALS[quality];
  if (!intervals) {
    return emptyParse(raw);
  }

  const pitchClasses = [
    ...new Set(intervals.map((interval) => (rootPc + interval) % 12)),
  ].sort((a, b) => a - b);

  return {
    rootPc,
    quality,
    pitchClasses,
    parseable: true,
    raw,
  };
}

/**
 * @param {string|null|undefined} symbol
 * @returns {number[]} pitch classes; empty if unparseable
 */
export function chordTonePitchClasses(symbol) {
  const parsed = parseLiveChordSymbol(symbol);
  if (!parsed.parseable) {
    unparseableCount += 1;
    if (unparseableCount <= 3 || unparseableCount % 32 === 0) {
      log.debug('unparseable chord', { count: unparseableCount });
    }
    return [];
  }
  return parsed.pitchClasses;
}

/**
 * Map pitch classes into a MIDI octave around center (default C3=48..B3).
 * @param {number[]} pitchClasses
 * @param {{ octaveBase?: number }} [opts]
 * @returns {number[]}
 */
export function pitchClassesToMidi(pitchClasses, opts = {}) {
  const base = Number.isFinite(Number(opts.octaveBase))
    ? Math.round(Number(opts.octaveBase))
    : 48;
  return (pitchClasses || [])
    .map((pc) => {
      const n = Number(pc);
      if (!Number.isFinite(n)) return null;
      return base + ((Math.round(n) % 12) + 12) % 12;
    })
    .filter((n) => n != null && n >= 0 && n <= 127);
}

function noteToPc(letter, accidental) {
  const root = NOTE_TO_PC[letter];
  if (root == null) return null;
  if (accidental === '#') return (root + 1) % 12;
  if (accidental === 'b') return (root + 11) % 12;
  return root;
}

function normalizeQuality(suffix) {
  const s = suffix.replace(/\s+/g, '');
  if (!s || s === 'maj' || s === 'M' || s === 'Δ') return 'maj';
  if (s === 'm' || s === 'min' || s === '-') return 'min';
  if (s === 'dim' || s === 'o') return 'dim';
  if (s === 'aug' || s === '+') return 'aug';
  if (s === 'sus2') return 'sus2';
  if (s === 'sus4' || s === 'sus') return 'sus4';
  if (s === '7' || s === 'dom7') return '7';
  if (s === 'maj7' || s === 'M7' || s === 'Δ7' || s === 'ma7') return 'maj7';
  if (s === 'm7' || s === 'min7' || s === '-7') return 'min7';
  if (s === 'dim7' || s === 'o7') return 'dim7';
  if (s === 'm7b5' || s === 'ø' || s === 'ø7' || s === 'min7b5') return 'm7b5';
  if (s === '6') return '6';
  if (s === 'm6' || s === 'min6') return 'min6';
  if (s === '9') return '9';
  if (s === 'maj9' || s === 'M9') return 'maj9';
  if (s === 'm9' || s === 'min9') return 'min9';
  // Bare extension like "maj7" already handled; treat unknown as maj triad only if empty.
  return null;
}

function emptyParse(raw = '') {
  return {
    rootPc: null,
    quality: null,
    pitchClasses: [],
    parseable: false,
    raw,
  };
}

export function resetLiveChordUnparseableCountForTests() {
  unparseableCount = 0;
}
