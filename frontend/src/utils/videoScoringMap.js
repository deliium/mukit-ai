/**
 * Video time ↔ musical time. Pure twin of backend video_scoring_map.
 * Tempo comes from compositionTimeline. This file does not log.
 */

import {
  barAtTick,
  barStartTick,
  secondsToTick,
  tickToSeconds,
  totalDurationSeconds,
} from './compositionTimeline.js';

export const CLOSED_FRAME_RATES = Object.freeze([
  Object.freeze({ numerator: 24, denominator: 1, nominal: 24 }),
  Object.freeze({ numerator: 25, denominator: 1, nominal: 25 }),
  Object.freeze({ numerator: 30, denominator: 1, nominal: 30 }),
  Object.freeze({ numerator: 24000, denominator: 1001, nominal: 24 }),
  Object.freeze({ numerator: 30000, denominator: 1001, nominal: 30 }),
]);

const DROP_FRAME = Object.freeze({ numerator: 30000, denominator: 1001 });
const TIMECODE_RE = /^(\d{2}):([0-5]\d):([0-5]\d):(\d{2})$/;

export class VideoScoringMapError extends Error {
  constructor(code) {
    super(code);
    this.code = code;
  }
}

export function isClosedFrameRate(numerator, denominator) {
  return CLOSED_FRAME_RATES.some(
    (rate) => rate.numerator === numerator && rate.denominator === denominator,
  );
}

export function nominalFrames(numerator, denominator) {
  const match = CLOSED_FRAME_RATES.find(
    (rate) => rate.numerator === numerator && rate.denominator === denominator,
  );
  return match ? match.nominal : null;
}

export function scoreSecondsFromVideo(videoSeconds, {
  timeline,
  videoOriginSeconds = 0,
  musicalOriginTick = 0,
} = {}) {
  const origin = tickToSeconds(timeline, musicalOriginTick);
  if (origin == null) {
    return null;
  }
  return origin + (Number(videoSeconds) - Number(videoOriginSeconds));
}

export function videoSecondsFromScore(scoreSeconds, {
  timeline,
  videoOriginSeconds = 0,
  musicalOriginTick = 0,
} = {}) {
  const origin = tickToSeconds(timeline, musicalOriginTick);
  if (origin == null) {
    return null;
  }
  return Number(videoOriginSeconds) + (Number(scoreSeconds) - origin);
}

export function pictureLeaderScoreSeconds(videoSeconds, options) {
  return scoreSecondsFromVideo(videoSeconds, options);
}

export function toneSeekVideoSeconds(scoreSeconds, options) {
  return videoSecondsFromScore(scoreSeconds, options);
}

export function mapVideoToMusic(videoSeconds, options) {
  requireRate(options);
  const warnings = [];
  let video = Number(videoSeconds);
  const duration = Number(options.durationSeconds);
  if (video < 0 || video > duration) {
    video = Math.min(Math.max(video, 0), duration);
    warnings.push('video_time_clamped');
  }
  let score = scoreSecondsFromVideo(video, options);
  const clamped = clampScore(score, options.timeline);
  score = clamped.score;
  if (clamped.warning) {
    warnings.push(clamped.warning);
  }
  return finish({ video, score, warnings, options });
}

export function mapTickToVideo(tick, options) {
  requireRate(options);
  const warnings = [];
  const durationTicks = options.timeline.durationTicks;
  let clampedTick = Math.trunc(Number(tick));
  if (clampedTick < 0 || clampedTick > durationTicks) {
    clampedTick = Math.min(Math.max(clampedTick, 0), durationTicks);
    warnings.push('musical_time_clamped');
  }
  const score = tickToSeconds(options.timeline, clampedTick);
  let video = videoSecondsFromScore(score, options);
  const duration = Number(options.durationSeconds);
  if (video < 0 || video > duration) {
    video = Math.min(Math.max(video, 0), duration);
    warnings.push('video_time_clamped');
  }
  return finish({ video, score, warnings, options, forcedTick: clampedTick });
}

export function mapBarToVideo(bar, options) {
  const tick = barStartTick(options.timeline, bar);
  if (tick == null) {
    throw new VideoScoringMapError('video_map_selector_invalid');
  }
  return mapTickToVideo(tick, options);
}

export function formatTimecode(videoSeconds, {
  frameRateNumerator,
  frameRateDenominator,
  timecodeMode = 'non_drop',
  startTimecode = '00:00:00:00',
} = {}) {
  if (!isClosedFrameRate(frameRateNumerator, frameRateDenominator)) {
    throw new VideoScoringMapError('video_frame_rate_required');
  }
  if (
    timecodeMode === 'drop_frame'
    && (frameRateNumerator !== DROP_FRAME.numerator || frameRateDenominator !== DROP_FRAME.denominator)
  ) {
    throw new VideoScoringMapError('video_drop_frame_unsupported');
  }
  const nominal = nominalFrames(frameRateNumerator, frameRateDenominator);
  let framesFromZero = Math.floor(
    (Number(videoSeconds) * frameRateNumerator) / frameRateDenominator + 1e-9,
  );
  if (framesFromZero < 0) {
    framesFromZero = 0;
  }
  const display = framesFromZero + parseTimecode(startTimecode, {
    frameRateNumerator,
    frameRateDenominator,
    timecodeMode,
  });
  const parts = timecodeMode === 'drop_frame'
    ? framesToDropFrame(display)
    : framesToNondrop(display, nominal);
  return parts.map((part) => String(part).padStart(2, '0')).join(':');
}

export function parseTimecode(text, {
  frameRateNumerator,
  frameRateDenominator,
  timecodeMode = 'non_drop',
} = {}) {
  if (!isClosedFrameRate(frameRateNumerator, frameRateDenominator)) {
    throw new VideoScoringMapError('video_frame_rate_required');
  }
  const match = TIMECODE_RE.exec(text || '');
  if (!match) {
    throw new VideoScoringMapError('video_timecode_invalid');
  }
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  const seconds = Number(match[3]);
  const frames = Number(match[4]);
  const nominal = nominalFrames(frameRateNumerator, frameRateDenominator);
  if (hours > 23 || frames >= nominal) {
    throw new VideoScoringMapError('video_timecode_invalid');
  }
  if (timecodeMode === 'drop_frame') {
    if (frameRateNumerator !== DROP_FRAME.numerator || frameRateDenominator !== DROP_FRAME.denominator) {
      throw new VideoScoringMapError('video_drop_frame_unsupported');
    }
    const totalMinutes = hours * 60 + minutes;
    const nominalFramesCount = ((hours * 3600 + minutes * 60 + seconds) * 30) + frames;
    const dropped = 2 * (totalMinutes - Math.floor(totalMinutes / 10));
    return nominalFramesCount - dropped;
  }
  return ((hours * 3600 + minutes * 60 + seconds) * nominal) + frames;
}

export function hitPointOffMap(hitPoint, options) {
  const mapped = mapVideoToMusic(hitPoint.video_seconds, options);
  return Math.abs(Number(hitPoint.musical_tick) - mapped.tick) > 1;
}

export function cueRulerFraction(videoSeconds, durationSeconds) {
  const duration = Number(durationSeconds);
  const seconds = Number(videoSeconds);
  if (!Number.isFinite(duration) || duration <= 0 || !Number.isFinite(seconds) || seconds <= 0) {
    return 0;
  }
  if (seconds >= duration) {
    return 1;
  }
  return seconds / duration;
}

export function cueVideoSeconds(timecode, {
  frameRateNumerator,
  frameRateDenominator,
  timecodeMode = 'non_drop',
  startTimecode = '00:00:00:00',
} = {}) {
  const rate = { frameRateNumerator, frameRateDenominator, timecodeMode };
  const frames = parseTimecode(timecode, rate) - parseTimecode(startTimecode, rate);
  if (frames < 0) {
    throw new VideoScoringMapError('video_timecode_invalid');
  }
  return (frames * frameRateDenominator) / frameRateNumerator;
}

export function cueFrameIndex(cue, {
  frameRateNumerator,
  frameRateDenominator,
  timecodeMode = 'non_drop',
  startTimecode = '00:00:00:00',
} = {}) {
  if (!isClosedFrameRate(frameRateNumerator, frameRateDenominator)) {
    throw new VideoScoringMapError('video_frame_rate_required');
  }
  if (cue.timecode) {
    const rate = { frameRateNumerator, frameRateDenominator, timecodeMode };
    const frames = parseTimecode(cue.timecode, rate) - parseTimecode(startTimecode, rate);
    if (frames < 0) {
      throw new VideoScoringMapError('video_timecode_invalid');
    }
    return frames;
  }
  return Math.floor((Number(cue.video_seconds) * frameRateNumerator) / frameRateDenominator + 1e-9);
}

export function scoreSecondsOutsideComposition(videoSeconds, options) {
  const score = scoreSecondsFromVideo(videoSeconds, options);
  const total = totalDurationSeconds(options.timeline);
  if (score == null || total == null) {
    return false;
  }
  return score < 0 || score > total;
}

export function verifyCueLandings(cues, composition, options) {
  if (!isClosedFrameRate(options.frameRateNumerator, options.frameRateDenominator)) {
    throw new VideoScoringMapError('video_frame_rate_required');
  }
  const attacks = noteAttacks(composition, options);
  return (cues || []).map((cue) => landOne(cue, attacks, options));
}

function landOne(cue, attacks, options) {
  const cueFrame = cueFrameIndex(cue, options);
  if (attacks.length === 0) {
    return {
      id: cue.id,
      kind: cue.kind,
      timecode: cue.timecode ?? null,
      tolerance_frames: cue.tolerance_frames ?? 0,
      status: 'empty',
      delta_frames: null,
      match_count: 0,
      matches: [],
    };
  }
  const ranked = [...attacks].sort((left, right) => {
    const delta = Math.abs(left.eventFrame - cueFrame) - Math.abs(right.eventFrame - cueFrame);
    if (delta !== 0) return delta;
    if (left.startTick !== right.startTick) return left.startTick - right.startTick;
    return left.trackIndex - right.trackIndex;
  });
  const closest = ranked[0];
  const tolerance = cue.tolerance_frames ?? 0;
  const matched = ranked.filter(
    (attack) => attack.inPicture && Math.abs(attack.eventFrame - cueFrame) <= tolerance,
  );
  return {
    id: cue.id,
    kind: cue.kind,
    timecode: cue.timecode ?? null,
    tolerance_frames: tolerance,
    status: matched.length > 0 ? 'landed' : 'missed',
    delta_frames: closest.eventFrame - cueFrame,
    match_count: matched.length,
    matches: matched.slice(0, 8).map((attack) => ({
      track_index: attack.trackIndex,
      event_index: attack.eventIndex,
      start_tick: attack.startTick,
      pitch: attack.pitch,
    })),
  };
}

function noteAttacks(composition, options) {
  const attacks = [];
  const tracks = composition?.tracks || [];
  tracks.forEach((track, trackIndex) => {
    (track.events || []).forEach((event, eventIndex) => {
      if (event?.type !== 'note') return;
      const startTick = Number(event.start_tick);
      if (!Number.isFinite(startTick) || startTick < 0 || startTick > options.timeline.durationTicks) {
        return;
      }
      const score = tickToSeconds(options.timeline, startTick);
      const eventVideo = videoSecondsFromScore(score, options);
      if (eventVideo == null) return;
      const eventFrame = Math.floor(
        (eventVideo * options.frameRateNumerator) / options.frameRateDenominator + 1e-9,
      );
      attacks.push({
        trackIndex,
        eventIndex,
        startTick,
        pitch: event.pitch,
        eventVideo,
        eventFrame,
        inPicture: eventVideo >= 0 && eventVideo <= Number(options.durationSeconds),
      });
    });
  });
  return attacks;
}

function requireRate(options) {
  if (!isClosedFrameRate(options.frameRateNumerator, options.frameRateDenominator)) {
    throw new VideoScoringMapError('video_frame_rate_required');
  }
}

function clampScore(score, timeline) {
  const total = totalDurationSeconds(timeline);
  if (score == null || score < 0) {
    return { score: 0, warning: 'musical_time_clamped' };
  }
  if (total != null && score > total) {
    return { score: total, warning: 'musical_time_clamped' };
  }
  return { score, warning: null };
}

function finish({ video, score, warnings, options, forcedTick = null }) {
  let tick = forcedTick;
  if (tick == null) {
    const raw = secondsToTick(options.timeline, Math.max(0, score));
    tick = raw == null ? 0 : Math.trunc(raw + 0.5);
    if (tick < 0 || tick > options.timeline.durationTicks) {
      tick = Math.min(Math.max(tick, 0), options.timeline.durationTicks);
      if (!warnings.includes('musical_time_clamped')) {
        warnings.push('musical_time_clamped');
      }
    }
  }
  const bar = barAtTick(options.timeline, tick) || 1;
  const timecode = formatTimecode(video, {
    frameRateNumerator: options.frameRateNumerator,
    frameRateDenominator: options.frameRateDenominator,
    timecodeMode: options.timecodeMode,
    startTimecode: options.startTimecode,
  });
  return {
    videoSeconds: video,
    scoreSeconds: score,
    tick,
    bar,
    timecode,
    warnings,
  };
}

function framesToNondrop(frameCount, nominal) {
  const frames = frameCount % nominal;
  let rest = Math.floor(frameCount / nominal);
  const seconds = rest % 60;
  rest = Math.floor(rest / 60);
  const minutes = rest % 60;
  const hours = Math.floor(rest / 60);
  return [hours, minutes, seconds, frames];
}

function framesToDropFrame(frameCount) {
  const tenMin = Math.floor(frameCount / 17982);
  const remainder = frameCount % 17982;
  let minuteInChunk;
  let frameInMinute;
  if (remainder < 1800) {
    minuteInChunk = 0;
    frameInMinute = remainder;
  } else {
    const adjusted = remainder - 1800;
    minuteInChunk = 1 + Math.floor(adjusted / 1798);
    frameInMinute = (adjusted % 1798) + 2;
  }
  const totalMinutes = tenMin * 10 + minuteInChunk;
  return [
    Math.floor(totalMinutes / 60),
    totalMinutes % 60,
    Math.floor(frameInMinute / 30),
    frameInMinute % 30,
  ];
}
