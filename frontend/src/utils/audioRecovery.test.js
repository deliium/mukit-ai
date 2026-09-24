/**
 * Audio recovery FE helpers — gates, overlay, ensure tracks, phase exclusion.
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  ensureRecoveryRoleTracks,
  mapRecoveryStemToV2TrackRole,
} from './audioRecoveryEnsureTracks.js';
import {
  applyAudioRecoveryToComposition,
} from './audioRecoveryApply.js';
import {
  defaultSelectedRecoveryProvisionalIds,
  selectRecoveryNotesForApply,
} from './audioRecoveryGates.js';
import {
  buildOverlayFromEventMap,
  buildRecoveryBoundConfidenceGeoms,
  buildRecoveryProvisionalGeoms,
  markOverlayUserEdited,
  pruneOverlayForComposition,
  syncOverlayAfterCompositionEdit,
} from './audioRecoveryOverlay.js';
import {
  assertRecoveryAllowedForOtherPhases,
  assertMonoAudioAllowedForRecoveryPhase,
} from './audioRecoveryPhaseGuards.js';

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
    tracks: [
      {
        id: 't1',
        name: 'Melody',
        instrument: 'piano',
        role: 'melody',
        midi_program: 0,
        channel: 1,
        expression: 127,
        sustain_pedals: [],
        events: [],
      },
    ],
    markers: [],
    harmony: [],
    ...overrides,
  };
}

const baseComposition = minimalV2();

describe('audioRecoveryEnsureTracks', () => {
  it('maps product stems to supported V2 roles', () => {
    assert.equal(mapRecoveryStemToV2TrackRole('harmonic'), 'harmony');
    assert.equal(mapRecoveryStemToV2TrackRole('vocals'), 'lead');
    assert.equal(mapRecoveryStemToV2TrackRole('drums'), 'drums');
  });

  it('creates missing stem tracks without inventing notes', () => {
    const result = ensureRecoveryRoleTracks(
      baseComposition,
      { melody: 't1', bass: null },
      {},
      { ensure_missing_tracks: true },
    );
    assert.equal(result.ok, true);
    assert.ok(result.stemRoleMap.bass);
    const bass = result.composition.tracks.find((t) => t.id === result.stemRoleMap.bass);
    assert.ok(bass);
    assert.equal(bass.events.length, 0);
  });
});

describe('audioRecoveryGates', () => {
  const preview = {
    notes: [
      { provisional_id: 'a', confidence: 0.9, pitch: 60, start_tick: 0, duration_ticks: 120, velocity: 80, stem: 'melody' },
      { provisional_id: 'b', confidence: 0.2, pitch: 62, start_tick: 120, duration_ticks: 120, velocity: 80, stem: 'melody' },
    ],
    summary: { include_threshold: 0.5 },
  };

  it('defaults selection to above-threshold notes only', () => {
    const ids = defaultSelectedRecoveryProvisionalIds(preview, 0.5);
    assert.deepEqual(ids, ['a']);
  });

  it('excludes low confidence unless opted in', () => {
    const gated = selectRecoveryNotesForApply(preview, { includeLowConfidence: false });
    assert.equal(gated.notes.length, 1);
    assert.equal(gated.excludedLow, 1);
  });
});

describe('audioRecoveryOverlay', () => {
  it('builds overlay from event map and prunes deleted ids', () => {
    const overlay = buildOverlayFromEventMap(
      [{ provisional_id: 'a', event_id: 'e1', track_id: 't1' }],
      [{ provisional_id: 'a', confidence: 0.8, stem: 'melody' }],
    );
    assert.equal(overlay.length, 1);
    assert.equal(overlay[0].status, 'recovered');
    const pruned = pruneOverlayForComposition(overlay, {
      tracks: [{ id: 't1', events: [] }],
    });
    assert.equal(pruned.prunedCount, 1);
    assert.equal(pruned.overlay.length, 0);
  });

  it('marks user_edited on pitch/time change', () => {
    const overlay = [
      { event_id: 'e1', confidence: 0.7, stem: 'melody', status: 'recovered' },
    ];
    const marked = markOverlayUserEdited(overlay, [{ eventId: 'e1', pitchChanged: true }]);
    assert.equal(marked.markedCount, 1);
    assert.equal(marked.overlay[0].status, 'user_edited');
  });

  it('syncs prune + user_edited after composition edit', () => {
    const overlay = [
      { event_id: 'e1', confidence: 0.9, stem: 'melody', status: 'recovered' },
      { event_id: 'e2', confidence: 0.4, stem: 'bass', status: 'recovered' },
    ];
    const prev = minimalV2({
      tracks: [{
        id: 't1',
        name: 'Melody',
        instrument: 'piano',
        role: 'melody',
        midi_program: 0,
        channel: 1,
        expression: 127,
        sustain_pedals: [],
        events: [
          { id: 'e1', pitch: 'C4', start_tick: 0, duration_ticks: 120, velocity: 80 },
          { id: 'e2', pitch: 'C3', start_tick: 0, duration_ticks: 240, velocity: 80 },
        ],
      }],
    });
    const next = minimalV2({
      tracks: [{
        id: 't1',
        name: 'Melody',
        instrument: 'piano',
        role: 'melody',
        midi_program: 0,
        channel: 1,
        expression: 127,
        sustain_pedals: [],
        events: [
          { id: 'e1', pitch: 'D4', start_tick: 0, duration_ticks: 120, velocity: 80 },
        ],
      }],
    });
    const synced = syncOverlayAfterCompositionEdit(overlay, prev, next);
    assert.equal(synced.prunedCount, 1);
    assert.equal(synced.markedCount, 1);
    assert.equal(synced.overlay.length, 1);
    assert.equal(synced.overlay[0].event_id, 'e1');
    assert.equal(synced.overlay[0].status, 'user_edited');
  });

  it('builds multi-stem provisional and bound confidence geoms', () => {
    const provisional = buildRecoveryProvisionalGeoms(
      [
        {
          provisional_id: 'p1',
          pitch: 60,
          start_tick: 0,
          duration_ticks: 120,
          confidence: 0.9,
          stem: 'melody',
        },
        {
          provisional_id: 'p2',
          pitch: 48,
          start_tick: 0,
          duration_ticks: 120,
          confidence: 0.2,
          stem: 'bass',
        },
      ],
      ['p1', 'p2'],
      {
        pixelsPerTick: 1,
        pitchMidiMax: 72,
        pitchMidiMin: 36,
        rowHeight: 10,
        threshold: 0.5,
      },
    );
    assert.equal(provisional.length, 2);
    assert.equal(provisional[0].stem, 'melody');
    assert.equal(provisional[1].low, true);

    const bound = buildRecoveryBoundConfidenceGeoms(
      minimalV2({
        tracks: [{
          id: 't1',
          name: 'Melody',
          instrument: 'piano',
          role: 'melody',
          midi_program: 0,
          channel: 1,
          expression: 127,
          sustain_pedals: [],
          events: [
            { id: 'e1', pitch: 'C4', start_tick: 0, duration_ticks: 100, velocity: 80 },
          ],
        }],
      }),
      [{ event_id: 'e1', confidence: 0.3, stem: 'melody', status: 'recovered' }],
      {
        pixelsPerTick: 1,
        pitchMidiMax: 72,
        pitchMidiMin: 36,
        rowHeight: 10,
        threshold: 0.5,
      },
    );
    assert.equal(bound.length, 1);
    assert.equal(bound[0].low, true);
    assert.equal(bound[0].status, 'recovered');
  });
});

describe('audioRecoveryPhaseGuards', () => {
  it('blocks recovery while midi/live/mono capture active', () => {
    assert.equal(assertRecoveryAllowedForOtherPhases('recording', 'idle', 'idle').ok, false);
    assert.equal(assertRecoveryAllowedForOtherPhases('idle', 'running', 'idle').ok, false);
    assert.equal(assertRecoveryAllowedForOtherPhases('idle', 'idle', 'recording').ok, false);
    assert.equal(assertRecoveryAllowedForOtherPhases('ready', 'idle', 'idle').ok, true);
  });

  it('blocks mono audio while recovery active', () => {
    assert.equal(assertMonoAudioAllowedForRecoveryPhase('running').ok, false);
    assert.equal(assertMonoAudioAllowedForRecoveryPhase('idle').ok, true);
  });
});

describe('audioRecoveryApply', () => {
  it('strips confidence and returns event map', () => {
    const preview = {
      schema_version: 'audio.recovery.preview.v1',
      preview_fingerprint: 'fp-test-12345678',
      notes: [
        {
          provisional_id: 'p1',
          stem: 'melody',
          pitch: 60,
          start_tick: 0,
          duration_ticks: 240,
          velocity: 80,
          confidence: 0.9,
        },
      ],
      scaffolding: {
        tempo_bpm: 120,
        tempo_confidence: 0.8,
        tempo_source: 'estimated',
        meter: '4/4',
        beat_grid: { downbeat_offset_seconds: 0, ticks_per_quarter: 480, confidence: 0.7 },
        structure: [],
        key: { tonic: 'C', mode: 'major', confidence: 0.7 },
        harmony: [],
      },
      stems: [],
      issues: [],
      summary: {
        note_count: 1,
        low_confidence_count: 0,
        excluded_low_confidence_count: 0,
        include_threshold: 0.5,
        stem_count: 1,
        separation_status: 'complete',
      },
      engine: { id: 'fake:audio-recovery', version: '1', fake: true },
    };
    const applied = applyAudioRecoveryToComposition(baseComposition, {
      preview,
      stemRoleMap: { melody: 't1' },
      selectedIds: ['p1'],
    });
    assert.equal(applied.ok, true);
    assert.equal(applied.eventMap.length, 1);
    const ev = applied.composition.tracks[0].events[0];
    assert.ok(ev.id);
    assert.equal(Object.prototype.hasOwnProperty.call(ev, 'confidence'), false);
  });
});
