import assert from 'node:assert/strict';
import test from 'node:test';

import { migrateV1ToV2 } from './compositionVersion.js';
import { isCanonicalComposition, validateMusicJson } from './musicJsonValidation.js';

test('validates canonical v1 composition JSON', () => {
  const result = validateMusicJson(canonicalV1Composition());
  assert.equal(result.valid, true);
  assert.equal(isCanonicalComposition(canonicalV1Composition()), false);
});

test('validates canonical v2 composition JSON', () => {
  const v2 = migrateV1ToV2(canonicalV1Composition());
  const result = validateMusicJson(v2);
  assert.equal(result.valid, true);
  assert.equal(isCanonicalComposition(v2), true);
});

test('rejects invalid canonical velocity', () => {
  const composition = migrateV1ToV2(canonicalV1Composition());
  composition.tracks[0].events[0].velocity = 0;

  const result = validateMusicJson(composition);

  assert.equal(result.valid, false);
  assert.match(result.message, /velocity/);
});

test('rejects overlapping sustain pedals on v2', () => {
  const composition = migrateV1ToV2(canonicalV1Composition());
  composition.tracks[0].sustain_pedals = [
    { start_tick: 0, duration_ticks: 960 },
    { start_tick: 480, duration_ticks: 480 },
  ];
  const result = validateMusicJson(composition);
  assert.equal(result.valid, false);
  assert.match(result.message, /non-overlapping/);
});

test('rejects unsupported schema versions', () => {
  const result = validateMusicJson({ schema_version: 'composition.v9', tempo: 100 });
  assert.equal(result.valid, false);
  assert.match(result.message, /Unsupported schema_version/);
});

test('accepts import-neutral other role and unsectioned section', () => {
  const composition = migrateV1ToV2(canonicalV1Composition());
  composition.sections[0].type = 'unsectioned';
  composition.tracks[0].role = 'other';
  const result = validateMusicJson(composition);
  assert.equal(result.valid, true);
});

test('accepts variable-meter V2 duration and section spans from compiled bar map', async () => {
  const { readFile } = await import('node:fs/promises');
  const { fileURLToPath } = await import('node:url');
  const path = await import('node:path');
  const fixturePath = path.join(
    path.dirname(fileURLToPath(import.meta.url)),
    'fixtures/timeline_mixed_meter_tempo.json',
  );
  const raw = JSON.parse(await readFile(fixturePath, 'utf8'));
  delete raw.expectations;
  for (const track of raw.tracks) {
    if (track.expression == null) {
      track.expression = 127;
    }
  }

  const result = validateMusicJson(raw);
  assert.equal(result.valid, true, result.message);

  // Same shape as the import multitrack fixture: 4/4 then 3/4 → 3360 ticks / 2 bars.
  const importLike = {
    schema_version: 'composition.v2',
    tempo: 100,
    key: 'C major',
    time_signature: '4/4',
    ticks_per_quarter: 480,
    duration_ticks: 3360,
    bar_count: 2,
    sections: [{
      id: 'section-1',
      type: 'unsectioned',
      label: null,
      start_bar: 1,
      bar_count: 2,
      start_tick: 0,
      duration_ticks: 3360,
    }],
    tracks: [{
      id: 'track-1',
      name: 'Piano',
      instrument: 'piano',
      role: 'other',
      midi_program: 0,
      channel: 1,
      expression: 127,
      events: [{ type: 'note', pitch: 'C4', start_tick: 0, duration_ticks: 480, velocity: 90 }],
      sustain_pedals: [],
    }],
    harmony: [],
    tempo_changes: [{ tick: 1920, bpm: 120 }],
    time_signature_changes: [{ tick: 1920, time_signature: '3/4' }],
    key_changes: [{ tick: 1920, key: 'G major' }],
    markers: [],
  };
  assert.equal(validateMusicJson(importLike).valid, true, validateMusicJson(importLike).message);
});

test('rejects unknown section type and track role', () => {
  const badSection = migrateV1ToV2(canonicalV1Composition());
  badSection.sections[0].type = 'coda_custom';
  assert.equal(validateMusicJson(badSection).valid, false);
  assert.match(validateMusicJson(badSection).message, /Unsupported section type/);

  const badRole = migrateV1ToV2(canonicalV1Composition());
  badRole.tracks[0].role = 'synth_lead_custom';
  assert.equal(validateMusicJson(badRole).valid, false);
  assert.match(validateMusicJson(badRole).message, /Unsupported track role/);
});

test('rejects legacy harmony-only JSON', () => {
  const result = validateMusicJson({
    tempo: 100,
    key: 'C major',
    time_signature: '4/4',
    sections: [{ type: 'intro', bars: 1 }],
    tracks: [{ instrument: 'piano', role: 'harmony' }],
    harmony: [{ bar: 1, chord: 'C' }],
    notes: [],
  });

  assert.equal(result.valid, false);
  assert.match(result.message, /no note events/);
});

test('accepts V2 with empty motifs default and valid motif references', () => {
  const composition = migrateV1ToV2(canonicalV1Composition());
  composition.tracks[0].events = [
    { type: 'note', pitch: 'C4', start_tick: 0, duration_ticks: 240, velocity: 90, id: 'n1' },
    { type: 'note', pitch: 'D4', start_tick: 240, duration_ticks: 240, velocity: 90, id: 'n2' },
    { type: 'note', pitch: 'E4', start_tick: 480, duration_ticks: 240, velocity: 90, id: 'n3' },
    { type: 'note', pitch: 'F4', start_tick: 720, duration_ticks: 240, velocity: 90, id: 'n4' },
  ];
  assert.equal(validateMusicJson(composition).valid, true);

  composition.motifs = [
    {
      id: 'motif-a',
      label: 'Motif A',
      occurrences: [
        {
          id: 'occ-orig',
          track_id: composition.tracks[0].id,
          event_ids: ['n1', 'n2', 'n3'],
          relationship: 'original',
        },
      ],
    },
  ];
  assert.equal(validateMusicJson(composition).valid, true, validateMusicJson(composition).message);
});

test('rejects dangling motif event references and note payloads', () => {
  const composition = migrateV1ToV2(canonicalV1Composition());
  composition.tracks[0].events = [
    { type: 'note', pitch: 'C4', start_tick: 0, duration_ticks: 240, velocity: 90, id: 'n1' },
    { type: 'note', pitch: 'D4', start_tick: 240, duration_ticks: 240, velocity: 90, id: 'n2' },
    { type: 'note', pitch: 'E4', start_tick: 480, duration_ticks: 240, velocity: 90, id: 'n3' },
  ];
  composition.motifs = [
    {
      id: 'motif-a',
      label: 'Motif A',
      occurrences: [
        {
          id: 'occ-orig',
          track_id: composition.tracks[0].id,
          event_ids: ['n1', 'n2', 'missing'],
          relationship: 'original',
        },
      ],
    },
  ];
  const dangling = validateMusicJson(composition);
  assert.equal(dangling.valid, false);
  assert.match(dangling.message, /unresolved event/i);

  composition.motifs[0].pitch = 'C4';
  composition.motifs[0].occurrences[0].event_ids = ['n1', 'n2', 'n3'];
  const payload = validateMusicJson(composition);
  assert.equal(payload.valid, false);
  assert.match(payload.message, /note payloads/i);
});

function canonicalV1Composition() {
  return {
    schema_version: 'composition.v1',
    tempo: 120,
    key: 'C major',
    time_signature: '4/4',
    ticks_per_quarter: 480,
    bar_count: 1,
    duration_ticks: 1920,
    sections: [{ type: 'intro', start_bar: 1, bar_count: 1, start_tick: 0, duration_ticks: 1920 }],
    tracks: [
      {
        id: 'piano-1',
        name: 'Piano',
        instrument: 'piano',
        role: 'harmony',
        midi_program: 0,
        channel: 1,
        events: [{ type: 'note', pitch: 'C4', start_tick: 0, duration_ticks: 480, velocity: 90 }],
      },
    ],
    harmony: [],
  };
}
