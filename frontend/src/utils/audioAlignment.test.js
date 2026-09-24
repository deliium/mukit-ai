import assert from 'node:assert/strict';
import test from 'node:test';

import {
  TEMPO_DIVERGENCE_EPSILON_BPM,
  alignmentMapOpts,
  barRangeToAudioWindow,
  buildAlignmentFromScaffolding,
  sourceSecondsToTick,
  tickToSourceSeconds,
} from './audioAlignment.js';
import { compileTimeline } from './compositionTimeline.js';
import { AUDIO_ALIGNMENT_ISSUE_CODES } from './audioAlignmentContracts.js';

function sixteenBar120() {
  return {
    schema_version: 'composition.v2',
    tempo: 120,
    key: 'C major',
    time_signature: '4/4',
    ticks_per_quarter: 480,
    bar_count: 16,
    duration_ticks: 16 * 1920,
    sections: [
      {
        type: 'verse',
        start_bar: 1,
        bar_count: 16,
        start_tick: 0,
        duration_ticks: 16 * 1920,
      },
    ],
    tracks: [
      {
        id: 'piano-1',
        name: 'Piano',
        instrument: 'piano',
        role: 'harmony',
        midi_program: 0,
        channel: 1,
        events: [],
      },
    ],
    harmony: [],
    tempo_changes: [],
    time_signature_changes: [],
    key_changes: [],
    markers: [],
  };
}

function scaffolding(overrides = {}) {
  return {
    tempo_bpm: 120,
    tempo_confidence: 0.85,
    tempo_source: 'estimated',
    meter: '4/4',
    beat_grid: {
      downbeat_offset_seconds: 0.5,
      ticks_per_quarter: 480,
      confidence: 0.8,
    },
    ...overrides,
  };
}

test('golden: bar 10 @ 120 BPM / offset 0.5s → 18.5s', () => {
  const timeline = compileTimeline(sixteenBar120());
  assert.equal(timeline.barBoundaries[9], 17280);
  const start = tickToSourceSeconds(timeline, 17280, {
    downbeatOffsetSeconds: 0.5,
    originTick: 0,
  });
  assert.ok(Math.abs(start - 18.5) < 1e-6);
  const end = tickToSourceSeconds(timeline, timeline.barBoundaries[10], {
    downbeatOffsetSeconds: 0.5,
  });
  assert.ok(Math.abs(end - 20.5) < 1e-6);
  const back = sourceSecondsToTick(timeline, 18.5, { downbeatOffsetSeconds: 0.5 });
  assert.equal(back, 17280);
});

test('barRangeToAudioWindow bars 9–12', () => {
  const composition = sixteenBar120();
  const alignment = buildAlignmentFromScaffolding({
    composition,
    scaffolding: scaffolding(),
    sourceAudioAssetId: 'src_golden01',
    compositionFingerprint: 'snap_golden_fp01',
  });
  const window = barRangeToAudioWindow(9, 12, composition, alignment);
  assert.ok(window);
  assert.ok(Math.abs(window.startSeconds - 16.5) < 1e-6);
  assert.ok(Math.abs(window.endSeconds - 24.5) < 1e-6);
  assert.equal(window.startTick, 15360);
  assert.equal(window.endTick, 23040);
});

test('buildAlignmentFromScaffolding flags tempo divergence', () => {
  const composition = { ...sixteenBar120(), tempo: 140 };
  const alignment = buildAlignmentFromScaffolding({
    composition,
    scaffolding: scaffolding({ tempo_bpm: 120 }),
    sourceAudioAssetId: 'src_div01',
    compositionFingerprint: 'snap_div_fp01xx',
  });
  assert.ok(
    alignment.quality.issues.includes(
      AUDIO_ALIGNMENT_ISSUE_CODES.ALIGNMENT_TEMPO_DIVERGED,
    ),
  );
  assert.ok(alignment.quality.overall_confidence <= 0.35);
  assert.ok(Math.abs(140 - 120) > TEMPO_DIVERGENCE_EPSILON_BPM);
});

test('alignmentMapOpts reads snake_case map', () => {
  const opts = alignmentMapOpts({
    map: { downbeat_offset_seconds: 0.5, origin_tick: 0 },
  });
  assert.equal(opts.downbeatOffsetSeconds, 0.5);
  assert.equal(opts.originTick, 0);
});
