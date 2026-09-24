/**
 * Store lifecycle: recovery overlay prune / user_edited on editor ops + undo restore.
 */

import assert from 'node:assert/strict';
import test from 'node:test';
import { migrateV1ToV2 } from '../utils/compositionVersion.js';
import { AUDIO_RECOVERY_PHASES, useMusicStore } from './musicStore.js';

function sampleCompositionWithNotes() {
  const base = migrateV1ToV2({
    schema_version: 'composition.v1',
    tempo: 120,
    key: 'C major',
    time_signature: '4/4',
    ticks_per_quarter: 480,
    bar_count: 4,
    duration_ticks: 7680,
    sections: [
      { id: 'verse', type: 'verse', start_bar: 1, bar_count: 4, start_tick: 0, duration_ticks: 7680 },
    ],
    tracks: [
      {
        id: 'melody-1',
        name: 'Melody',
        instrument: 'piano',
        role: 'melody',
        midi_program: 0,
        channel: 1,
        is_drum: false,
        volume: 100,
        events: [
          {
            id: 'rec-e1',
            pitch: 'C4',
            start_tick: 0,
            duration_ticks: 480,
            velocity: 90,
          },
          {
            id: 'rec-e2',
            pitch: 'E4',
            start_tick: 480,
            duration_ticks: 480,
            velocity: 80,
          },
        ],
      },
    ],
    harmony: [],
  });
  return base;
}

function seedBoundOverlay(composition = sampleCompositionWithNotes()) {
  useMusicStore.setState({
    generatedMusicJson: composition,
    editedMusicJson: composition,
    compositionRevision: 'recovery-overlay-test',
    pianoRollTrackId: 'melody-1',
    pianoRollNoteId: null,
    pianoRollNoteIds: [],
    editorSelectionRefs: [],
    editorSelectionPrimary: null,
    editCursorTick: 0,
    compositionEditUndoStack: [],
    compositionEditRedoStack: [],
    lockedTrackIds: [],
    recoveryPhase: AUDIO_RECOVERY_PHASES.BOUND,
    recoveryOverlay: [
      {
        event_id: 'rec-e1',
        track_id: 'melody-1',
        confidence: 0.9,
        stem: 'melody',
        status: 'recovered',
      },
      {
        event_id: 'rec-e2',
        track_id: 'melody-1',
        confidence: 0.4,
        stem: 'melody',
        status: 'recovered',
      },
    ],
  });
}

test('deleteSelection prunes recovery overlay entry', () => {
  seedBoundOverlay();
  useMusicStore.setState({
    pianoRollNoteId: 'rec-e2',
    pianoRollNoteIds: ['rec-e2'],
    editorSelectionRefs: [{ trackId: 'melody-1', eventId: 'rec-e2' }],
    editorSelectionPrimary: { trackId: 'melody-1', eventId: 'rec-e2' },
  });
  const result = useMusicStore.getState().deleteSelection();
  assert.equal(result.ok, true);
  const overlay = useMusicStore.getState().recoveryOverlay;
  assert.equal(overlay.length, 1);
  assert.equal(overlay[0].event_id, 'rec-e1');
  assert.equal(overlay[0].status, 'recovered');
});

test('transposeSelection marks recovered overlay user_edited', () => {
  seedBoundOverlay();
  useMusicStore.setState({
    pianoRollNoteId: 'rec-e1',
    pianoRollNoteIds: ['rec-e1'],
    editorSelectionRefs: [{ trackId: 'melody-1', eventId: 'rec-e1' }],
    editorSelectionPrimary: { trackId: 'melody-1', eventId: 'rec-e1' },
  });
  const result = useMusicStore.getState().transposeSelection(2);
  assert.equal(result.ok, true);
  const overlay = useMusicStore.getState().recoveryOverlay;
  assert.equal(overlay.length, 2);
  const e1 = overlay.find((row) => row.event_id === 'rec-e1');
  assert.equal(e1.status, 'user_edited');
  const e2 = overlay.find((row) => row.event_id === 'rec-e2');
  assert.equal(e2.status, 'recovered');
});

test('undo restores recovery overlay after prune', () => {
  seedBoundOverlay();
  useMusicStore.setState({
    pianoRollNoteId: 'rec-e2',
    pianoRollNoteIds: ['rec-e2'],
    editorSelectionRefs: [{ trackId: 'melody-1', eventId: 'rec-e2' }],
    editorSelectionPrimary: { trackId: 'melody-1', eventId: 'rec-e2' },
  });
  assert.equal(useMusicStore.getState().deleteSelection().ok, true);
  assert.equal(useMusicStore.getState().recoveryOverlay.length, 1);
  assert.equal(useMusicStore.getState().undoCompositionEdit(), true);
  const restored = useMusicStore.getState().recoveryOverlay;
  assert.equal(restored.length, 2);
  assert.ok(restored.some((row) => row.event_id === 'rec-e2'));
});
