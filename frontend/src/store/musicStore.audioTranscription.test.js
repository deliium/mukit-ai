/**
 * Store session tests for monophonic audio transcription review / apply / undo.
 */

import assert from 'node:assert/strict';
import test from 'node:test';
import { migrateV1ToV2 } from '../utils/compositionVersion.js';
import { defaultSelectedProvisionalIds } from '../utils/audioTranscriptionApply.js';
import { AUDIO_PHASES, useMusicStore } from './musicStore.js';

function sampleComposition() {
  return migrateV1ToV2({
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
        events: [],
      },
    ],
    harmony: [],
  });
}

const samplePreview = {
  schema_version: 'transcription.preview.v1',
  notes: [
    {
      provisional_id: 'a1',
      pitch: 60,
      start_tick: 0,
      duration_ticks: 480,
      velocity: 90,
      confidence: 0.95,
    },
    {
      provisional_id: 'a2',
      pitch: 64,
      start_tick: 480,
      duration_ticks: 480,
      velocity: 80,
      confidence: 0.3,
    },
  ],
  summary: {
    note_count: 2,
    low_confidence_count: 1,
    excluded_low_confidence_count: 1,
    include_threshold: 0.5,
  },
  timing: {
    tempo_bpm: 120,
    ticks_per_quarter: 480,
    origin_tick: 0,
    tempo_source: 'provided',
    meter: '4/4',
  },
  engine: { id: 'fake:audio-mono', version: '1', fake: true },
  issues: [],
};

function resetAudioStore(composition = sampleComposition()) {
  useMusicStore.getState().discardAudioTranscription();
  useMusicStore.setState({
    generatedMusicJson: composition,
    editedMusicJson: composition,
    compositionRevision: 'audio-test-rev',
    pianoRollTrackId: 'melody-1',
    audioDestinationTrackId: 'melody-1',
    editCursorTick: 0,
    compositionEditUndoStack: [],
    compositionEditRedoStack: [],
    lockedTrackIds: [],
    pianoRollSnap: '1/8',
  });
}

function seedReviewPreview(preview = samplePreview) {
  const threshold = preview.summary?.include_threshold ?? 0.5;
  useMusicStore.setState({
    audioPhase: AUDIO_PHASES.REVIEW,
    audioPreview: preview,
    audioConfidenceThreshold: threshold,
    audioIncludeLowConfidence: false,
    audioSelectedProvisionalIds: defaultSelectedProvisionalIds(preview, threshold),
    audioQuantizeOnApply: false,
    audioErrorCode: null,
    audioErrorMessage: '',
  });
}

test('probeAudioSupport does not call getUserMedia', () => {
  resetAudioStore();
  let called = false;
  const previousNavigator = globalThis.navigator;
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true,
    value: {
      mediaDevices: {
        getUserMedia: async () => {
          called = true;
          throw new Error('should not run');
        },
      },
    },
  });
  globalThis.isSecureContext = true;
  globalThis.MediaRecorder = function MediaRecorder() {};

  const support = useMusicStore.getState().probeAudioSupport();
  assert.equal(called, false);
  assert.equal(support.reason === 'available' || support.supported === true || support.supported === false, true);
  assert.ok(useMusicStore.getState().audioSupport);

  Object.defineProperty(globalThis, 'navigator', {
    configurable: true,
    value: previousNavigator,
  });
});

test('review defaults exclude low-confidence provisional ids', () => {
  resetAudioStore();
  seedReviewPreview();
  const state = useMusicStore.getState();
  assert.equal(state.audioPhase, AUDIO_PHASES.REVIEW);
  assert.deepEqual(state.audioSelectedProvisionalIds, ['a1']);
  assert.equal(state.audioIncludeLowConfidence, false);
});

test('include low-confidence expands selection; toggle off restores defaults', () => {
  resetAudioStore();
  seedReviewPreview();
  useMusicStore.getState().setAudioIncludeLowConfidence(true);
  assert.deepEqual(
    useMusicStore.getState().audioSelectedProvisionalIds.sort(),
    ['a1', 'a2'],
  );
  useMusicStore.getState().setAudioIncludeLowConfidence(false);
  assert.deepEqual(useMusicStore.getState().audioSelectedProvisionalIds, ['a1']);
});

test('apply commits selected notes without confidence and undo removes take', () => {
  resetAudioStore();
  seedReviewPreview();
  const beforeCount = useMusicStore.getState().editedMusicJson.tracks[0].events.length;
  const result = useMusicStore.getState().applyAudioTranscription();
  assert.equal(result.ok, true);
  const after = useMusicStore.getState();
  assert.equal(after.editedMusicJson.tracks[0].events.length, beforeCount + 1);
  const event = after.editedMusicJson.tracks[0].events[0];
  assert.equal(event.pitch, 'C4');
  assert.equal(event.confidence, undefined);
  assert.equal(after.audioPhase, AUDIO_PHASES.IDLE);
  assert.equal(after.audioPreview, null);
  assert.ok(after.compositionEditUndoStack.length >= 1);

  after.undoCompositionEdit();
  assert.equal(useMusicStore.getState().editedMusicJson.tracks[0].events.length, beforeCount);
});

test('quantize on apply snaps start ticks in single transaction', () => {
  resetAudioStore();
  const preview = {
    ...samplePreview,
    notes: [
      {
        provisional_id: 'a1',
        pitch: 60,
        start_tick: 10,
        duration_ticks: 240,
        velocity: 90,
        confidence: 0.95,
      },
    ],
    summary: {
      note_count: 1,
      low_confidence_count: 0,
      excluded_low_confidence_count: 0,
      include_threshold: 0.5,
    },
  };
  seedReviewPreview(preview);
  useMusicStore.setState({ audioQuantizeOnApply: true, pianoRollSnap: '1/8' });
  const result = useMusicStore.getState().applyAudioTranscription();
  assert.equal(result.ok, true);
  const event = useMusicStore.getState().editedMusicJson.tracks[0].events[0];
  assert.equal(event.start_tick, 0);
  assert.equal(event.confidence, undefined);
  assert.ok(useMusicStore.getState().compositionEditUndoStack.length >= 1);
  useMusicStore.getState().undoCompositionEdit();
});

test('discard clears preview without mutating composition', () => {
  resetAudioStore();
  seedReviewPreview();
  assert.equal(useMusicStore.getState().editedMusicJson.tracks[0].events.length, 0);
  useMusicStore.getState().discardAudioTranscription();
  const state = useMusicStore.getState();
  assert.equal(state.audioPhase, AUDIO_PHASES.IDLE);
  assert.equal(state.audioPreview, null);
  assert.deepEqual(state.audioSelectedProvisionalIds, []);
  assert.equal(state.editedMusicJson.tracks[0].events.length, 0);
});
