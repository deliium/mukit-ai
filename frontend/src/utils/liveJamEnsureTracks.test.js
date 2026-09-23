/**
 * Tests for ensureJamRoleTracks — valid V2 track creation for jam Commit.
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  defaultMidiProgramForJamRole,
  ensureJamRoleTracks,
  mapJamRoleToV2TrackRole,
} from './liveJamEnsureTracks.js';
import { validateMusicJson } from './musicJsonValidation.js';

describe('defaultMidiProgramForJamRole', () => {
  it('returns role defaults used by ensureJamRoleTracks', () => {
    assert.equal(defaultMidiProgramForJamRole('melody'), 0);
    assert.equal(defaultMidiProgramForJamRole('bass'), 32);
    assert.equal(defaultMidiProgramForJamRole('accompaniment'), 48);
    assert.equal(defaultMidiProgramForJamRole('texture'), 89);
    assert.equal(defaultMidiProgramForJamRole('unknown'), 0);
  });
});

function minimalV2(overrides = {}) {
  return {
    schema_version: 'composition.v2',
    tempo: 120,
    key: 'C major',
    time_signature: '4/4',
    ticks_per_quarter: 480,
    duration_ticks: 1920,
    bar_count: 1,
    sections: [{
      id: 's1',
      type: 'verse',
      start_bar: 1,
      bar_count: 1,
      start_tick: 0,
      duration_ticks: 1920,
    }],
    tracks: [{
      id: 'melody-1',
      name: 'Melody',
      instrument: 'piano',
      role: 'melody',
      midi_program: 0,
      channel: 1,
      expression: 127,
      sustain_pedals: [],
      events: [],
    }],
    harmony: [],
    ...overrides,
  };
}

describe('ensureJamRoleTracks', () => {
  it('maps jam roles onto supported V2 track roles', () => {
    assert.equal(mapJamRoleToV2TrackRole('melody'), 'melody');
    assert.equal(mapJamRoleToV2TrackRole('bass'), 'bass');
    assert.equal(mapJamRoleToV2TrackRole('harmony'), 'harmony');
    assert.equal(mapJamRoleToV2TrackRole('accompaniment'), 'harmony');
    assert.equal(mapJamRoleToV2TrackRole('texture'), 'pad');
  });

  it('creates valid empty tracks for missing roles and passes validation', () => {
    const base = minimalV2();
    const result = ensureJamRoleTracks(
      base,
      { bass: null, accompaniment: null, texture: null },
      {
        bass: { midi_program: 33 },
        accompaniment: { midi_program: 48 },
        texture: { midi_program: 89 },
      },
    );
    assert.equal(result.ok, true);
    assert.equal(result.tracksEnsured.length, 3);
    assert.ok(result.roleMap.bass);
    assert.ok(result.roleMap.accompaniment);
    assert.ok(result.roleMap.texture);

    const validation = validateMusicJson(result.composition);
    assert.equal(validation.valid, true, validation.message);

    const bass = result.composition.tracks.find((t) => t.id === result.roleMap.bass);
    assert.equal(bass.role, 'bass');
    assert.equal(bass.midi_program, 33);
    assert.deepEqual(bass.events, []);
    assert.ok(Number.isInteger(bass.channel) && bass.channel >= 1 && bass.channel <= 16);

    const accomp = result.composition.tracks.find((t) => t.id === result.roleMap.accompaniment);
    assert.equal(accomp.role, 'harmony');
    assert.deepEqual(accomp.events, []);

    const texture = result.composition.tracks.find((t) => t.id === result.roleMap.texture);
    assert.equal(texture.role, 'pad');
  });

  it('reuses existing mapped tracks without duplicating', () => {
    const base = minimalV2({
      tracks: [
        {
          id: 'melody-1',
          name: 'Melody',
          instrument: 'piano',
          role: 'melody',
          midi_program: 0,
          channel: 1,
          expression: 127,
          sustain_pedals: [],
          events: [],
        },
        {
          id: 'bass-1',
          name: 'Bass',
          instrument: 'bass',
          role: 'bass',
          midi_program: 32,
          channel: 2,
          expression: 127,
          sustain_pedals: [],
          events: [],
        },
      ],
    });
    const result = ensureJamRoleTracks(base, { bass: 'bass-1', accompaniment: null });
    assert.equal(result.ok, true);
    assert.equal(result.roleMap.bass, 'bass-1');
    assert.equal(result.tracksEnsured.length, 1);
    assert.equal(result.composition.tracks.length, 3);
  });

  it('rejects null composition', () => {
    const result = ensureJamRoleTracks(null, { bass: null });
    assert.equal(result.ok, false);
    assert.equal(result.code, 'jam_ensure_no_composition');
  });
});
