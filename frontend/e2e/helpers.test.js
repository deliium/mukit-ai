import assert from 'node:assert/strict';
import test from 'node:test';

import {
  compareNoteSequences,
  noteIdsCompatible,
  buildLargeScoreEditorFixture,
  largeScoreNoteCount,
  toCanonicalLargeScore,
} from './helpers.js';

test('noteIdsCompatible treats hydration-assigned ids as equal to null source ids', () => {
  assert.equal(noteIdsCompatible(null, null), true);
  assert.equal(noteIdsCompatible('n1', 'n1'), true);
  assert.equal(noteIdsCompatible(null, 'melody-1-n0'), true);
  assert.equal(noteIdsCompatible('', 'melody-1-n0'), true);
  assert.equal(noteIdsCompatible('n1', 'n2'), false);
  assert.equal(noteIdsCompatible('n1', null), false);
});

test('compareNoteSequences ignores hydration-only note id assignment', () => {
  const source = {
    'melody-1': [
      {
        id: null,
        pitch: 'C5',
        start_tick: 0,
        duration_ticks: 480,
        velocity: 80,
        staff: 1,
        voice: 1,
      },
    ],
  };
  const opened = {
    'melody-1': [
      {
        id: 'melody-1-n0',
        pitch: 'C5',
        start_tick: 0,
        duration_ticks: 480,
        velocity: 80,
        staff: 1,
        voice: 1,
      },
    ],
  };
  assert.equal(compareNoteSequences(source, opened), true);
  opened['melody-1'][0].pitch = 'D5';
  assert.equal(compareNoteSequences(source, opened), false);
});

test('buildLargeScoreEditorFixture creates a dense 100-bar V2 document', () => {
  const fixture = buildLargeScoreEditorFixture({ barCount: 100, notesPerBar: 4 });
  assert.equal(fixture.schema_version, 'composition.v2');
  assert.equal(fixture.bar_count, 100);
  assert.equal(fixture.duration_ticks, 100 * 1920);
  assert.equal(fixture.sections.length, 2);
  const noteCount = largeScoreNoteCount(fixture);
  assert.ok(noteCount >= 600);
  const canonical = toCanonicalLargeScore(fixture);
  assert.equal(canonical.schema_version, 'composition.v2');
  assert.equal(Object.prototype.hasOwnProperty.call(canonical, '__testNoteCount'), false);
});
