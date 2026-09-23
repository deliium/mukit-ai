/**
 * Deterministic warm-path live performance feature extractor.
 *
 * Pure analysis over a bounded MIDI ring window + live clock →
 * `live.performance.features.v1` (raw only — never embeds harmony belief).
 *
 * Warm-path / rAF pump wiring belongs in later jam tasks (musicStore).
 */

import { createAppLogger } from './appLogger.js';
import {
  QUALITY_INTERVALS,
} from './liveChordTones.js';
import {
  createEmptyLivePerformanceFeatures,
  normalizeLivePerformanceFeatures,
  readLiveJamSettings,
} from './liveJamContracts.js';
import { MIDI_MESSAGE_KINDS } from './midiInputMessages.js';

const log = createAppLogger('liveJam');

/** Krumhansl–Kessler major profile (tonic = index 0). */
const MAJOR_PROFILE = Object.freeze([
  6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88,
]);

/** Krumhansl–Kessler minor profile (tonic = index 0). */
const MINOR_PROFILE = Object.freeze([
  6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17,
]);

const PC_NAMES = Object.freeze([
  'C', 'C#', 'D', 'Eb', 'E', 'F', 'F#', 'G', 'Ab', 'A', 'Bb', 'B',
]);

/** Qualities used for live template matching (subset of QUALITY_INTERVALS). */
const HARMONY_TEMPLATE_QUALITIES = Object.freeze([
  'maj', 'min', 'dim', 'aug', 'sus2', 'sus4',
  '7', 'maj7', 'min7', 'dim7', 'm7b5', '6', 'min6',
]);

const QUALITY_SYMBOL_SUFFIX = Object.freeze({
  maj: '',
  min: 'm',
  dim: 'dim',
  aug: 'aug',
  sus2: 'sus2',
  sus4: 'sus4',
  '7': '7',
  maj7: 'maj7',
  min7: 'm7',
  dim7: 'dim7',
  m7b5: 'm7b5',
  '6': '6',
  min6: 'm6',
});

/** @type {number} */
let emptyWarnCount = 0;

/**
 * Extract raw live.performance.features.v1 from ring events + clock.
 *
 * @param {{
 *   events?: Array<{ kind: string, tick: number, note?: number }>,
 *   clock: {
 *     tick?: number,
 *     bar?: number,
 *     beatInBar?: number,
 *     beat_in_bar?: number,
 *     tickInBar?: number,
 *     tick_in_bar?: number,
 *     barTicks?: number,
 *     ticksPerBeat?: number,
 *     tempo?: number,
 *   },
 *   settings?: ReturnType<typeof readLiveJamSettings>,
 *   latencyTracker?: { markStart?: Function, markEnd?: Function }|null,
 *   phraseState?: { lastBoundaryTick?: number|null }|null,
 * }} args
 */
export function extractLivePerformanceFeatures(args) {
  const tracker = args?.latencyTracker || null;
  if (tracker && typeof tracker.markStart === 'function') {
    tracker.markStart('analysis');
  }

  try {
    const settings = args?.settings || readLiveJamSettings();
    const clock = args?.clock && typeof args.clock === 'object' ? args.clock : {};
    const events = Array.isArray(args?.events) ? args.events : [];
    const tick = Math.max(0, Math.round(Number(clock.tick) || 0));
    const bar = Math.max(1, Math.round(Number(clock.bar) || 1));
    const beatInBar = Math.max(
      1,
      Math.round(Number(clock.beatInBar ?? clock.beat_in_bar) || 1),
    );
    const tickInBar = Math.max(
      0,
      Math.round(Number(clock.tickInBar ?? clock.tick_in_bar) || 0),
    );
    const barTicks = Math.max(
      1,
      Math.round(Number(clock.barTicks) || (Number(clock.ticksPerBeat) || 480) * 4),
    );
    const ticksPerBeat = Math.max(
      1,
      Math.round(Number(clock.ticksPerBeat) || Math.round(barTicks / 4)),
    );
    const tempo = Math.max(1, Number(clock.tempo) || 100);

    if (events.length === 0) {
      emptyWarnCount += 1;
      if (emptyWarnCount <= 3 || emptyWarnCount % 32 === 0) {
        log.warn('live features empty ring', { count: emptyWarnCount, tick });
      }
    }

    const noteOns = [];
    for (const entry of events) {
      if (entry?.kind === MIDI_MESSAGE_KINDS.NOTE_ON && Number.isFinite(Number(entry.note))) {
        noteOns.push({
          tick: Math.max(0, Math.round(Number(entry.tick) || 0)),
          note: Math.max(0, Math.min(127, Math.round(Number(entry.note)))),
        });
      }
    }

    const pitchActivity = computePitchActivity(noteOns, {
      tick,
      ticksPerBeat,
      tempo,
      maxEvents: settings.analysisMaxEvents,
    });
    const probableKey = scoreProbableKey(pitchActivity.pc_histogram_12);
    const probableHarmony = scoreProbableHarmony(pitchActivity.pc_histogram_12, noteOns);
    const phrase = computePhraseHeuristic(noteOns, {
      tick,
      bar,
      barTicks,
      phraseState: args?.phraseState || null,
    });

    const raw = {
      schema: 'live.performance.features.v1',
      pitch_activity: pitchActivity,
      beat: {
        tick,
        bar,
        beat_in_bar: beatInBar,
        tick_in_bar: tickInBar,
      },
      probable_key: probableKey,
      probable_harmony: probableHarmony,
      phrase,
    };

    const features = normalizeLivePerformanceFeatures(raw);
    log.debug('features extract', {
      event_count: events.length,
      note_on_count: noteOns.length,
      tick,
      bar,
      harmony_conf: Number(features.probable_harmony.confidence.toFixed(3)),
      key_conf: Number(features.probable_key.confidence.toFixed(3)),
    });
    return features;
  } finally {
    if (tracker && typeof tracker.markEnd === 'function') {
      tracker.markEnd('analysis');
    }
  }
}

/**
 * @param {Array<{ tick: number, note: number }>} noteOns
 * @param {{ tick: number, ticksPerBeat: number, tempo: number, maxEvents: number }} ctx
 */
function computePitchActivity(noteOns, ctx) {
  if (noteOns.length === 0) {
    return {
      note_on_rate: 0,
      pc_histogram_12: Array.from({ length: 12 }, () => 0),
      register_mean: 60,
      register_var: 0,
    };
  }

  const counts = Array.from({ length: 12 }, () => 0);
  let sumMidi = 0;
  for (const n of noteOns) {
    counts[n.note % 12] += 1;
    sumMidi += n.note;
  }
  const total = noteOns.length;
  const hist = counts.map((c) => c / total);
  const mean = sumMidi / total;
  let varAcc = 0;
  for (const n of noteOns) {
    const d = n.note - mean;
    varAcc += d * d;
  }
  const registerVar = varAcc / total;

  const minTick = noteOns[0].tick;
  const maxTick = Math.max(noteOns[noteOns.length - 1].tick, ctx.tick);
  const spanTicks = Math.max(ctx.ticksPerBeat, maxTick - minTick);
  const spanBeats = spanTicks / ctx.ticksPerBeat;
  const noteOnRate = spanBeats > 0 ? total / spanBeats : total;

  return {
    note_on_rate: noteOnRate,
    pc_histogram_12: hist,
    register_mean: mean,
    register_var: registerVar,
  };
}

/**
 * PC-mass correlation against major/minor profiles (no torch).
 * @param {number[]} hist
 */
function scoreProbableKey(hist) {
  let best = { tonic_pc: 0, mode: 'major', score: -Infinity };
  let second = -Infinity;

  for (let tonic = 0; tonic < 12; tonic += 1) {
    for (const [mode, profile] of [
      ['major', MAJOR_PROFILE],
      ['minor', MINOR_PROFILE],
    ]) {
      let score = 0;
      for (let i = 0; i < 12; i += 1) {
        score += (hist[i] || 0) * profile[(i - tonic + 12) % 12];
      }
      if (score > best.score || (
        score === best.score
        && (tonic < best.tonic_pc || (tonic === best.tonic_pc && mode === 'major'))
      )) {
        second = best.score;
        best = { tonic_pc: tonic, mode, score };
      } else if (score > second) {
        second = score;
      }
    }
  }

  const mass = hist.reduce((a, b) => a + b, 0);
  if (mass <= 0 || !Number.isFinite(best.score) || best.score <= 0) {
    return { tonic_pc: 0, mode: 'major', confidence: 0 };
  }
  const margin = best.score - (Number.isFinite(second) ? second : 0);
  const confidence = Math.max(0, Math.min(1, margin / Math.max(best.score, 1e-6)));
  return {
    tonic_pc: best.tonic_pc,
    mode: best.mode,
    confidence,
  };
}

/**
 * Match chord templates via QUALITY_INTERVALS; score overlapping PCs.
 * @param {number[]} hist
 * @param {Array<{ note: number }>} noteOns
 */
function scoreProbableHarmony(hist, noteOns) {
  const distinctPcs = new Set();
  for (const n of noteOns) {
    distinctPcs.add(n.note % 12);
  }
  // Ambiguous single pitch class → low confidence (belief layer holds).
  if (distinctPcs.size <= 1) {
    const only = distinctPcs.size === 1 ? [...distinctPcs][0] : null;
    return {
      symbol: only != null ? `${PC_NAMES[only]}` : null,
      root_pc: only,
      quality: only != null ? 'maj' : null,
      confidence: distinctPcs.size === 1 ? 0.22 : 0,
    };
  }

  let best = {
    symbol: null,
    root_pc: null,
    quality: null,
    score: -1,
  };

  for (let root = 0; root < 12; root += 1) {
    for (const quality of HARMONY_TEMPLATE_QUALITIES) {
      const intervals = QUALITY_INTERVALS[quality];
      if (!intervals) continue;
      const template = new Set(intervals.map((iv) => (root + iv) % 12));
      const size = template.size;
      if (size === 0) continue;
      let massIn = 0;
      let present = 0;
      for (const pc of template) {
        const w = hist[pc] || 0;
        if (w > 0) {
          present += 1;
          massIn += w;
        }
      }
      let outsider = 0;
      for (let pc = 0; pc < 12; pc += 1) {
        if (!template.has(pc)) outsider += hist[pc] || 0;
      }
      const recall = present / size;
      const precision = massIn / (massIn + outsider + 1e-6);
      // Prefer complete smaller templates (triad over 6/maj7 when tones match).
      const score = recall * 0.55 + precision * 0.4 + (1 / (size + 1)) * 0.05;
      const better = score > best.score + 1e-9
        || (
          Math.abs(score - best.score) <= 1e-9
          && (
            size < (QUALITY_INTERVALS[best.quality]?.length ?? 99)
            || (
              size === (QUALITY_INTERVALS[best.quality]?.length ?? 99)
              && (root < (best.root_pc ?? 99)
                || (root === best.root_pc && quality < (best.quality || '')))
            )
          )
        );
      if (better) {
        best = {
          symbol: `${PC_NAMES[root]}${QUALITY_SYMBOL_SUFFIX[quality] ?? quality}`,
          root_pc: root,
          quality,
          score,
        };
      }
    }
  }

  if (best.score < 0 || best.symbol == null) {
    return { symbol: null, root_pc: null, quality: null, confidence: 0 };
  }

  const confidence = Math.max(0, Math.min(1, best.score));
  return {
    symbol: best.symbol,
    root_pc: best.root_pc,
    quality: best.quality,
    confidence,
  };
}

/**
 * IOI / bar silence heuristic → phrase boundary hints.
 * @param {Array<{ tick: number }>} noteOns
 * @param {{ tick: number, bar: number, barTicks: number, phraseState: { lastBoundaryTick?: number|null }|null }} ctx
 */
function computePhraseHeuristic(noteOns, ctx) {
  const barTicks = ctx.barTicks;
  const silenceThreshold = Math.max(barTicks * 0.85, 1);
  let boundaryLikely = false;
  let boundaryConfidence = 0;
  let lastBoundaryTick = ctx.phraseState?.lastBoundaryTick ?? null;

  if (noteOns.length === 0) {
    // Full silence at playhead — treat as soft boundary if we had prior material.
    if (lastBoundaryTick != null && ctx.tick - lastBoundaryTick >= silenceThreshold) {
      boundaryLikely = true;
      boundaryConfidence = 0.55;
      lastBoundaryTick = ctx.tick;
    }
  } else if (noteOns.length >= 2) {
    let maxIoi = 0;
    let maxIoiTick = noteOns[0].tick;
    for (let i = 1; i < noteOns.length; i += 1) {
      const ioi = noteOns[i].tick - noteOns[i - 1].tick;
      if (ioi > maxIoi) {
        maxIoi = ioi;
        maxIoiTick = noteOns[i].tick;
      }
    }
    const tailGap = Math.max(0, ctx.tick - noteOns[noteOns.length - 1].tick);
    if (maxIoi >= silenceThreshold) {
      boundaryLikely = true;
      boundaryConfidence = Math.min(1, maxIoi / (barTicks * 1.5));
      lastBoundaryTick = maxIoiTick;
    } else if (tailGap >= silenceThreshold) {
      boundaryLikely = true;
      boundaryConfidence = Math.min(1, tailGap / (barTicks * 1.5));
      lastBoundaryTick = ctx.tick;
    }
  } else {
    const tailGap = Math.max(0, ctx.tick - noteOns[0].tick);
    if (tailGap >= silenceThreshold) {
      boundaryLikely = true;
      boundaryConfidence = Math.min(1, tailGap / (barTicks * 1.5));
      lastBoundaryTick = ctx.tick;
    }
  }

  if (boundaryLikely && ctx.phraseState && typeof ctx.phraseState === 'object') {
    ctx.phraseState.lastBoundaryTick = lastBoundaryTick;
  }

  const refBoundary = lastBoundaryTick != null
    ? lastBoundaryTick
    : (noteOns.length > 0 ? noteOns[0].tick : ctx.tick);
  const barsSince = Math.max(0, Math.floor((ctx.tick - refBoundary) / barTicks));

  return {
    boundary_likely: boundaryLikely,
    bars_since_boundary: barsSince,
    confidence: boundaryConfidence,
  };
}

/** Idle / empty features helper re-export for callers. */
export { createEmptyLivePerformanceFeatures };

/** Test helper. */
export function resetLivePerformanceFeaturesWarnCountForTests() {
  emptyWarnCount = 0;
}
