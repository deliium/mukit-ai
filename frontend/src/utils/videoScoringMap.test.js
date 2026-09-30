import assert from 'node:assert/strict';
import test from 'node:test';

import { compileTimeline, secondsToTick } from './compositionTimeline.js';
import {
  cueFrameIndex,
  cueRulerFraction,
  cueVideoSeconds,
  formatTimecode,
  mapTickToVideo,
  mapVideoToMusic,
  scoreSecondsFromVideo,
  verifyCueLandings,
  videoSecondsFromScore,
  VideoScoringMapError,
} from './videoScoringMap.js';

function timeline() {
  return compileTimeline({
    ticks_per_quarter: 480,
    duration_ticks: 7680,
    bar_count: 4,
    tempo: 120,
    time_signature: '4/4',
    key: 'C major',
    tempo_changes: [{ tick: 3840, bpm: 60 }],
  });
}

function options(compiled) {
  return {
    timeline: compiled,
    durationSeconds: 30,
    videoOriginSeconds: 0,
    musicalOriginTick: 0,
    frameRateNumerator: 24,
    frameRateDenominator: 1,
    timecodeMode: 'non_drop',
    startTimecode: '00:00:00:00',
  };
}

test('tempo change tick is not the constant-bpm tick', () => {
  const compiled = timeline();
  const mapped = mapVideoToMusic(6, options(compiled));
  const constantTick = 6 / (60 / 120 / 480);
  assert.notEqual(mapped.tick, Math.trunc(constantTick));
  assert.equal(mapped.tick, 4800);
  assert.equal(secondsToTick(compiled, mapped.scoreSeconds), 4800);
});

test('in-range video time round-trips within one tick', () => {
  const compiled = timeline();
  const forward = mapVideoToMusic(5.5, options(compiled));
  const backward = mapTickToVideo(forward.tick, options(compiled));
  const again = mapVideoToMusic(backward.videoSeconds, options(compiled));
  assert.ok(Math.abs(again.tick - forward.tick) <= 1);
  const score = scoreSecondsFromVideo(5.5, options(compiled));
  assert.equal(videoSecondsFromScore(score, options(compiled)), 5.5);
});

test('clamp warnings and timecode', () => {
  const compiled = timeline();
  const low = mapVideoToMusic(-1, options(compiled));
  assert.ok(low.warnings.includes('video_time_clamped'));
  const past = mapTickToVideo(99999, options(compiled));
  assert.ok(past.warnings.includes('musical_time_clamped'));
  assert.equal(formatTimecode(0, {
    frameRateNumerator: 24,
    frameRateDenominator: 1,
    timecodeMode: 'non_drop',
    startTimecode: '00:00:00:00',
  }), '00:00:00:00');
  const firstDropped = (1800 * 1001) / 30000;
  assert.equal(formatTimecode(firstDropped, {
    frameRateNumerator: 30000,
    frameRateDenominator: 1001,
    timecodeMode: 'drop_frame',
    startTimecode: '00:00:00:00',
  }), '00:01:00:02');
});

test('non-drop formatter does not use the drop-frame error', () => {
  assert.doesNotThrow(() => formatTimecode(1, {
    frameRateNumerator: 24,
    frameRateDenominator: 1,
    timecodeMode: 'non_drop',
    startTimecode: '00:00:00:00',
  }));
  assert.throws(
    () => mapVideoToMusic(1, { ...options(timeline()), frameRateNumerator: null }),
    (error) => error instanceof VideoScoringMapError && error.code === 'video_frame_rate_required',
  );
});

function longTimeline({ tempoChange = false } = {}) {
  const body = {
    ticks_per_quarter: 480,
    duration_ticks: 112 * 1920,
    bar_count: 112,
    tempo: 120,
    time_signature: '4/4',
    key: 'C major',
  };
  if (tempoChange) {
    body.bar_count = 83;
    body.duration_ticks = 83 * 1920;
    body.tempo_changes = [{ tick: 96000, bpm: 60 }];
  }
  return compileTimeline(body);
}

function roundTick(value) {
  return Math.trunc(value + 0.5);
}

function cue(overrides = {}) {
  return {
    id: 'hit_0123abcd',
    kind: 'hit_point',
    label: 'Door',
    timecode: '00:03:42:12',
    video_seconds: 0,
    musical_tick: 0,
    tolerance_frames: 0,
    ...overrides,
  };
}

test('timecode 00:03:42:12 is 222.5 seconds and lands only on the attack', () => {
  assert.equal(cueVideoSeconds('00:03:42:12', {
    frameRateNumerator: 24,
    frameRateDenominator: 1,
    timecodeMode: 'non_drop',
    startTimecode: '00:00:00:00',
  }), 222.5);
  const compiled = longTimeline();
  const attack = roundTick(secondsToTick(compiled, 222.5));
  const offset = roundTick(secondsToTick(compiled, 222.625));
  const rate = {
    timeline: compiled,
    durationSeconds: 400,
    videoOriginSeconds: 0,
    musicalOriginTick: 0,
    frameRateNumerator: 24,
    frameRateDenominator: 1,
    timecodeMode: 'non_drop',
    startTimecode: '00:00:00:00',
  };
  const exact = verifyCueLandings([cue()], {
    tracks: [{ events: [{ type: 'note', start_tick: attack, duration_ticks: 120, pitch: 'C4' }] }],
  }, rate);
  assert.equal(exact[0].status, 'landed');
  assert.equal(exact[0].matches[0].start_tick, attack);
  const missed = verifyCueLandings([cue({ tolerance_frames: 1 })], {
    tracks: [{ events: [{ type: 'note', start_tick: offset, duration_ticks: 120, pitch: 'C4' }] }],
  }, rate);
  const landed = verifyCueLandings([cue({ tolerance_frames: 3 })], {
    tracks: [{ events: [{ type: 'note', start_tick: offset, duration_ticks: 120, pitch: 'C4' }] }],
  }, rate);
  assert.equal(missed[0].status, 'missed');
  assert.equal(landed[0].status, 'landed');
  const early = roundTick(secondsToTick(compiled, 220));
  const sustain = verifyCueLandings([cue()], {
    tracks: [{
      events: [{
        type: 'note',
        start_tick: early,
        duration_ticks: attack - early + 480,
        pitch: 'C4',
      }],
    }],
  }, rate);
  assert.equal(sustain[0].status, 'missed');
  const legacy = cue({ timecode: null, video_seconds: 222.5 });
  assert.equal(cueFrameIndex(legacy, rate), 5340);
  const legacyLanding = verifyCueLandings([legacy], {
    tracks: [{ events: [{ type: 'note', start_tick: attack, duration_ticks: 120, pitch: 'C4' }] }],
  }, rate);
  assert.equal(legacyLanding[0].status, 'landed');
});

test('a cue past the picture sits at the end of the ruler', () => {
  assert.equal(cueRulerFraction(222.5, 2), 1);
  assert.equal(cueRulerFraction(1, 2), 0.5);
});

test('tempo change landing tick is secondsToTick of 222.5', () => {
  const compiled = longTimeline({ tempoChange: true });
  const expected = roundTick(secondsToTick(compiled, 222.5));
  const constant = roundTick(222.5 * (120 / 60) * compiled.ticksPerQuarter);
  assert.notEqual(expected, constant);
  const result = verifyCueLandings([cue()], {
    tracks: [{ events: [{ type: 'note', start_tick: expected, duration_ticks: 120, pitch: 'C4' }] }],
  }, {
    timeline: compiled,
    durationSeconds: 400,
    videoOriginSeconds: 0,
    musicalOriginTick: 0,
    frameRateNumerator: 24,
    frameRateDenominator: 1,
    timecodeMode: 'non_drop',
    startTimecode: '00:00:00:00',
  });
  assert.equal(result[0].status, 'landed');
  assert.equal(result[0].matches[0].start_tick, expected);
});
