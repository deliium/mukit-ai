import assert from 'node:assert/strict';
import test from 'node:test';

import { migrateV1ToV2 } from './compositionVersion.js';
import { compositionRevisionKey } from './playbackPosition.js';
import { projectPersistRevisionKey } from './projectPersistRevision.js';
import { ensureCompositionNoteIds } from './pianoRollEvents.js';

const BASE = migrateV1ToV2({
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
});

test('persist revision changes when harmony changes', () => {
  const first = structuredClone(BASE);
  const second = structuredClone(BASE);
  second.harmony = [{ bar: 1, chord: 'G' }];

  assert.notEqual(projectPersistRevisionKey(first), projectPersistRevisionKey(second));
  assert.notEqual(compositionRevisionKey(first), compositionRevisionKey(second));
});

test('persist revision changes when V2 track expression changes', () => {
  const first = structuredClone(BASE);
  const second = structuredClone(BASE);
  second.tracks[0].expression = 90;

  assert.notEqual(projectPersistRevisionKey(first), projectPersistRevisionKey(second));
  assert.notEqual(compositionRevisionKey(first), compositionRevisionKey(second));
});

test('persist revision changes when key/sections/track metadata change', () => {
  const first = structuredClone(BASE);
  const byKey = structuredClone(BASE);
  byKey.key = 'G major';
  const bySections = structuredClone(BASE);
  bySections.sections = [
    { id: 'section-1', type: 'verse', label: null, start_bar: 1, bar_count: 1, start_tick: 0, duration_ticks: 1920 },
  ];
  const byTrackName = structuredClone(BASE);
  byTrackName.tracks[0].name = 'Grand Piano';

  assert.notEqual(projectPersistRevisionKey(first), projectPersistRevisionKey(byKey));
  assert.notEqual(projectPersistRevisionKey(first), projectPersistRevisionKey(bySections));
  assert.notEqual(projectPersistRevisionKey(first), projectPersistRevisionKey(byTrackName));
  assert.notEqual(compositionRevisionKey(first), compositionRevisionKey(byKey));
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
});

test('persist revision ignores hydration-only note id differences', () => {
  const withoutIds = structuredClone(BASE);
  withoutIds.tracks[0].events = [
    { type: 'note', pitch: 'C4', start_tick: 0, duration_ticks: 480, velocity: 80 },
  ];
  const hydrated = ensureCompositionNoteIds(withoutIds).composition;
  assert.equal(
    projectPersistRevisionKey(withoutIds),
    projectPersistRevisionKey(hydrated),
  );
});
