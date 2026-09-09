/**
 * Legacy `{bar, chord}` → explicit tick-span harmony normalization (client mirror).
 * Harmony remains metadata only — never a playable note source.
 */

import { compileBarBoundaries, barAtTick } from './compositionTimeline.js';

export const HARMONY_SHAPE_LEGACY = 'legacy_bar_points';
export const HARMONY_SHAPE_CANONICAL = 'canonical_spans';
export const HARMONY_SHAPE_EMPTY = 'empty';
export const HARMONY_SHAPE_MIXED = 'mixed';
export const HARMONY_SHAPE_MALFORMED = 'malformed';

export class HarmonySpanNormalizationError extends Error {
  constructor(message, { code = HARMONY_SHAPE_MALFORMED } = {}) {
    super(message);
    this.name = 'HarmonySpanNormalizationError';
    this.code = code;
  }
}

function classifyItem(item) {
  if (!item || typeof item !== 'object' || Array.isArray(item)) {
    return HARMONY_SHAPE_MALFORMED;
  }
  const hasBar = Object.prototype.hasOwnProperty.call(item, 'bar');
  const hasStart = Object.prototype.hasOwnProperty.call(item, 'start_tick');
  const hasDuration = Object.prototype.hasOwnProperty.call(item, 'duration_ticks');
  const hasChord = Object.prototype.hasOwnProperty.call(item, 'chord');
  if (hasBar && !hasStart && !hasDuration && hasChord) {
    return HARMONY_SHAPE_LEGACY;
  }
  if (hasStart && hasDuration && hasChord && !hasBar) {
    return HARMONY_SHAPE_CANONICAL;
  }
  if (hasBar && (hasStart || hasDuration)) {
    return HARMONY_SHAPE_MIXED;
  }
  return HARMONY_SHAPE_MALFORMED;
}

export function classifyHarmonyListShape(harmony) {
  if (harmony == null) {
    return HARMONY_SHAPE_EMPTY;
  }
  if (!Array.isArray(harmony)) {
    return HARMONY_SHAPE_MALFORMED;
  }
  if (harmony.length === 0) {
    return HARMONY_SHAPE_EMPTY;
  }
  const shapes = new Set(harmony.map(classifyItem));
  if (shapes.size === 1 && shapes.has(HARMONY_SHAPE_LEGACY)) {
    return HARMONY_SHAPE_LEGACY;
  }
  if (shapes.size === 1 && shapes.has(HARMONY_SHAPE_CANONICAL)) {
    return HARMONY_SHAPE_CANONICAL;
  }
  if (shapes.has(HARMONY_SHAPE_MIXED) || (shapes.has(HARMONY_SHAPE_LEGACY) && shapes.has(HARMONY_SHAPE_CANONICAL))) {
    return HARMONY_SHAPE_MIXED;
  }
  return HARMONY_SHAPE_MALFORMED;
}

export function legacyHarmonyPointsToSpans(harmony, {
  boundaries,
  durationTicks,
  barCount,
} = {}) {
  const indexed = [];
  for (let index = 0; index < harmony.length; index += 1) {
    const item = harmony[index];
    const bar = Number(item.bar);
    const chord = String(item.chord ?? '').trim();
    if (!chord) {
      throw new HarmonySpanNormalizationError('Harmony chord must not be empty', {
        code: HARMONY_SHAPE_MALFORMED,
      });
    }
    if (!Number.isInteger(bar) || bar < 1 || bar > barCount) {
      console.warn('[compositionHarmonySpans] Rejected out-of-range legacy harmony bar', {
        code: 'harmony_legacy_bar_out_of_range',
        bar,
        barCount,
      });
      throw new HarmonySpanNormalizationError(
        `Harmony bar ${bar} is outside 1..${barCount}`,
        { code: 'harmony_legacy_bar_out_of_range' },
      );
    }
    indexed.push({ bar, index, chord });
  }

  indexed.sort((left, right) => (left.bar - right.bar) || (left.index - right.index));
  const collapsed = [];
  let duplicateCollapsed = 0;
  for (const row of indexed) {
    if (collapsed.length && collapsed[collapsed.length - 1].bar === row.bar) {
      duplicateCollapsed += 1;
      collapsed[collapsed.length - 1] = { bar: row.bar, chord: row.chord };
    } else {
      collapsed.push({ bar: row.bar, chord: row.chord });
    }
  }

  if (duplicateCollapsed > 0) {
    console.warn('[compositionHarmonySpans] Collapsed duplicate legacy harmony bars', {
      code: 'harmony_duplicate_bar_collapsed',
      duplicateCollapsedCount: duplicateCollapsed,
      uniqueBarCount: collapsed.length,
    });
  }

  const spans = [];
  for (let position = 0; position < collapsed.length; position += 1) {
    const { bar, chord } = collapsed[position];
    const startTick = boundaries[bar - 1];
    const endTick = position + 1 < collapsed.length
      ? boundaries[collapsed[position + 1].bar - 1]
      : durationTicks;
    const duration = endTick - startTick;
    if (!(duration > 0)) {
      throw new HarmonySpanNormalizationError('Legacy harmony produced a non-positive duration span', {
        code: HARMONY_SHAPE_MALFORMED,
      });
    }
    spans.push({
      start_tick: startTick,
      duration_ticks: duration,
      chord,
    });
  }

  console.info('[compositionHarmonySpans] Normalized legacy harmony to explicit spans', {
    inputCount: harmony.length,
    spanCount: spans.length,
    duplicateCollapsedCount: duplicateCollapsed,
  });

  return {
    spans,
    stats: {
      inputCount: harmony.length,
      spanCount: spans.length,
      duplicateCollapsedCount: duplicateCollapsed,
    },
  };
}

/**
 * Normalize composition.harmony in-place on a cloned V2 document.
 * Returns the same composition object reference passed in.
 */
export function normalizeCompositionHarmonySpans(composition) {
  if (!composition || typeof composition !== 'object') {
    return composition;
  }
  const harmony = composition.harmony;
  const shape = classifyHarmonyListShape(harmony);
  console.debug('[compositionHarmonySpans] Harmony shape dispatch', {
    shape,
    inputCount: Array.isArray(harmony) ? harmony.length : 0,
  });

  if (shape === HARMONY_SHAPE_EMPTY) {
    composition.harmony = Array.isArray(harmony) ? harmony : [];
    return composition;
  }
  if (shape === HARMONY_SHAPE_MIXED) {
    console.warn('[compositionHarmonySpans] Rejected mixed harmony shapes', {
      code: 'harmony_mixed_shape_rejected',
    });
    throw new HarmonySpanNormalizationError(
      'Harmony must not mix {bar, chord} points with explicit tick spans',
      { code: 'harmony_mixed_shape_rejected' },
    );
  }
  if (shape === HARMONY_SHAPE_MALFORMED) {
    console.warn('[compositionHarmonySpans] Rejected malformed harmony items', {
      code: 'harmony_malformed_rejected',
    });
    throw new HarmonySpanNormalizationError(
      'Harmony items must be legacy {bar, chord} or canonical tick spans',
      { code: 'harmony_malformed_rejected' },
    );
  }
  if (shape === HARMONY_SHAPE_CANONICAL) {
    return composition;
  }

  const boundaries = compileBarBoundaries({
    timeSignature: composition.time_signature,
    ticksPerQuarter: composition.ticks_per_quarter,
    barCount: composition.bar_count,
    durationTicks: composition.duration_ticks,
    timeSignatureChanges: composition.time_signature_changes || [],
  });
  if (!boundaries) {
    throw new HarmonySpanNormalizationError(
      'Cannot normalize harmony without a valid meter map',
      { code: HARMONY_SHAPE_MALFORMED },
    );
  }

  const { spans } = legacyHarmonyPointsToSpans(harmony, {
    boundaries,
    durationTicks: Number(composition.duration_ticks),
    barCount: Number(composition.bar_count),
  });
  composition.harmony = spans;
  return composition;
}

export function harmonyChangePointsByBar(composition) {
  const boundaries = compileBarBoundaries({
    timeSignature: composition.time_signature,
    ticksPerQuarter: composition.ticks_per_quarter,
    barCount: composition.bar_count,
    durationTicks: composition.duration_ticks,
    timeSignatureChanges: composition.time_signature_changes || [],
  });
  if (!boundaries) {
    return {};
  }
  const byBar = {};
  for (const item of composition.harmony || []) {
    if (item == null || item.start_tick == null) {
      continue;
    }
    const timeline = {
      barBoundaries: boundaries,
      barCount: Number(composition.bar_count),
      durationTicks: Number(composition.duration_ticks),
    };
    const bar = barAtTick(timeline, Number(item.start_tick));
    if (bar != null) {
      byBar[bar] = item.chord;
    }
  }
  return byBar;
}
