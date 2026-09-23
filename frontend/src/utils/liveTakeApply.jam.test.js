/**
 * Tests for multi-track AI Jam Commit (applyAiJamTakeToComposition).
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { JAM_COMMIT_MAP_INCOMPLETE } from './liveJamContracts.js';
import {
  applyAiJamTakeToComposition,
  defaultCommitHarmonySpans,
  mergeHarmonySpansFromPlannedWindow,
} from './liveTakeApply.js';
import { validateMusicJson } from './musicJsonValidation.js';

function minimalV2() {
  return {
    schema_version: 'composition.v2',
    tempo: 120,
    key: 'C major',
    time_signature: '4/4',
    ticks_per_quarter: 480,
    duration_ticks: 7680,
    bar_count: 4,
    sections: [{
      id: 's1',
      type: 'verse',
      start_bar: 1,
      bar_count: 4,
      start_tick: 0,
      duration_ticks: 7680,
    }],
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
      {
        id: 'pad-1',
        name: 'Pad',
        instrument: 'strings',
        role: 'harmony',
        midi_program: 48,
        channel: 3,
        expression: 127,
        sustain_pedals: [],
        events: [],
      },
    ],
    harmony: [],
  };
}

describe('applyAiJamTakeToComposition', () => {
  it('defaults harmony spans on for user_chords and off for user_melody', () => {
    assert.equal(defaultCommitHarmonySpans('user_chords'), true);
    assert.equal(defaultCommitHarmonySpans('user_melody'), false);
  });

  it('rejects incomplete role map when AI events exist', () => {
    const result = applyAiJamTakeToComposition(minimalV2(), {
      jamMode: 'user_melody',
      userTrackId: 'melody-1',
      roleTracks: {},
      streamNotes: [{ midi: 60, start_tick: 0, duration_ticks: 240, velocity: 80 }],
      accompanimentEvents: [
        { pitch: 36, start_tick: 0, duration_ticks: 480, velocity: 70, track_role: 'bass' },
      ],
      ensureMissingTracks: false,
    });
    assert.equal(result.ok, false);
    assert.equal(result.code, JAM_COMMIT_MAP_INCOMPLETE);
  });

  it('maps user + AI roles onto distinct tracks in one apply', () => {
    const result = applyAiJamTakeToComposition(minimalV2(), {
      jamMode: 'user_melody',
      userTrackId: 'melody-1',
      roleTracks: { bass: 'bass-1', accompaniment: 'pad-1' },
      streamNotes: [
        { midi: 72, start_tick: 0, duration_ticks: 240, velocity: 90 },
        { midi: 74, start_tick: 480, duration_ticks: 240, velocity: 88 },
      ],
      accompanimentEvents: [
        { pitch: 36, start_tick: 0, duration_ticks: 960, velocity: 70, track_role: 'bass' },
        { pitch: 60, start_tick: 0, duration_ticks: 960, velocity: 55, track_role: 'accompaniment' },
      ],
      ensureMissingTracks: false,
      commitHarmonySpans: false,
    });
    assert.equal(result.ok, true, result.message || result.code);
    assert.equal(result.userCount, 2);
    assert.equal(result.roleCounts.bass, 1);
    assert.equal(result.roleCounts.accompaniment, 1);

    const melody = result.composition.tracks.find((t) => t.id === 'melody-1');
    const bass = result.composition.tracks.find((t) => t.id === 'bass-1');
    const pad = result.composition.tracks.find((t) => t.id === 'pad-1');
    assert.equal(melody.events.length, 2);
    assert.equal(bass.events.length, 1);
    assert.equal(pad.events.length, 1);
    assert.equal(result.composition.harmony.length, 0);

    const validation = validateMusicJson(result.composition);
    assert.equal(validation.valid, true, validation.message);
  });

  it('ensures missing tracks when opted in', () => {
    const slim = minimalV2();
    slim.tracks = [slim.tracks[0]];
    const result = applyAiJamTakeToComposition(slim, {
      jamMode: 'user_melody',
      userTrackId: 'melody-1',
      roleTracks: {},
      streamNotes: [{ midi: 60, start_tick: 0, duration_ticks: 240, velocity: 80 }],
      accompanimentEvents: [
        { pitch: 36, start_tick: 0, duration_ticks: 480, velocity: 70, track_role: 'bass' },
      ],
      ensureMissingTracks: true,
      commitHarmonySpans: false,
    });
    assert.equal(result.ok, true, result.message || result.code);
    assert.ok(result.tracksEnsured.length >= 1);
    assert.ok(result.roleMap.bass);
    const bassTrack = result.composition.tracks.find((t) => t.id === result.roleMap.bass);
    assert.ok(bassTrack);
    assert.equal(bassTrack.events.length, 1);
    assert.equal(validateMusicJson(result.composition).valid, true);
  });

  it('writes planned_window into harmony metadata only for chords mode default', () => {
    const result = applyAiJamTakeToComposition(minimalV2(), {
      jamMode: 'user_chords',
      userTrackId: 'pad-1',
      roleTracks: { melody: 'melody-1', bass: 'bass-1', texture: 'pad-1' },
      streamNotes: [
        { midi: 60, start_tick: 0, duration_ticks: 480, velocity: 70 },
        { midi: 64, start_tick: 0, duration_ticks: 480, velocity: 70 },
        { midi: 67, start_tick: 0, duration_ticks: 480, velocity: 70 },
      ],
      accompanimentEvents: [
        { pitch: 72, start_tick: 480, duration_ticks: 240, velocity: 80, track_role: 'melody' },
      ],
      plannedWindow: [
        { start_tick: 0, end_tick: 1920, symbol: 'C', source: 'inferred' },
      ],
      // default commitHarmonySpans = true for user_chords
    });
    assert.equal(result.ok, true, result.message || result.code);
    assert.ok(result.harmonySpansCommitted >= 1);
    assert.equal(result.composition.harmony[0].chord, 'C');
    assert.equal(result.composition.harmony[0].start_tick, 0);
    // No notes invented solely from spans — melody track has generator notes only.
    const melody = result.composition.tracks.find((t) => t.id === 'melody-1');
    assert.equal(melody.events.length, 1);
    assert.equal(validateMusicJson(result.composition).valid, true);
  });
});

describe('mergeHarmonySpansFromPlannedWindow', () => {
  it('skips overlapping incoming spans and never invents notes', () => {
    const { spans, added } = mergeHarmonySpansFromPlannedWindow(
      [{ start_tick: 0, duration_ticks: 960, chord: 'G' }],
      [
        { start_tick: 480, end_tick: 1440, symbol: 'C' },
        { start_tick: 1920, end_tick: 2880, symbol: 'Am' },
      ],
      7680,
    );
    assert.equal(added, 1);
    assert.equal(spans.length, 2);
    assert.equal(spans[1].chord, 'Am');
  });
});
