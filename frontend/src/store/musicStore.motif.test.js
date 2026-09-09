import assert from 'node:assert/strict';
import test from 'node:test';

import axios from 'axios';
import { migrateV1ToV2 } from '../utils/compositionVersion.js';
import { useMusicStore } from './musicStore.js';

function motifAuthoringComposition() {
  return migrateV1ToV2({
    schema_version: 'composition.v1',
    tempo: 100,
    key: 'C major',
    time_signature: '4/4',
    ticks_per_quarter: 480,
    bar_count: 4,
    duration_ticks: 7680,
    sections: [
      { id: 'verse', type: 'verse', start_bar: 1, bar_count: 2, start_tick: 0, duration_ticks: 3840 },
      { id: 'chorus', type: 'chorus', start_bar: 3, bar_count: 2, start_tick: 3840, duration_ticks: 3840 },
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
          { type: 'note', id: 'n1', pitch: 'C4', start_tick: 0, duration_ticks: 480, velocity: 90 },
          { type: 'note', id: 'n2', pitch: 'D4', start_tick: 480, duration_ticks: 480, velocity: 88 },
          { type: 'note', id: 'n3', pitch: 'E4', start_tick: 960, duration_ticks: 480, velocity: 87 },
          { type: 'note', id: 'n4', pitch: 'F4', start_tick: 3840, duration_ticks: 480, velocity: 86 },
        ],
      },
    ],
    harmony: [{ bar: 1, chord: 'C' }],
  });
}

function resetMotifStore(composition = motifAuthoringComposition()) {
  useMusicStore.setState({
    generatedMusicJson: composition,
    editedMusicJson: composition,
    compositionRevision: 'test-rev',
    notationRevision: 'test-notation',
    trackControls: {},
    pianoRollTrackId: 'melody-1',
    pianoRollNoteId: null,
    pianoRollNoteIds: [],
    noteEditUndoStack: [],
    noteEditRedoStack: [],
    playbackStatus: 'playing',
    playbackSeconds: 12,
    playbackBar: 2,
    selectedProvider: 'fake',
    selectedModel: 'fake-model',
    currentProjectId: 'project-motif',
    currentProjectName: 'Motif Project',
    activeView: 'composer',
    motifSelectedMotifId: null,
    motifSelectedOccurrenceId: null,
    motifHighlightedUsageKey: null,
    motifDestinationSectionId: 'chorus',
    motifDestinationTrackId: 'melody-1',
    motifDestinationStartBar: 3,
    motifDestinationStartTick: 3840,
    motifOperation: 'repeat',
    motifOperationParams: {},
    motifVariationStrength: 0.5,
    motifApplyStatus: 'idle',
    motifApplyError: '',
    motifApplyWarnings: [],
    motifReconcileWarnings: [],
    analysisResult: null,
    analysisStatus: 'idle',
  });
}

function installAxiosStub(handler) {
  const previousAdapter = axios.defaults.adapter;
  axios.defaults.adapter = handler;
  return () => {
    axios.defaults.adapter = previousAdapter;
  };
}

test('markMotifFromSelection writes motif definitions into editedMusicJson.motifs', () => {
  resetMotifStore();
  const store = useMusicStore.getState();
  store.selectPianoRollNote('n1');
  store.selectPianoRollNote('n2', { extend: true });
  store.selectPianoRollNote('n3', { extend: true });

  const result = store.markMotifFromSelection();
  assert.equal(result.ok, true);
  const after = useMusicStore.getState();
  assert.equal(after.editedMusicJson.motifs.length, 1);
  assert.equal(after.editedMusicJson.motifs[0].label, 'Motif A');
  assert.equal(after.editedMusicJson.motifs[0].occurrences[0].relationship, 'original');
  assert.deepEqual(after.editedMusicJson.motifs[0].occurrences[0].event_ids, ['n1', 'n2', 'n3']);
  assert.equal(after.motifSelectedMotifId, after.editedMusicJson.motifs[0].id);
  assert.equal(after.noteEditUndoStack.length, 1);
  assert.equal(after.noteEditRedoStack.length, 0);
  assert.equal(after.playbackStatus, 'idle');
});

test('deleteMotif removes definition and supports undo/redo', () => {
  resetMotifStore();
  const store = useMusicStore.getState();
  store.selectPianoRollNote('n1');
  store.selectPianoRollNote('n2', { extend: true });
  store.selectPianoRollNote('n3', { extend: true });
  const marked = store.markMotifFromSelection();
  assert.equal(marked.ok, true);
  const motifId = useMusicStore.getState().editedMusicJson.motifs[0].id;

  assert.equal(store.deleteMotif(motifId), true);
  assert.equal(useMusicStore.getState().editedMusicJson.motifs.length, 0);
  assert.equal(useMusicStore.getState().motifSelectedMotifId, null);

  assert.equal(store.undoNoteEdit(), true);
  assert.equal(useMusicStore.getState().editedMusicJson.motifs.length, 1);
  assert.equal(store.redoNoteEdit(), true);
  assert.equal(useMusicStore.getState().editedMusicJson.motifs.length, 0);
});

test('deleteNote reconciles motif references when original source note is removed', () => {
  resetMotifStore();
  const store = useMusicStore.getState();
  store.selectPianoRollNote('n1');
  store.selectPianoRollNote('n2', { extend: true });
  store.selectPianoRollNote('n3', { extend: true });
  assert.equal(store.markMotifFromSelection().ok, true);

  assert.equal(store.deleteNote('melody-1', 'n1'), true);
  const after = useMusicStore.getState();
  assert.equal(after.editedMusicJson.motifs.length, 0);
  assert.ok(after.motifReconcileWarnings.length >= 1);
});

test('completeMotifApply and failMotifApply manage request status without mutating on failure', () => {
  resetMotifStore();
  const store = useMusicStore.getState();
  store.selectPianoRollNote('n1');
  store.selectPianoRollNote('n2', { extend: true });
  store.selectPianoRollNote('n3', { extend: true });
  const marked = store.markMotifFromSelection();
  store.selectMotif(marked.motifId);
  store.configureMotifDestination({
    sectionId: 'chorus',
    trackId: 'melody-1',
    startBar: 3,
    startTick: 3840,
  });

  const before = structuredClone(useMusicStore.getState().editedMusicJson);
  assert.equal(store.startMotifApply(), true);
  store.failMotifApply('provider unavailable', { code: 'motif_creative_unavailable' });
  const failed = useMusicStore.getState();
  assert.deepEqual(failed.editedMusicJson, before);
  assert.equal(failed.motifApplyStatus, 'error');
  assert.match(failed.motifApplyError, /provider unavailable/);

  const applied = structuredClone(before);
  applied.motifs[0].occurrences.push({
    id: 'occ-repeat',
    track_id: 'melody-1',
    event_ids: ['n4', 'n5', 'n6'],
    relationship: 'repeat',
  });
  applied.tracks[0].events.push(
    { type: 'note', id: 'n5', pitch: 'G4', start_tick: 4320, duration_ticks: 480, velocity: 85 },
    { type: 'note', id: 'n6', pitch: 'A4', start_tick: 4800, duration_ticks: 480, velocity: 84 },
  );
  assert.equal(store.completeMotifApply({
    composition: applied,
    musicxml: '<score/>',
    warnings: ['motif_overlap_replaced'],
    result: {
      motif_id: marked.motifId,
      source_occurrence_id: marked.occurrenceId,
      destination_track_id: 'melody-1',
      new_occurrence_id: 'occ-repeat',
      created_event_ids: ['n4', 'n5', 'n6'],
    },
  }), true);
  const success = useMusicStore.getState();
  assert.equal(success.motifApplyStatus, 'success');
  assert.equal(success.motifApplyWarnings.length, 1);
  assert.equal(success.noteEditUndoStack.length, 2);
  assert.equal(success.playbackStatus, 'idle');
});

test('applyMotifTransformation calls API and completes atomically', async (t) => {
  const restore = installAxiosStub(async (config) => {
    assert.equal(config.url, '/motifs/apply');
    const body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
    assert.equal(body.operation, 'repeat');
    assert.equal(body.source.motif_id, body.composition.motifs[0].id);
    const composition = structuredClone(body.composition);
    const newOccurrenceId = 'occ-applied';
    composition.motifs[0].occurrences.push({
      id: newOccurrenceId,
      track_id: 'melody-1',
      event_ids: ['n4', 'n5', 'n6'],
      relationship: 'repeat',
    });
    composition.tracks[0].events.push(
      { type: 'note', id: 'n5', pitch: 'G4', start_tick: 4320, duration_ticks: 480, velocity: 85 },
      { type: 'note', id: 'n6', pitch: 'A4', start_tick: 4800, duration_ticks: 480, velocity: 84 },
    );
    return {
      data: {
        composition,
        musicxml: '<score/>',
        warnings: [],
        result: {
          motif_id: composition.motifs[0].id,
          source_occurrence_id: composition.motifs[0].occurrences[0].id,
          destination_section_id: 'chorus',
          destination_track_id: 'melody-1',
          destination_start_bar: 3,
          destination_start_tick: 3840,
          created_event_ids: ['n4', 'n5', 'n6'],
          new_occurrence_id: newOccurrenceId,
          relationship: 'repeat',
          identity_score: 1,
          transform: { operation: 'repeat', variation_strength: null },
          diagnostics: {
            source_event_count: 3,
            created_event_count: 3,
            replaced_event_count: 1,
            destination_span_ticks: 1440,
            warning_codes: [],
          },
        },
      },
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    };
  });
  t.after(restore);

  resetMotifStore();
  const store = useMusicStore.getState();
  store.selectPianoRollNote('n1');
  store.selectPianoRollNote('n2', { extend: true });
  store.selectPianoRollNote('n3', { extend: true });
  const marked = store.markMotifFromSelection();
  store.selectMotif(marked.motifId);
  store.configureMotifDestination({
    sectionId: 'chorus',
    trackId: 'melody-1',
    startBar: 3,
    startTick: 3840,
  });

  assert.equal(await store.applyMotifTransformation(), true);
  const after = useMusicStore.getState();
  assert.equal(after.motifApplyStatus, 'success');
  assert.equal(after.editedMusicJson.motifs[0].occurrences.length, 2);
  assert.equal(after.motifSelectedOccurrenceId, 'occ-applied');
});

test('motif UI state resets on project open and JSON edit', async (t) => {
  const restore = installAxiosStub(async (config) => ({
    data: {
      id: 'p-motif',
      name: 'Other',
      composition: motifAuthoringComposition(),
      generation_provider: null,
      generation_model: null,
      generation_prompt: null,
    },
    status: 200,
    statusText: 'OK',
    headers: {},
    config,
  }));
  t.after(restore);

  resetMotifStore();
  const store = useMusicStore.getState();
  store.selectPianoRollNote('n1');
  store.selectPianoRollNote('n2', { extend: true });
  store.selectPianoRollNote('n3', { extend: true });
  store.markMotifFromSelection();
  store.configureMotifTransformation({ operation: 'transpose', operationParams: { transpose_semitones: 2 } });
  useMusicStore.setState({
    motifApplyStatus: 'success',
    motifHighlightedUsageKey: 'canonical:test:test',
    motifOperation: 'transpose',
    motifOperationParams: { transpose_semitones: 2 },
  });

  await store.openProject('p-motif');
  const opened = useMusicStore.getState();
  assert.equal(opened.motifSelectedMotifId, null);
  assert.equal(opened.motifApplyStatus, 'idle');
  assert.equal(opened.motifOperation, 'repeat');

  useMusicStore.setState({
    motifSelectedMotifId: 'stale',
    motifApplyStatus: 'error',
    motifApplyError: 'stale',
  });
  store.setEditedMusicJson(structuredClone(opened.editedMusicJson));
  const edited = useMusicStore.getState();
  assert.equal(edited.motifSelectedMotifId, null);
  assert.equal(edited.motifApplyStatus, 'idle');
  assert.equal(edited.motifApplyError, '');
});

test('undo/redo motif mark restores composition and clears apply status', () => {
  resetMotifStore();
  const store = useMusicStore.getState();
  store.selectPianoRollNote('n1');
  store.selectPianoRollNote('n2', { extend: true });
  store.selectPianoRollNote('n3', { extend: true });
  store.markMotifFromSelection();
  useMusicStore.setState({ motifApplyStatus: 'success', motifApplyError: '', motifApplyWarnings: ['x'] });

  assert.equal(store.undoNoteEdit(), true);
  const undone = useMusicStore.getState();
  assert.equal(undone.editedMusicJson.motifs?.length || 0, 0);
  assert.equal(undone.motifApplyStatus, 'idle');
  assert.equal(undone.motifApplyWarnings.length, 0);

  assert.equal(store.redoNoteEdit(), true);
  assert.equal(useMusicStore.getState().editedMusicJson.motifs.length, 1);
});
