import assert from 'node:assert/strict';
import test from 'node:test';

import { compileTimeline, secondsToTick } from './compositionTimeline.js';
import {
  formatTimecode,
  mapTickToVideo,
  mapVideoToMusic,
  scoreSecondsFromVideo,
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
