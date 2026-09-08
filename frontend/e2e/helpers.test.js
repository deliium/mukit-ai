import assert from 'node:assert/strict';
import test from 'node:test';

import {
  compareNoteSequences,
  noteIdsCompatible,
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
