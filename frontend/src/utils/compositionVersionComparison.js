/**
 * Deterministic musical/structural comparison for composition.v2 snapshots.
 * Pure and source-immutable: never mutates inputs; does not invent notes from harmony.
 *
 * Event matching:
 * 1. Unique event IDs on both sides
 * 2. Remaining events by canonical musical-field multiset key + occurrence index
 * ID-only replacements count as identity churn, not musical changes.
 */

import { canonicalizeValue } from './compositionCanonical.js';
import { barAtTick, compileTimeline } from './compositionTimeline.js';

const AFFECTED_RANGE_MAX = 64;
const AFFECTED_TRACK_MAX = 64;

/**
 * Musical identity key excluding event id (stable for multiset matching).
 * @param {object} event
 * @returns {string}
 */
export function musicalEventKey(event) {
  const articulations = Array.isArray(event?.articulations)
    ? [...event.articulations].map(String).sort()
    : [];
  return JSON.stringify(canonicalizeValue({
    type: event?.type || 'note',
    pitch: event?.pitch ?? null,
    start_tick: Number(event?.start_tick) || 0,
    duration_ticks: Number(event?.duration_ticks) || 0,
    velocity: Number(event?.velocity) || 0,
    staff: event?.staff ?? null,
    voice: event?.voice ?? null,
    articulations,
    tie: event?.tie ?? null,
  }));
}

function emptyCompositionShape() {
  return {
    tempo: null,
    key: null,
    time_signature: null,
    ticks_per_quarter: null,
    bar_count: 0,
    duration_ticks: 0,
    sections: [],
    tracks: [],
    harmony: [],
    markers: [],
    motifs: [],
    tempo_changes: [],
    time_signature_changes: [],
    key_changes: [],
  };
}

function asComposition(value) {
  if (value == null) {
    return emptyCompositionShape();
  }
  if (typeof value !== 'object' || Array.isArray(value)) {
    return null;
  }
  return value;
}

function listTracks(composition) {
  return Array.isArray(composition?.tracks) ? composition.tracks : [];
}

function listEvents(track) {
  return Array.isArray(track?.events) ? track.events : [];
}

function countList(value) {
  return Array.isArray(value) ? value.length : 0;
}

function expressiveTrackCount(track) {
  return (
    countList(track?.dynamic_marks)
    + countList(track?.sustain_pedals)
    + countList(track?.automation)
  );
}

function mergeBarsToRanges(bars) {
  if (!bars.length) {
    return [];
  }
  const sorted = [...new Set(bars)].sort((a, b) => a - b);
  const ranges = [];
  let start = sorted[0];
  let prev = sorted[0];
  for (let i = 1; i < sorted.length; i += 1) {
    const bar = sorted[i];
    if (bar === prev + 1) {
      prev = bar;
      continue;
    }
    ranges.push({ start_bar: start, end_bar: prev });
    start = prev = bar;
  }
  ranges.push({ start_bar: start, end_bar: prev });
  return ranges.slice(0, AFFECTED_RANGE_MAX);
}

function barsForTicks(composition, ticks) {
  if (!ticks.length || !composition) {
    return [];
  }
  const timeline = compileTimeline(composition);
  if (!timeline) {
    const tpq = Math.max(1, Number(composition.ticks_per_quarter) || 480);
    const barTicks = tpq * 4;
    const maxBar = Math.max(1, Number(composition.bar_count) || 1);
    return [...new Set(ticks.map((tick) => {
      const bar = Math.max(1, Math.floor(Math.max(0, tick) / barTicks) + 1);
      return Math.min(bar, maxBar);
    }))].sort((a, b) => a - b);
  }
  const bars = new Set();
  for (const tick of ticks) {
    const clamped = Math.min(Math.max(0, tick), Number(timeline.durationTicks) || 0);
    const bar = barAtTick(timeline, clamped);
    if (Number.isInteger(bar) && bar >= 1) {
      bars.add(bar);
    }
  }
  return [...bars].sort((a, b) => a - b);
}

function uniqueIdMap(events) {
  const counts = new Map();
  for (const event of events) {
    const id = event?.id;
    if (id == null || id === '') {
      continue;
    }
    const key = String(id);
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  const unique = new Map();
  for (const event of events) {
    const id = event?.id;
    if (id == null || id === '') {
      continue;
    }
    const key = String(id);
    if (counts.get(key) === 1) {
      unique.set(key, event);
    }
  }
  return unique;
}

function multisetQueue(events) {
  const map = new Map();
  for (const event of events) {
    const key = musicalEventKey(event);
    if (!map.has(key)) {
      map.set(key, []);
    }
    map.get(key).push(event);
  }
  return map;
}

/**
 * Compare events on one track pair.
 * @returns {{ added: number, removed: number, changed: number, identityChurn: number, changedTicks: number[] }}
 */
export function compareTrackEvents(sourceEvents, targetEvents) {
  const left = Array.isArray(sourceEvents) ? sourceEvents : [];
  const right = Array.isArray(targetEvents) ? targetEvents : [];
  const leftUnique = uniqueIdMap(left);
  const rightUnique = uniqueIdMap(right);
  const matchedLeft = new Set();
  const matchedRight = new Set();
  let changed = 0;
  let identityChurn = 0;
  const changedTicks = [];

  const pushTicks = (event) => {
    const start = Number(event?.start_tick) || 0;
    const duration = Number(event?.duration_ticks) || 0;
    changedTicks.push(start, start + duration);
  };

  for (const [id, leftEvent] of leftUnique.entries()) {
    const rightEvent = rightUnique.get(id);
    if (!rightEvent) {
      continue;
    }
    matchedLeft.add(leftEvent);
    matchedRight.add(rightEvent);
    if (musicalEventKey(leftEvent) !== musicalEventKey(rightEvent)) {
      changed += 1;
      pushTicks(leftEvent);
      pushTicks(rightEvent);
    }
  }

  const leftRemaining = left.filter((event) => !matchedLeft.has(event));
  const rightRemaining = right.filter((event) => !matchedRight.has(event));
  const rightQueue = multisetQueue(rightRemaining);

  let removed = 0;
  for (const leftEvent of leftRemaining) {
    const key = musicalEventKey(leftEvent);
    const bucket = rightQueue.get(key);
    if (bucket && bucket.length) {
      const rightEvent = bucket.shift();
      matchedRight.add(rightEvent);
      const leftId = leftEvent?.id;
      const rightId = rightEvent?.id;
      if (String(leftId ?? '') !== String(rightId ?? '')) {
        identityChurn += 1;
      }
      continue;
    }
    removed += 1;
    pushTicks(leftEvent);
  }

  let added = 0;
  for (const rightEvent of rightRemaining) {
    if (matchedRight.has(rightEvent)) {
      continue;
    }
    added += 1;
    pushTicks(rightEvent);
  }

  return { added, removed, changed, identityChurn, changedTicks };
}

function trackTopology(track) {
  return {
    id: track?.id ?? null,
    name: track?.name ?? null,
    instrument: track?.instrument ?? null,
    role: track?.role ?? null,
    midi_program: track?.midi_program ?? null,
    channel: track?.channel ?? null,
    is_drum: Boolean(track?.is_drum),
  };
}

function compareTrackTopology(sourceTrack, targetTrack) {
  if (!sourceTrack || !targetTrack) {
    return { renamed: false, reinstrumented: false };
  }
  const left = trackTopology(sourceTrack);
  const right = trackTopology(targetTrack);
  return {
    renamed: left.name !== right.name,
    reinstrumented: (
      left.instrument !== right.instrument
      || left.role !== right.role
      || left.midi_program !== right.midi_program
      || left.channel !== right.channel
      || left.is_drum !== right.is_drum
    ),
  };
}

function metadataChanged(source, target) {
  return (
    source.tempo !== target.tempo
    || source.key !== target.key
    || source.time_signature !== target.time_signature
    || source.bar_count !== target.bar_count
    || source.duration_ticks !== target.duration_ticks
    || source.ticks_per_quarter !== target.ticks_per_quarter
    || JSON.stringify(canonicalizeValue(source.sections || []))
      !== JSON.stringify(canonicalizeValue(target.sections || []))
    || JSON.stringify(canonicalizeValue(source.harmony || []))
      !== JSON.stringify(canonicalizeValue(target.harmony || []))
    || JSON.stringify(canonicalizeValue(source.markers || []))
      !== JSON.stringify(canonicalizeValue(target.markers || []))
    || JSON.stringify(canonicalizeValue(source.motifs || []))
      !== JSON.stringify(canonicalizeValue(target.motifs || []))
    || JSON.stringify(canonicalizeValue(source.tempo_changes || []))
      !== JSON.stringify(canonicalizeValue(target.tempo_changes || []))
    || JSON.stringify(canonicalizeValue(source.time_signature_changes || []))
      !== JSON.stringify(canonicalizeValue(target.time_signature_changes || []))
    || JSON.stringify(canonicalizeValue(source.key_changes || []))
      !== JSON.stringify(canonicalizeValue(target.key_changes || []))
  );
}

/**
 * Compare two compositions (or null empty snapshots).
 * @param {object|null|undefined} source
 * @param {object|null|undefined} target
 * @param {object} [context] optional provenance metadata (not stored on compositions)
 * @returns {object} bounded comparison summary
 */
export function compareCompositions(source, target, context = {}) {
  const left = asComposition(source);
  const right = asComposition(target);
  if (left === null || right === null) {
    return {
      identical: false,
      valid: false,
      code: 'invalid_composition',
      affected_ranges: [],
      affected_track_ids: [],
      tracks: {
        added: [],
        removed: [],
        reordered: false,
        renamed: [],
        reinstrumented: [],
      },
      events: {
        added: 0,
        removed: 0,
        changed: 0,
        identity_churn: 0,
        by_track: {},
      },
      timeline: {},
      counts: {},
      context: sanitizeContext(context),
    };
  }

  const sourceTracks = listTracks(left);
  const targetTracks = listTracks(right);
  const sourceById = new Map(sourceTracks.map((track) => [String(track.id), track]));
  const targetById = new Map(targetTracks.map((track) => [String(track.id), track]));
  const sourceOrder = sourceTracks.map((track) => String(track.id));
  const targetOrder = targetTracks.map((track) => String(track.id));

  const addedTrackIds = targetOrder.filter((id) => !sourceById.has(id));
  const removedTrackIds = sourceOrder.filter((id) => !targetById.has(id));
  const sharedIds = sourceOrder.filter((id) => targetById.has(id));
  const reordered = sharedIds.join('\0') !== targetOrder.filter((id) => sourceById.has(id)).join('\0');

  const renamed = [];
  const reinstrumented = [];
  const byTrack = {};
  let addedEvents = 0;
  let removedEvents = 0;
  let changedEvents = 0;
  let identityChurn = 0;
  const changedTicks = [];
  const affectedTrackIds = [];

  for (const id of sharedIds) {
    const topology = compareTrackTopology(sourceById.get(id), targetById.get(id));
    if (topology.renamed) {
      renamed.push(id);
    }
    if (topology.reinstrumented) {
      reinstrumented.push(id);
    }
    const eventDiff = compareTrackEvents(
      listEvents(sourceById.get(id)),
      listEvents(targetById.get(id)),
    );
    byTrack[id] = {
      source_event_count: listEvents(sourceById.get(id)).length,
      target_event_count: listEvents(targetById.get(id)).length,
      added: eventDiff.added,
      removed: eventDiff.removed,
      changed: eventDiff.changed,
      identity_churn: eventDiff.identityChurn,
    };
    addedEvents += eventDiff.added;
    removedEvents += eventDiff.removed;
    changedEvents += eventDiff.changed;
    identityChurn += eventDiff.identityChurn;
    changedTicks.push(...eventDiff.changedTicks);
    if (
      eventDiff.added
      || eventDiff.removed
      || eventDiff.changed
      || topology.renamed
      || topology.reinstrumented
    ) {
      affectedTrackIds.push(id);
    }
  }

  for (const id of addedTrackIds) {
    const events = listEvents(targetById.get(id));
    byTrack[id] = {
      source_event_count: 0,
      target_event_count: events.length,
      added: events.length,
      removed: 0,
      changed: 0,
      identity_churn: 0,
    };
    addedEvents += events.length;
    affectedTrackIds.push(id);
    for (const event of events) {
      const start = Number(event?.start_tick) || 0;
      changedTicks.push(start, start + (Number(event?.duration_ticks) || 0));
    }
  }
  for (const id of removedTrackIds) {
    const events = listEvents(sourceById.get(id));
    byTrack[id] = {
      source_event_count: events.length,
      target_event_count: 0,
      added: 0,
      removed: events.length,
      changed: 0,
      identity_churn: 0,
    };
    removedEvents += events.length;
    affectedTrackIds.push(id);
    for (const event of events) {
      const start = Number(event?.start_tick) || 0;
      changedTicks.push(start, start + (Number(event?.duration_ticks) || 0));
    }
  }

  const metaChanged = metadataChanged(left, right);
  const reference = (Number(right.bar_count) > 0 ? right : left);
  let affectedRanges;
  const nullToValue = source == null || target == null;
  const timelineMetaChanged = (
    left.bar_count !== right.bar_count
    || left.tempo !== right.tempo
    || left.key !== right.key
    || left.time_signature !== right.time_signature
    || left.duration_ticks !== right.duration_ticks
    || left.ticks_per_quarter !== right.ticks_per_quarter
  );
  const formMetaChanged = (
    JSON.stringify(canonicalizeValue(left.sections || []))
      !== JSON.stringify(canonicalizeValue(right.sections || []))
    || JSON.stringify(canonicalizeValue(left.harmony || []))
      !== JSON.stringify(canonicalizeValue(right.harmony || []))
    || JSON.stringify(canonicalizeValue(left.markers || []))
      !== JSON.stringify(canonicalizeValue(right.markers || []))
    || JSON.stringify(canonicalizeValue(left.motifs || []))
      !== JSON.stringify(canonicalizeValue(right.motifs || []))
  );
  if (nullToValue || timelineMetaChanged || formMetaChanged) {
    const barCount = Math.max(1, Number(reference.bar_count) || 1);
    affectedRanges = [{ start_bar: 1, end_bar: barCount }];
  } else {
    affectedRanges = mergeBarsToRanges(barsForTicks(reference, changedTicks));
  }

  if (metaChanged || addedTrackIds.length || removedTrackIds.length || reordered) {
    for (const id of [...sourceOrder, ...targetOrder]) {
      if (!affectedTrackIds.includes(id)) {
        affectedTrackIds.push(id);
      }
    }
  }

  const sourceExpressive = sourceTracks.reduce((sum, track) => sum + expressiveTrackCount(track), 0);
  const targetExpressive = targetTracks.reduce((sum, track) => sum + expressiveTrackCount(track), 0);

  const identical = (
    !metaChanged
    && !addedTrackIds.length
    && !removedTrackIds.length
    && !reordered
    && !renamed.length
    && !reinstrumented.length
    && addedEvents === 0
    && removedEvents === 0
    && changedEvents === 0
    && identityChurn === 0
    && sourceExpressive === targetExpressive
  );

  return {
    identical,
    valid: true,
    affected_ranges: affectedRanges,
    affected_track_ids: [...new Set(affectedTrackIds)].slice(0, AFFECTED_TRACK_MAX),
    tracks: {
      added: addedTrackIds,
      removed: removedTrackIds,
      reordered,
      renamed,
      reinstrumented,
    },
    events: {
      added: addedEvents,
      removed: removedEvents,
      changed: changedEvents,
      identity_churn: identityChurn,
      by_track: byTrack,
    },
    timeline: {
      source: {
        tempo: left.tempo ?? null,
        key: left.key ?? null,
        time_signature: left.time_signature ?? null,
        bar_count: Number(left.bar_count) || 0,
        duration_ticks: Number(left.duration_ticks) || 0,
        ticks_per_quarter: Number(left.ticks_per_quarter) || 0,
      },
      target: {
        tempo: right.tempo ?? null,
        key: right.key ?? null,
        time_signature: right.time_signature ?? null,
        bar_count: Number(right.bar_count) || 0,
        duration_ticks: Number(right.duration_ticks) || 0,
        ticks_per_quarter: Number(right.ticks_per_quarter) || 0,
      },
      changed: {
        tempo: left.tempo !== right.tempo,
        key: left.key !== right.key,
        time_signature: left.time_signature !== right.time_signature,
        bar_count: left.bar_count !== right.bar_count,
        duration_ticks: left.duration_ticks !== right.duration_ticks,
        ticks_per_quarter: left.ticks_per_quarter !== right.ticks_per_quarter,
      },
    },
    counts: {
      sections: { source: countList(left.sections), target: countList(right.sections) },
      harmony: { source: countList(left.harmony), target: countList(right.harmony) },
      markers: { source: countList(left.markers), target: countList(right.markers) },
      motifs: { source: countList(left.motifs), target: countList(right.motifs) },
      expressive: { source: sourceExpressive, target: targetExpressive },
    },
    context: sanitizeContext(context),
  };
}

function sanitizeContext(context) {
  if (!context || typeof context !== 'object') {
    return {};
  }
  return {
    operation_type: context.operation_type ?? null,
    ai_provider: context.ai_provider ?? null,
    ai_model: context.ai_model ?? null,
    has_user_instruction: Boolean(context.has_user_instruction),
    revision_name: context.revision_name ?? null,
    branch_name: context.branch_name ?? null,
    created_at: context.created_at ?? null,
    warning_codes: Array.isArray(context.warning_codes)
      ? context.warning_codes.map(String).slice(0, 32)
      : [],
  };
}
