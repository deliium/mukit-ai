/**
 * Immutable V2 harmony timeline operations and preview verification helpers.
 * Harmony is metadata only — never a playable note source.
 */

import { compileTimeline, barAtTick, barRangeTicks } from './compositionTimeline.js';
import { normalizeCompositionHarmonySpans } from './compositionHarmonySpans.js';

export class HarmonyTimelineClientError extends Error {
  constructor(message, { code = 'harmony_invalid' } = {}) {
    super(message);
    this.name = 'HarmonyTimelineClientError';
    this.code = code;
  }
}

function asSpan(item) {
  return {
    start_tick: Number(item.start_tick),
    duration_ticks: Number(item.duration_ticks),
    chord: String(item.chord).trim(),
  };
}

function spanEnd(span) {
  return span.start_tick + span.duration_ticks;
}

export function mergeAdjacentIdenticalSpans(spans) {
  const ordered = [...spans].map(asSpan).sort((a, b) => a.start_tick - b.start_tick);
  if (ordered.length === 0) {
    return [];
  }
  const merged = [{ ...ordered[0] }];
  for (let i = 1; i < ordered.length; i += 1) {
    const prev = merged[merged.length - 1];
    const cur = ordered[i];
    if (spanEnd(prev) === cur.start_tick && prev.chord === cur.chord) {
      prev.duration_ticks += cur.duration_ticks;
    } else {
      merged.push({ ...cur });
    }
  }
  return merged;
}

function validateRange(startTick, durationTicks, durationLimit) {
  if (!Number.isInteger(startTick) || startTick < 0) {
    throw new HarmonyTimelineClientError('Invalid start_tick', { code: 'harmony_out_of_bounds' });
  }
  if (!Number.isInteger(durationTicks) || durationTicks <= 0) {
    throw new HarmonyTimelineClientError('Invalid duration_ticks', {
      code: 'harmony_non_positive_duration',
    });
  }
  if (startTick + durationTicks > durationLimit) {
    throw new HarmonyTimelineClientError('Span exceeds composition duration', {
      code: 'harmony_out_of_bounds',
    });
  }
  return [startTick, startTick + durationTicks];
}

function spansOverlapping(spans, startTick, endTick) {
  return spans.filter((span) => span.start_tick < endTick && spanEnd(span) > startTick);
}

function fragmentOutside(spans, startTick, endTick) {
  const kept = [];
  for (const span of spans) {
    const s = asSpan(span);
    const end = spanEnd(s);
    if (end <= startTick || s.start_tick >= endTick) {
      kept.push(s);
      continue;
    }
    if (s.start_tick < startTick) {
      kept.push({
        start_tick: s.start_tick,
        duration_ticks: startTick - s.start_tick,
        chord: s.chord,
      });
    }
    if (end > endTick) {
      kept.push({
        start_tick: endTick,
        duration_ticks: end - endTick,
        chord: s.chord,
      });
    }
  }
  return kept;
}

function withHarmony(composition, spans) {
  return {
    ...composition,
    harmony: mergeAdjacentIdenticalSpans(spans),
  };
}

export function applyHarmonyAdd(composition, span) {
  const durationLimit = Number(composition.duration_ticks);
  const [start, end] = validateRange(span.start_tick, span.duration_ticks, durationLimit);
  const current = (composition.harmony || []).map(asSpan);
  if (spansOverlapping(current, start, end).length) {
    throw new HarmonyTimelineClientError('Harmony span overlaps an existing span.', {
      code: 'harmony_overlap',
    });
  }
  return withHarmony(composition, [...current, asSpan(span)]);
}

export function applyHarmonyReplace(composition, { start_tick, duration_ticks, spans = [] }) {
  const durationLimit = Number(composition.duration_ticks);
  const [start, end] = validateRange(start_tick, duration_ticks, durationLimit);
  for (const span of spans) {
    const [s, e] = validateRange(span.start_tick, span.duration_ticks, durationLimit);
    if (s < start || e > end) {
      throw new HarmonyTimelineClientError('Replacement spans must fit within the replace range', {
        code: 'harmony_out_of_bounds',
      });
    }
  }
  const kept = fragmentOutside(composition.harmony || [], start, end);
  kept.push(...spans.map(asSpan));
  return withHarmony(composition, kept);
}

export function applyHarmonyRemove(composition, { start_tick, duration_ticks }) {
  const durationLimit = Number(composition.duration_ticks);
  const [start, end] = validateRange(start_tick, duration_ticks, durationLimit);
  return withHarmony(composition, fragmentOutside(composition.harmony || [], start, end));
}

export function applyHarmonyMove(composition, { source_start_tick, new_start_tick }) {
  const current = (composition.harmony || []).map(asSpan);
  const source = current.find((span) => span.start_tick === source_start_tick);
  if (!source) {
    throw new HarmonyTimelineClientError('No harmony span matches the requested identity.', {
      code: 'harmony_span_not_found',
    });
  }
  const durationLimit = Number(composition.duration_ticks);
  const [start, end] = validateRange(new_start_tick, source.duration_ticks, durationLimit);
  const others = current.filter((span) => span.start_tick !== source_start_tick);
  if (spansOverlapping(others, start, end).length) {
    throw new HarmonyTimelineClientError('Moved harmony span would overlap another span.', {
      code: 'harmony_move_overlap',
    });
  }
  return withHarmony(composition, [
    ...others,
    { start_tick: start, duration_ticks: source.duration_ticks, chord: source.chord },
  ]);
}

export function applyHarmonyResize(composition, { source_start_tick, edge, new_tick }) {
  const current = (composition.harmony || []).map(asSpan);
  const source = current.find((span) => span.start_tick === source_start_tick);
  if (!source) {
    throw new HarmonyTimelineClientError('No harmony span matches the requested identity.', {
      code: 'harmony_span_not_found',
    });
  }
  const durationLimit = Number(composition.duration_ticks);
  let next;
  if (edge === 'start') {
    const end = spanEnd(source);
    if (!Number.isInteger(new_tick) || new_tick < 0 || new_tick >= end) {
      throw new HarmonyTimelineClientError('Invalid resize', { code: 'harmony_invalid_resize' });
    }
    next = { start_tick: new_tick, duration_ticks: end - new_tick, chord: source.chord };
  } else if (edge === 'end') {
    if (!Number.isInteger(new_tick) || new_tick <= source.start_tick || new_tick > durationLimit) {
      throw new HarmonyTimelineClientError('Invalid resize', { code: 'harmony_invalid_resize' });
    }
    next = {
      start_tick: source.start_tick,
      duration_ticks: new_tick - source.start_tick,
      chord: source.chord,
    };
  } else {
    throw new HarmonyTimelineClientError('Invalid resize edge', { code: 'harmony_invalid_resize' });
  }
  validateRange(next.start_tick, next.duration_ticks, durationLimit);
  const others = current.filter((span) => span.start_tick !== source_start_tick);
  if (spansOverlapping(others, next.start_tick, spanEnd(next)).length) {
    throw new HarmonyTimelineClientError('Resize would overlap another span.', {
      code: 'harmony_invalid_resize',
    });
  }
  return withHarmony(composition, [...others, next]);
}

export function inferredBarLabelForSpan(composition, span) {
  const timeline = compileTimeline(composition);
  if (!timeline) {
    return null;
  }
  return barAtTick(timeline, span.start_tick);
}

export function selectionTicksFromBars(composition, startBar, endBar) {
  const timeline = compileTimeline(composition);
  if (!timeline) {
    throw new HarmonyTimelineClientError('Unable to compile meter map', {
      code: 'harmony_out_of_bounds',
    });
  }
  const range = barRangeTicks(timeline, startBar, endBar);
  if (!range) {
    throw new HarmonyTimelineClientError('Invalid bar selection', {
      code: 'harmony_out_of_bounds',
    });
  }
  return range;
}

export function diffHarmonySpans(before = [], after = []) {
  const beforeKey = new Map(
    before.map((span) => [`${span.start_tick}:${span.duration_ticks}:${span.chord}`, asSpan(span)]),
  );
  const afterKey = new Map(
    after.map((span) => [`${span.start_tick}:${span.duration_ticks}:${span.chord}`, asSpan(span)]),
  );
  const removed = [];
  const added = [];
  for (const [key, span] of beforeKey) {
    if (!afterKey.has(key)) {
      removed.push(span);
    }
  }
  for (const [key, span] of afterKey) {
    if (!beforeKey.has(key)) {
      added.push(span);
    }
  }
  return { added, removed };
}

function eventFingerprint(event) {
  return [
    event.type || 'note',
    event.id ?? null,
    event.pitch,
    Number(event.start_tick),
    Number(event.duration_ticks),
    Number(event.velocity || 0),
  ].join('|');
}

/**
 * Pure candidate verification for an accepted reharmonization preview.
 */
export function verifyReharmonizationCandidate({
  baseComposition,
  candidateComposition,
  startTick,
  endTick,
  authorizedTrackIds = [],
  preserveMelody = true,
  preserveHarmony = false,
  baseFingerprint,
  proposalFingerprint,
  responseBaseFingerprint,
  responseProposalFingerprint,
}) {
  const failures = [];
  if (baseFingerprint && responseBaseFingerprint && baseFingerprint !== responseBaseFingerprint) {
    failures.push({ code: 'stale_base_fingerprint' });
  }
  if (
    proposalFingerprint
    && responseProposalFingerprint
    && proposalFingerprint !== responseProposalFingerprint
  ) {
    failures.push({ code: 'stale_proposal_fingerprint' });
  }

  const auth = new Set(authorizedTrackIds);
  const baseTracks = new Map((baseComposition.tracks || []).map((t) => [t.id, t]));
  const candTracks = new Map((candidateComposition.tracks || []).map((t) => [t.id, t]));
  if (baseTracks.size !== candTracks.size) {
    failures.push({ code: 'structural_track_mismatch' });
  }

  for (const [trackId, baseTrack] of baseTracks) {
    const candTrack = candTracks.get(trackId);
    if (!candTrack) {
      failures.push({ code: 'missing_track', track_id: trackId });
      continue;
    }
    const role = baseTrack.role || 'other';
    const baseEvents = baseTrack.events || [];
    const candEvents = candTrack.events || [];
    if (preserveMelody && (role === 'melody' || role === 'lead')) {
      if (baseEvents.map(eventFingerprint).join(',') !== candEvents.map(eventFingerprint).join(',')) {
        failures.push({ code: 'melody_not_preserved', track_id: trackId });
      }
    }
    if (!auth.has(trackId) && !(preserveMelody && (role === 'melody' || role === 'lead'))) {
      if (baseEvents.map(eventFingerprint).join(',') !== candEvents.map(eventFingerprint).join(',')) {
        // Non-target may still differ only inside range when authorized empty — treat as failure.
        const baseOutside = baseEvents
          .filter(
            (e) => Number(e.start_tick) + Number(e.duration_ticks) <= startTick
              || Number(e.start_tick) >= endTick,
          )
          .map(eventFingerprint);
        const candOutside = candEvents
          .filter(
            (e) => Number(e.start_tick) + Number(e.duration_ticks) <= startTick
              || Number(e.start_tick) >= endTick,
          )
          .map(eventFingerprint);
        if (baseOutside.join(',') !== candOutside.join(',')) {
          failures.push({ code: 'outside_range_changed', track_id: trackId });
        }
        if (!auth.has(trackId) && baseEvents.map(eventFingerprint).join(',') !== candEvents.map(eventFingerprint).join(',')) {
          failures.push({ code: 'unauthorized_track_changed', track_id: trackId });
        }
      }
    }
  }

  if (preserveHarmony) {
    const before = (baseComposition.harmony || []).map((s) => `${s.start_tick}:${s.duration_ticks}:${s.chord}`).join(',');
    const after = (candidateComposition.harmony || []).map((s) => `${s.start_tick}:${s.duration_ticks}:${s.chord}`).join(',');
    if (before !== after) {
      failures.push({ code: 'harmony_not_preserved' });
    }
  }

  return { ok: failures.length === 0, failures };
}

export function ensureCanonicalHarmony(composition) {
  return normalizeCompositionHarmonySpans({ ...composition });
}
