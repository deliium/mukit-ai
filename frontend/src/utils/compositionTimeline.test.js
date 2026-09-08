import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

import {
  activeKey,
  activeTempo,
  activeTimeSignature,
  barAtTick,
  barRangeTicks,
  compileTimeline,
  secondsToTick,
  tickToSeconds,
  totalDurationSeconds,
} from './compositionTimeline.js';

const fixturePath = join(dirname(fileURLToPath(import.meta.url)), 'fixtures', 'timeline_mixed_meter_tempo.json');

function loadFixture() {
  return JSON.parse(readFileSync(fixturePath, 'utf8'));
}

test('compileTimeline mixed meter/tempo matches golden expectations', () => {
  const raw = loadFixture();
  const expectations = raw.expectations;
  delete raw.expectations;
  const timeline = compileTimeline(raw);
  assert.ok(timeline);
  assert.deepEqual(timeline.barBoundaries, expectations.bar_boundaries);
  assert.equal(tickToSeconds(timeline, 1920), expectations.seconds_at_1920);
  assert.equal(tickToSeconds(timeline, 3360), expectations.seconds_at_3360);
  assert.equal(totalDurationSeconds(timeline), expectations.total_seconds);
  assert.equal(barAtTick(timeline, 2000), expectations.bar_at_2000);
  assert.equal(activeTempo(timeline, 2000), expectations.active_tempo_at_2000);
  assert.equal(activeTimeSignature(timeline, 2000), expectations.active_meter_at_2000);
  assert.equal(activeKey(timeline, 2000), expectations.active_key_at_2000);
  assert.equal(secondsToTick(timeline, 1.5), expectations.tick_at_1_5_seconds);
  assert.equal(secondsToTick(timeline, tickToSeconds(timeline, 1920)), 1920);
  assert.deepEqual(barRangeTicks(timeline, 2, 3), { startTick: 1920, endTick: 4800 });
});

test('compileTimeline rejects incomplete final bar duration', () => {
  const raw = loadFixture();
  delete raw.expectations;
  raw.duration_ticks = 4700;
  assert.equal(compileTimeline(raw), null);
});
