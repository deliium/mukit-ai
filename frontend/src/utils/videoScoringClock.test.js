import assert from 'node:assert/strict';
import test from 'node:test';

import { compileTimeline } from './compositionTimeline.js';
import {
  pictureLeaderScoreSeconds,
  toneSeekVideoSeconds,
} from './videoScoringMap.js';

const timeline = compileTimeline({
  ticks_per_quarter: 480,
  duration_ticks: 7680,
  bar_count: 4,
  tempo: 120,
  time_signature: '4/4',
  key: 'C major',
  tempo_changes: [{ tick: 3840, bpm: 60 }],
});

const origins = {
  timeline,
  videoOriginSeconds: 1,
  musicalOriginTick: 960,
};

test('picture time maps to score seconds and tone seek uses the inverse', () => {
  const scoreSeconds = pictureLeaderScoreSeconds(6, origins);
  const videoSeconds = toneSeekVideoSeconds(scoreSeconds, origins);
  assert.equal(videoSeconds, 6);
  const originSeconds = pictureLeaderScoreSeconds(1, origins);
  assert.ok(originSeconds > 0);
  assert.equal(toneSeekVideoSeconds(originSeconds, origins), 1);
});
