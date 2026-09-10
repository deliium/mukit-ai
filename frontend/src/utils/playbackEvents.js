/**
 * Compile deterministic Tone.js playback schedules from composition.v2 documents.
 * Preserves tick timing, tie collapse, articulation, expression, sustain, and piecewise tempo.
 * Logical notes and attack/release items carry stable noteId values so overlapping
 * same-pitch voices can release independently.
 */

import { createAppLogger } from './appLogger.js';
import {
  compileTimeline,
  roundHalfAwayFromZero,
  tickToSeconds,
  totalDurationSeconds,
} from './compositionTimeline.js';

const logger = createAppLogger('playbackEvents');

const DYNAMIC_LEVEL_TO_EXPRESSION = {
  ppp: 32,
  pp: 48,
  p: 64,
  mp: 80,
  mf: 96,
  f: 112,
  ff: 120,
  fff: 127,
};

const ARTICULATION_GATE_FACTOR = {
  staccato: 0.50,
  staccatissimo: 0.25,
  tenuto: 1.00,
  marcato: 0.75,
};

const ARTICULATION_VELOCITY_DELTA = {
  accent: 12,
  marcato: 20,
};

export function automationSampleInterval(ticksPerQuarter) {
  const tpq = Number(ticksPerQuarter) || 480;
  return Math.max(1, Math.floor(tpq / 16));
}

export function articulationGateTicks(notatedDurationTicks, articulations = []) {
  let factor = 1.0;
  for (const name of articulations) {
    if (Object.hasOwn(ARTICULATION_GATE_FACTOR, name)) {
      factor = Math.min(factor, ARTICULATION_GATE_FACTOR[name]);
    }
  }
  const gated = roundHalfAwayFromZero(notatedDurationTicks * factor);
  return Math.max(1, Math.min(notatedDurationTicks, gated));
}

export function articulationVelocity(baseVelocity, articulations = []) {
  let velocity = baseVelocity;
  for (const name of articulations) {
    velocity += ARTICULATION_VELOCITY_DELTA[name] || 0;
  }
  return Math.max(1, Math.min(127, velocity));
}

export function combinedExpression(laneValue, dynamicValue) {
  return Math.max(0, Math.min(127, roundHalfAwayFromZero((laneValue * dynamicValue) / 127)));
}

/**
 * @returns {import('./playbackEvents.js').PlaybackSchedule|null}
 */
export function compilePlaybackSchedule(musicJson) {
  if (!Array.isArray(musicJson?.tracks)) {
    logger.warn('Playback compile rejected because tracks is not an array', {
      schemaVersion: musicJson?.schema_version,
    });
    return null;
  }

  const timeline = compileTimeline(musicJson);
  if (!timeline) {
    logger.warn('Playback compile rejected because timeline is invalid', {
      schemaVersion: musicJson?.schema_version,
    });
    return null;
  }

  const durationSeconds = totalDurationSeconds(timeline);
  const items = [];
  const logicalNotes = [];
  const controllerSegments = [];
  let attackCount = 0;
  let releaseCount = 0;
  let controllerCount = 0;
  let skippedNotes = 0;
  let noteSeq = 0;

  musicJson.tracks.forEach((track, trackIndex) => {
    const trackMeta = extractTrackMeta(track, trackIndex);
    const sustainPedals = Array.isArray(track?.sustain_pedals) ? track.sustain_pedals : [];
    const compiledControllers = compileTrackControllers(track, timeline, trackMeta.trackId);
    compiledControllers.items.forEach((item) => {
      items.push({ ...item, ...trackMeta });
      controllerCount += 1;
    });
    compiledControllers.segments.forEach((segment) => {
      controllerSegments.push({ ...segment, ...trackMeta });
    });

    const collapsed = collapseTieChains(track);
    collapsed.forEach((note, eventIndex) => {
      const gateTicks = articulationGateTicks(note.duration_ticks, note.articulations);
      const velocityMidi = articulationVelocity(note.velocity, note.articulations);
      const noteEndTick = note.start_tick + gateTicks;
      const releaseTick = sustainReleaseTick(note.start_tick, noteEndTick, sustainPedals);

      const attackSeconds = tickToSeconds(timeline, note.start_tick);
      const releaseSeconds = tickToSeconds(timeline, releaseTick);
      if (attackSeconds == null || releaseSeconds == null || releaseSeconds <= attackSeconds) {
        skippedNotes += 1;
        logger.warn('Skipped malformed optional note timing', {
          trackId: trackMeta.trackId,
          startTick: note.start_tick,
          releaseTick,
        });
        return;
      }

      const expressionMidi = expressionAtTick(track, timeline, note.start_tick);
      const velocity = velocityMidi / 127;
      noteSeq += 1;
      const noteId = buildLogicalNoteId(trackMeta.trackId, noteSeq, note.start_tick, note.pitch);

      const noteItem = {
        ...trackMeta,
        noteId,
        pitch: note.pitch,
        notes: [note.pitch],
        startTick: note.start_tick,
        durationTicks: gateTicks,
        nominalEndTick: noteEndTick,
        releaseTick,
        velocityMidi,
        velocity,
        position: attackSeconds,
        duration: releaseSeconds - attackSeconds,
        stopPosition: releaseSeconds,
        expressionMidi,
        originalIndex: eventIndex,
      };

      logicalNotes.push(noteItem);
      items.push({
        kind: 'attack',
        noteId,
        time: attackSeconds,
        tick: note.start_tick,
        trackId: trackMeta.trackId,
        pitch: note.pitch,
        velocity,
        velocityMidi: noteItem.velocityMidi,
      });
      items.push({
        kind: 'release',
        noteId,
        time: releaseSeconds,
        tick: releaseTick,
        trackId: trackMeta.trackId,
        pitch: note.pitch,
      });
      attackCount += 1;
      releaseCount += 1;
    });
  });

  sortScheduleItems(items);
  const sortedLogicalNotes = sortLogicalNotes(logicalNotes);
  sortControllerSegments(controllerSegments);

  const schedule = {
    timeline,
    totalDurationSeconds: durationSeconds,
    items,
    logicalNotes: sortedLogicalNotes,
    controllerSegments,
    summary: {
      schemaVersion: musicJson.schema_version,
      trackCount: musicJson.tracks.length,
      attackCount,
      releaseCount,
      controllerCount,
      controllerSegmentCount: controllerSegments.length,
      logicalNoteCount: logicalNotes.length,
      skippedNotes,
      tempoSegmentCount: 1 + (Array.isArray(timeline?.tempoChanges) ? timeline.tempoChanges.length : 0),
      totalDurationSeconds: durationSeconds,
    },
  };

  logger.debug('Playback schedule compiled', schedule.summary);
  if (!logicalNotes.length) {
    logger.warn('Composition has no playable track events', {
      trackCount: musicJson.tracks.length,
    });
  }

  return schedule;
}

/**
 * Stable logical-note identity used by attack/release pairing.
 * @param {string} trackId
 * @param {number} seq
 * @param {number} startTick
 * @param {string} pitch
 * @returns {string}
 */
export function buildLogicalNoteId(trackId, seq, startTick, pitch) {
  return `${String(trackId)}#${Number(seq)}@${Number(startTick)}:${String(pitch || '')}`;
}

/**
 * Logical notes sounding at a half-open tick (attack <= tick < release).
 * @param {object} schedule
 * @param {number} tick
 * @returns {object[]}
 */
export function logicalNotesSoundingAtTick(schedule, tick) {
  const t = Number(tick);
  if (!schedule?.logicalNotes || !Number.isFinite(t)) {
    return [];
  }
  return schedule.logicalNotes.filter((note) => (
    Number(note.startTick) <= t && t < Number(note.releaseTick)
  ));
}

/**
 * Controller values at a tick for one track (volume / pan / expression).
 * Prefers bounded segments for linear ramps; falls back to step samples.
 * @param {object} schedule
 * @param {string} trackId
 * @param {number} tick
 * @returns {{ volume: number, pan: number, expression: number }}
 */
export function controllerStateAtTick(schedule, trackId, tick) {
  const id = String(trackId || '');
  const t = Number(tick);
  const defaults = { volume: 100, pan: 0, expression: 127 };
  if (!schedule || !Number.isFinite(t)) {
    return defaults;
  }

  const state = { ...defaults };
  const segments = (schedule.controllerSegments || []).filter((segment) => segment.trackId === id);
  ['volume', 'pan', 'expression'].forEach((parameter) => {
    const match = segments
      .filter((segment) => segment.parameter === parameter && segment.startTick <= t && t <= segment.endTick)
      .sort((left, right) => left.startTick - right.startTick)
      .at(-1);
    if (match) {
      state[parameter] = valueOnControllerSegment(match, t);
      return;
    }
    const samples = (schedule.items || [])
      .filter((item) => (
        item.kind === 'controller'
        && item.trackId === id
        && item.parameter === parameter
        && Number(item.tick) <= t
      ))
      .sort((left, right) => left.tick - right.tick);
    if (samples.length) {
      state[parameter] = samples[samples.length - 1].value;
    }
  });
  return state;
}

function valueOnControllerSegment(segment, tick) {
  if (segment.interpolation !== 'linear' || segment.endTick <= segment.startTick) {
    return segment.startValue;
  }
  const ratio = (tick - segment.startTick) / (segment.endTick - segment.startTick);
  const value = segment.startValue + ratio * (segment.endValue - segment.startValue);
  if (segment.parameter === 'pan') {
    return clampMidi(roundHalfAwayFromZero(value), -64, 63, 0);
  }
  return clampMidi(roundHalfAwayFromZero(value), 0, 127, segment.startValue);
}

/** Back-compat helper returning logical note events sorted by attack time. */
export function buildCanonicalPlaybackEvents(musicJson) {
  const schedule = compilePlaybackSchedule(musicJson);
  if (!schedule) {
    return [];
  }
  return schedule.logicalNotes.map((note) => stripOriginalIndex(note));
}

function extractTrackMeta(track, trackIndex) {
  return {
    trackId: String(track?.id ?? `track-${trackIndex}`),
    trackName: typeof track?.name === 'string' && track.name.trim() ? track.name.trim() : String(track?.id ?? `track-${trackIndex}`),
    instrument: typeof track?.instrument === 'string' ? track.instrument : '',
    role: typeof track?.role === 'string' ? track.role : '',
    midiProgram: normalizeMidiProgram(track?.midi_program),
    channel: normalizeChannel(track?.channel),
    isDrum: Boolean(track?.is_drum),
    trackVolume: normalizeTrackVolume(track?.volume),
    pan: normalizePan(track?.pan),
  };
}

function collapseTieChains(track) {
  const tieMembers = new Map();
  const standalone = [];

  if (!Array.isArray(track?.events)) {
    return [];
  }

  track.events.forEach((event) => {
    if (!isValidNoteEvent(event)) {
      return;
    }
    if (!event?.tie) {
      standalone.push(event);
      return;
    }
    const groupId = event.tie.group_id;
    if (!tieMembers.has(groupId)) {
      tieMembers.set(groupId, []);
    }
    tieMembers.get(groupId).push(event);
  });

  const collapsed = [];
  standalone.forEach((event) => {
    collapsed.push(normalizeCollapsedNote(event));
  });

  tieMembers.forEach((members) => {
    const ordered = [...members].sort((left, right) => {
      if (left.start_tick !== right.start_tick) {
        return left.start_tick - right.start_tick;
      }
      return left.duration_ticks - right.duration_ticks;
    });
    const head = ordered[0];
    collapsed.push({
      pitch: normalizePitch(head.pitch),
      start_tick: Number(head.start_tick),
      duration_ticks: ordered.reduce((sum, item) => sum + Number(item.duration_ticks), 0),
      velocity: Number(head.velocity),
      articulations: Array.isArray(head.articulations) ? head.articulations : [],
    });
  });

  return collapsed.sort((left, right) => {
    if (left.start_tick !== right.start_tick) {
      return left.start_tick - right.start_tick;
    }
    if (left.pitch !== right.pitch) {
      return String(left.pitch).localeCompare(String(right.pitch));
    }
    return left.duration_ticks - right.duration_ticks;
  });
}

function isValidNoteEvent(event) {
  const startTick = Number(event?.start_tick);
  const durationTicks = Number(event?.duration_ticks);
  const velocityMidi = Number(event?.velocity);
  const pitch = normalizePitch(event?.pitch);
  return Boolean(
    pitch
    && Number.isFinite(startTick)
    && startTick >= 0
    && Number.isFinite(durationTicks)
    && durationTicks > 0
    && Number.isFinite(velocityMidi)
    && velocityMidi >= 1
    && velocityMidi <= 127,
  );
}

function normalizeCollapsedNote(event) {
  return {
    pitch: normalizePitch(event.pitch),
    start_tick: Number(event.start_tick),
    duration_ticks: Number(event.duration_ticks),
    velocity: Number(event.velocity),
    articulations: Array.isArray(event.articulations) ? event.articulations : [],
  };
}

/**
 * Project sustain-pedal hold onto a nominal note-off.
 * Half-open pedal spans [start, end): a note-off at `end` is not held.
 * Notes attacked before pedal-down are held when their nominal off falls in the span.
 */
export function sustainReleaseTick(attackTick, noteEndTick, sustainPedals) {
  let releaseTick = noteEndTick;
  if (!Array.isArray(sustainPedals) || !sustainPedals.length) {
    return releaseTick;
  }
  for (const pedal of sustainPedals) {
    const start = Number(pedal?.start_tick);
    const duration = Number(pedal?.duration_ticks);
    const end = start + duration;
    if (!Number.isFinite(start) || !Number.isFinite(duration) || duration <= 0 || !Number.isFinite(end)) {
      continue;
    }
    const attackDuringPedal = attackTick >= start && attackTick < end;
    const noteOffDuringPedal = noteEndTick >= start && noteEndTick < end;
    if (attackDuringPedal || noteOffDuringPedal) {
      releaseTick = Math.max(releaseTick, end);
    }
  }
  return releaseTick;
}

function compileTrackControllers(track, timeline, trackId) {
  const interval = automationSampleInterval(timeline.ticksPerQuarter);
  const items = [];
  const segments = [];

  const volume = compileAutomationWithSegments({
    staticValue: normalizeTrackVolume(track?.volume),
    lane: findAutomationLane(track, 'volume'),
    interval,
    parameter: 'volume',
    timeline,
    clamp: (value) => clampMidi(value, 0, 127, 100),
  });
  const pan = compileAutomationWithSegments({
    staticValue: normalizePan(track?.pan),
    lane: findAutomationLane(track, 'pan'),
    interval,
    parameter: 'pan',
    timeline,
    clamp: (value) => clampMidi(value, -64, 63, 0),
  });
  const expressionLane = compileAutomationWithSegments({
    staticValue: clampMidi(track?.expression, 0, 127, 127),
    lane: findAutomationLane(track, 'expression'),
    interval,
    parameter: 'expression',
    timeline,
    clamp: (value) => clampMidi(value, 0, 127, 127),
  });
  const expressionEvents = compileDynamicExpressionEvents(track, expressionLane.points);
  const expressionSegments = buildStepSegmentsFromPoints(
    expressionEvents,
    'expression',
    timeline,
    (value) => clampMidi(value, 0, 127, 127),
  );

  volume.points.forEach(([tick, value]) => {
    items.push({
      kind: 'controller',
      parameter: 'volume',
      tick,
      time: tickToSeconds(timeline, tick),
      value: clampMidi(value, 0, 127, 100),
      interpolation: volume.interpolation,
    });
  });
  pan.points.forEach(([tick, value]) => {
    items.push({
      kind: 'controller',
      parameter: 'pan',
      tick,
      time: tickToSeconds(timeline, tick),
      value: clampMidi(value, -64, 63, 0),
      interpolation: pan.interpolation,
    });
  });
  expressionEvents.forEach(([tick, value]) => {
    items.push({
      kind: 'controller',
      parameter: 'expression',
      tick,
      time: tickToSeconds(timeline, tick),
      value: clampMidi(value, 0, 127, 127),
      interpolation: 'step',
    });
  });

  segments.push(...volume.segments, ...pan.segments, ...expressionSegments);

  return {
    trackId,
    items: items.filter((item) => item.time != null),
    segments: segments.filter((segment) => segment.startTime != null && segment.endTime != null),
  };
}

function compileAutomationWithSegments({
  staticValue,
  lane,
  interval,
  parameter,
  timeline,
  clamp,
}) {
  const interpolation = lane?.interpolation === 'linear' ? 'linear' : 'step';
  const points = compileAutomationLane({ staticValue, lane, interval });
  const segments = interpolation === 'linear' && lane?.points?.length
    ? buildLinearSegmentsFromLane(staticValue, lane, timeline, parameter, clamp)
    : buildStepSegmentsFromPoints(points, parameter, timeline, clamp);
  return { points, segments, interpolation };
}

function buildLinearSegmentsFromLane(staticValue, lane, timeline, parameter, clamp) {
  const points = [[0, staticValue], ...lane.points.map((point) => [Number(point.tick), Number(point.value)])]
    .filter(([tick, value]) => Number.isFinite(tick) && Number.isFinite(value))
    .sort((left, right) => left[0] - right[0]);
  const segments = [];
  for (let index = 0; index < points.length - 1; index += 1) {
    const [startTick, startValue] = points[index];
    const [endTick, endValue] = points[index + 1];
    if (endTick <= startTick) {
      continue;
    }
    segments.push({
      parameter,
      startTick,
      endTick,
      startValue: clamp(startValue),
      endValue: clamp(endValue),
      interpolation: 'linear',
      startTime: tickToSeconds(timeline, startTick),
      endTime: tickToSeconds(timeline, endTick),
    });
  }
  return segments;
}

function buildStepSegmentsFromPoints(points, parameter, timeline, clamp) {
  if (!points.length) {
    return [];
  }
  const segments = [];
  for (let index = 0; index < points.length; index += 1) {
    const [startTick, startValue] = points[index];
    const endTick = index < points.length - 1
      ? points[index + 1][0]
      : startTick;
    segments.push({
      parameter,
      startTick,
      endTick: Math.max(endTick, startTick),
      startValue: clamp(startValue),
      endValue: clamp(startValue),
      interpolation: 'step',
      startTime: tickToSeconds(timeline, startTick),
      endTime: tickToSeconds(timeline, Math.max(endTick, startTick)),
    });
  }
  return segments;
}

function sortControllerSegments(segments) {
  segments.sort((left, right) => {
    if (left.startTick !== right.startTick) {
      return left.startTick - right.startTick;
    }
    const trackCompare = String(left.trackId).localeCompare(String(right.trackId));
    if (trackCompare !== 0) {
      return trackCompare;
    }
    return String(left.parameter).localeCompare(String(right.parameter));
  });
}

function findAutomationLane(track, parameter) {
  return (track?.automation || []).find((lane) => lane?.parameter === parameter) || null;
}

function compileAutomationLane({ staticValue, lane, interval }) {
  if (!lane || !Array.isArray(lane.points) || !lane.points.length) {
    return dedupeConsecutiveValues([[0, staticValue]]);
  }

  const points = [[0, staticValue], ...lane.points.map((point) => [Number(point.tick), Number(point.value)])];
  if (lane.interpolation === 'step') {
    return dedupeConsecutiveValues(points);
  }

  const sampled = [];
  for (let index = 0; index < points.length - 1; index += 1) {
    const [startTick, startValue] = points[index];
    const [endTick, endValue] = points[index + 1];
    const segment = sampleLinearSegment(startTick, startValue, endTick, endValue, interval);
    if (index === 0) {
      sampled.push(...segment);
    } else {
      sampled.push(...segment.slice(1));
    }
  }
  return dedupeConsecutiveValues(sampled);
}

function sampleLinearSegment(startTick, startValue, endTick, endValue, interval) {
  if (endTick <= startTick) {
    return [[startTick, roundHalfAwayFromZero(startValue)]];
  }

  const samples = [[startTick, roundHalfAwayFromZero(startValue)]];
  let tick = startTick + interval;
  const span = endTick - startTick;
  while (tick < endTick) {
    const ratio = (tick - startTick) / span;
    const value = startValue + ratio * (endValue - startValue);
    samples.push([tick, roundHalfAwayFromZero(value)]);
    tick += interval;
  }
  samples.push([endTick, roundHalfAwayFromZero(endValue)]);
  return samples;
}

function compileDynamicExpressionEvents(track, expressionLaneEvents) {
  const dynamicPoints = [[0, 127]];
  for (const mark of track?.dynamic_marks || []) {
    const level = DYNAMIC_LEVEL_TO_EXPRESSION[mark.level];
    if (level != null) {
      dynamicPoints.push([Number(mark.tick), level]);
    }
  }
  dynamicPoints.sort((left, right) => left[0] - right[0]);

  const changeTicks = [...new Set([
    ...expressionLaneEvents.map(([tick]) => tick),
    ...dynamicPoints.map(([tick]) => tick),
  ])].sort((left, right) => left - right);

  const combined = changeTicks.map((tick) => {
    const laneValue = valueAtTick(expressionLaneEvents, tick, clampMidi(track?.expression, 0, 127, 127));
    const dynamicValue = valueAtTick(dynamicPoints, tick, 127);
    return [tick, combinedExpression(laneValue, dynamicValue)];
  });

  return dedupeConsecutiveValues(combined);
}

function expressionAtTick(track, timeline, tick) {
  const interval = automationSampleInterval(timeline.ticksPerQuarter);
  const expressionLaneEvents = compileAutomationLane({
    staticValue: clampMidi(track?.expression, 0, 127, 127),
    lane: findAutomationLane(track, 'expression'),
    interval,
  });
  const combined = compileDynamicExpressionEvents(track, expressionLaneEvents);
  return valueAtTick(combined, tick, clampMidi(track?.expression, 0, 127, 127));
}

function valueAtTick(points, tick, defaultValue) {
  let value = defaultValue;
  for (const [pointTick, pointValue] of points) {
    if (pointTick <= tick) {
      value = pointValue;
    } else {
      break;
    }
  }
  return value;
}

function dedupeConsecutiveValues(points) {
  if (!points.length) {
    return [];
  }
  const deduped = [points[0]];
  for (let index = 1; index < points.length; index += 1) {
    if (points[index][1] !== deduped[deduped.length - 1][1]) {
      deduped.push(points[index]);
    }
  }
  return deduped;
}

function sortLogicalNotes(notes) {
  return [...notes].sort((left, right) => {
    if (left.position !== right.position) {
      return left.position - right.position;
    }
    const trackCompare = String(left.trackId).localeCompare(String(right.trackId));
    if (trackCompare !== 0) {
      return trackCompare;
    }
    return left.originalIndex - right.originalIndex;
  }).map((note) => stripOriginalIndex(note));
}

function stripOriginalIndex(note) {
  const event = { ...note };
  delete event.originalIndex;
  return event;
}

const CONTROLLER_ORDER = { volume: 0, pan: 1, expression: 2 };
const ITEM_ORDER = { controller: 0, release: 1, attack: 2 };

function sortScheduleItems(items) {
  items.sort((left, right) => {
    if (left.time !== right.time) {
      return left.time - right.time;
    }
    const kindDelta = (ITEM_ORDER[left.kind] ?? 99) - (ITEM_ORDER[right.kind] ?? 99);
    if (kindDelta !== 0) {
      return kindDelta;
    }
    if (left.kind === 'controller' && right.kind === 'controller') {
      const paramDelta = (CONTROLLER_ORDER[left.parameter] ?? 99) - (CONTROLLER_ORDER[right.parameter] ?? 99);
      if (paramDelta !== 0) {
        return paramDelta;
      }
    }
    const trackCompare = String(left.trackId).localeCompare(String(right.trackId));
    if (trackCompare !== 0) {
      return trackCompare;
    }
    const noteIdCompare = String(left.noteId || '').localeCompare(String(right.noteId || ''));
    if (noteIdCompare !== 0) {
      return noteIdCompare;
    }
    return String(left.pitch || '').localeCompare(String(right.pitch || ''));
  });
}

function normalizePitch(pitch) {
  if (!pitch || typeof pitch !== 'string') {
    return '';
  }
  return pitch.trim();
}

function normalizeMidiProgram(value) {
  const program = Number(value);
  if (!Number.isInteger(program) || program < 0 || program > 127) {
    return 0;
  }
  return program;
}

function normalizeChannel(value) {
  const channel = Number(value);
  if (!Number.isInteger(channel) || channel < 1 || channel > 16) {
    return 1;
  }
  return channel;
}

function normalizeTrackVolume(value) {
  return clampMidi(value, 0, 127, 100);
}

function normalizePan(value) {
  return clampMidi(value, -64, 63, 0);
}

function clampMidi(value, min, max, fallback) {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return fallback;
  }
  return Math.max(min, Math.min(max, number));
}

export {
  DYNAMIC_LEVEL_TO_EXPRESSION,
  ARTICULATION_GATE_FACTOR,
  ARTICULATION_VELOCITY_DELTA,
};
