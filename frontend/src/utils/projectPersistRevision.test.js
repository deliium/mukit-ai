import assert from 'node:assert/strict';
import test from 'node:test';

import { compositionRevisionKey } from './playbackPosition.js';
import { projectPersistRevisionKey } from './projectPersistRevision.js';

const BASE = {
  schema_version: 'composition.v1',
  tempo: 100,
  key: 'C major',
  time_signature: '4/4',
  ticks_per_quarter: 480,
  bar_count: 1,
  duration_ticks: 1920,
  sections: [
    { type: 'intro', start_bar: 1, bar_count: 1, start_tick: 0, duration_ticks: 1920 },
  ],
  tracks: [
    {
      id: 'piano-1',
      name: 'Piano',
      instrument: 'piano',
      role: 'harmony',
      midi_program: 0,
      channel: 1,
      volume: 100,
      events: [
        { type: 'note', id: 'n1', pitch: 'C4', start_tick: 0, duration_ticks: 480, velocity: 80 },
      ],
    },
  ],
  harmony: [{ bar: 1, chord: 'C' }],
};

test('persist revision changes when harmony changes but playback key stays stable', () => {
  const first = structuredClone(BASE);
  const second = structuredClone(BASE);
  second.harmony = [{ bar: 1, chord: 'G' }];

  assert.equal(compositionRevisionKey(first), compositionRevisionKey(second));
  assert.notEqual(projectPersistRevisionKey(first), projectPersistRevisionKey(second));
});

test('persist revision changes when key/sections/track metadata change', () => {
  const first = structuredClone(BASE);
  const byKey = structuredClone(BASE);
  byKey.key = 'G major';
  const bySections = structuredClone(BASE);
  bySections.sections = [
    { type: 'verse', start_bar: 1, bar_count: 1, start_tick: 0, duration_ticks: 1920 },
  ];
  const byTrackName = structuredClone(BASE);
  byTrackName.tracks[0].name = 'Grand Piano';

  assert.notEqual(projectPersistRevisionKey(first), projectPersistRevisionKey(byKey));
  assert.notEqual(projectPersistRevisionKey(first), projectPersistRevisionKey(bySections));
  assert.notEqual(projectPersistRevisionKey(first), projectPersistRevisionKey(byTrackName));
  assert.equal(compositionRevisionKey(first), compositionRevisionKey(byKey));
  assert.equal(compositionRevisionKey(first), compositionRevisionKey(byTrackName));
});

test('persist revision includes generationMeta without requiring event changes', () => {
  const composition = structuredClone(BASE);
  const withoutMeta = projectPersistRevisionKey(composition, null);
  const withMeta = projectPersistRevisionKey(composition, {
    provider: 'openai',
    model: 'gpt-4o-mini',
    prompt: { genre: 'ambient' },
  });
  const otherMeta = projectPersistRevisionKey(composition, {
    provider: 'deepseek',
    model: 'deepseek-chat',
    prompt: { genre: 'ambient' },
  });

  assert.notEqual(withoutMeta, withMeta);
  assert.notEqual(withMeta, otherMeta);
  assert.equal(compositionRevisionKey(composition), compositionRevisionKey(composition));
});

test('persist revision returns empty-shaped key for null composition', () => {
  const key = projectPersistRevisionKey(null, null);
  assert.equal(typeof key, 'string');
  assert.ok(key.length > 0);
  assert.equal(key, projectPersistRevisionKey(undefined, null));
});
