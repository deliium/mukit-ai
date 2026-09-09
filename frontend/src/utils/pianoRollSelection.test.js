import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

import {
  MOTIF_BAR_SPAN_EXCEEDED_CODE,
  defaultTargetTrackIds,
  motifDestinationTickRange,
  motifSourceTickRange,
  normalizeBarRange,
  pointerXToBar,
  selectedTickBoundaries,
  validateMotifBarRange,
} from './pianoRollSelection.js';

const PANEL_SOURCE = readFileSync(
  path.join(path.dirname(fileURLToPath(import.meta.url)), '../components/AiRegionEditPanel.jsx'),
  'utf8',
);

test('normalizeBarRange clamps and orders bars', () => {
  assert.deepEqual(normalizeBarRange(12, 9, 16), { startBar: 9, endBar: 12 });
  assert.deepEqual(normalizeBarRange(0, 20, 16), { startBar: 1, endBar: 16 });
});

test('pointerXToBar maps pixels to inclusive bars', () => {
  const mapped = pointerXToBar(1920 * 0.05 * 8 + 1, {
    pixelsPerTick: 0.05,
    barTicks: 1920,
    barCount: 16,
  });
  assert.equal(mapped.bar, 9);
});

test('selectedTickBoundaries returns inclusive bar tick window', () => {
  const bounds = selectedTickBoundaries(9, 12, {
    timeSignature: '4/4',
    ticksPerQuarter: 480,
    durationTicks: 16 * 1920,
  });
  assert.equal(bounds.startTick, 8 * 1920);
  assert.equal(bounds.endTick, 12 * 1920);
});

test('defaultTargetTrackIds prefers current track then falls back', () => {
  const composition = {
    tracks: [{ id: 'melody-1' }, { id: 'bass-1' }],
  };
  assert.deepEqual(
    defaultTargetTrackIds(composition, { mode: 'current', currentTrackId: 'bass-1' }),
    ['bass-1'],
  );
  assert.deepEqual(
    defaultTargetTrackIds(composition, { mode: 'all', currentTrackId: 'bass-1' }),
    ['melody-1', 'bass-1'],
  );
});

test('validateMotifBarRange rejects spans wider than two bars', () => {
  assert.equal(validateMotifBarRange(1, 2, 16).valid, true);
  assert.equal(validateMotifBarRange(1, 3, 16).valid, false);
  assert.equal(validateMotifBarRange(1, 3, 16).code, MOTIF_BAR_SPAN_EXCEEDED_CODE);
});

test('motifSourceTickRange uses compiled timeline and rejects truncation', () => {
  const composition = {
    schema_version: 'composition.v2',
    tempo: 100,
    key: 'C major',
    time_signature: '4/4',
    ticks_per_quarter: 480,
    bar_count: 8,
    duration_ticks: 15360,
    sections: [{ type: 'intro', start_bar: 1, bar_count: 8, start_tick: 0, duration_ticks: 15360 }],
    tracks: [{ id: 'melody-1', events: [] }],
    tempo_changes: [],
    time_signature_changes: [],
    key_changes: [],
  };
  const valid = motifSourceTickRange(2, 3, { composition });
  assert.equal(valid.valid, true);
  assert.equal(valid.startTick, 1920);
  assert.equal(valid.endTick, 5760);
  assert.equal(valid.barSpan, 2);

  const invalid = motifSourceTickRange(1, 4, { composition });
  assert.equal(invalid.valid, false);
  assert.equal(invalid.code, MOTIF_BAR_SPAN_EXCEEDED_CODE);
  assert.equal(invalid.startTick, null);
});

test('motifDestinationTickRange validates destination span', () => {
  const composition = {
    schema_version: 'composition.v2',
    tempo: 100,
    key: 'C major',
    time_signature: '4/4',
    ticks_per_quarter: 480,
    bar_count: 4,
    duration_ticks: 7680,
    sections: [{ type: 'intro', start_bar: 1, bar_count: 4, start_tick: 0, duration_ticks: 7680 }],
    tracks: [{ id: 'melody-1', events: [] }],
    tempo_changes: [],
    time_signature_changes: [],
    key_changes: [],
  };
  const placement = motifDestinationTickRange(3, { composition, barSpan: 2 });
  assert.equal(placement.valid, true);
  assert.equal(placement.startBar, 3);
  assert.equal(placement.endBar, 4);
  assert.equal(placement.startTick, 3840);
  assert.equal(placement.endTick, 7680);
});

test('AiRegionEditPanel keeps defaultTargetTrackIds import for empty selection fallback', () => {
  assert.match(
    PANEL_SOURCE,
    /import\s*\{\s*defaultTargetTrackIds\s*\}\s*from\s*['"]\.\.\/utils\/pianoRollSelection\.js['"]/,
    'AiRegionEditPanel must import defaultTargetTrackIds (regression for dropped V2 import)',
  );
  assert.match(
    PANEL_SOURCE,
    /defaultTargetTrackIds\s*\(\s*editedMusicJson/,
    'AiRegionEditPanel must call defaultTargetTrackIds when aiEditTrackIds is empty',
  );
  assert.match(
    PANEL_SOURCE,
    /import\s*\{\s*countV2FeatureSummary\s*\}\s*from\s*['"]\.\.\/utils\/pianoRollEvents\.js['"]/,
    'V2 feature summary import must remain alongside defaultTargetTrackIds',
  );
});
