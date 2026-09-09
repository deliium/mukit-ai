import assert from 'node:assert/strict';
import test from 'node:test';

import axios from 'axios';
import { migrateV1ToV2 } from '../utils/compositionVersion.js';
import { useMusicStore } from '../store/musicStore.js';

const BASE = migrateV1ToV2({
  schema_version: 'composition.v1',
  tempo: 100,
  key: 'C major',
  time_signature: '4/4',
  ticks_per_quarter: 480,
  bar_count: 2,
  duration_ticks: 3840,
  sections: [
    { type: 'intro', start_bar: 1, bar_count: 2, start_tick: 0, duration_ticks: 3840 },
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
      pan: 0,
      events: [
        { type: 'note', id: 'n1', pitch: 'C4', start_tick: 0, duration_ticks: 480, velocity: 90 },
        { type: 'note', id: 'n2', pitch: 'E4', start_tick: 0, duration_ticks: 480, velocity: 88 },
      ],
    },
    {
      id: 'bass-1',
      name: 'Bass',
      instrument: 'bass',
      role: 'bass',
      midi_program: 32,
      channel: 2,
      is_drum: false,
      volume: 100,
      pan: 0,
      events: [
        { type: 'note', id: 'b1', pitch: 'C2', start_tick: 0, duration_ticks: 960, velocity: 90 },
      ],
    },
  ],
  harmony: [{ bar: 1, chord: 'C' }],
});

function resetStore(composition = structuredClone(BASE)) {
  useMusicStore.setState({
    generatedMusicJson: composition,
    editedMusicJson: composition,
    musicXml: '',
    compositionRevision: 'test',
    trackControls: {},
    pianoRollTrackId: 'melody-1',
    pianoRollNoteId: null,
    pianoRollNoteIds: [],
    pianoRollSnap: '1/8',
    pianoRollZoom: 0.05,
    pianoRollEditStatus: 'idle',
    pianoRollNotationStatus: 'idle',
    pianoRollNotationError: '',
    noteEditUndoStack: [],
    noteEditRedoStack: [],
    aiEditStartBar: null,
    aiEditEndBar: null,
    aiEditTrackMode: 'current',
    aiEditTrackIds: null,
    aiEditInstruction: '',
    aiEditStatus: 'idle',
    aiEditError: '',
    aiEditWarnings: [],
    selectedProvider: 'openai',
    selectedModel: 'test-model',
    playbackStatus: 'idle',
    playbackSeconds: 0,
    playbackBar: 1,
    uiError: '',
    warnings: [],
  });
}

test('store create/update/delete and undo/redo keep editedMusicJson authoritative', () => {
  resetStore();
  const store = useMusicStore.getState();

  const created = store.createNote('melody-1', {
    pitch: 'G4',
    start_tick: 480,
    duration_ticks: 240,
  });
  assert.ok(created?.id);
  assert.equal(useMusicStore.getState().editedMusicJson.tracks[0].events.length, 3);
  assert.equal(useMusicStore.getState().pianoRollNoteId, created.id);

  const updated = store.updateNote('melody-1', created.id, { pitch: 'A4', start_tick: 720 });
  assert.equal(updated.pitch, 'A4');
  assert.equal(updated.start_tick, 720);
  // Polyphonic overlap with existing notes remains allowed
  assert.equal(useMusicStore.getState().editedMusicJson.tracks[0].events.length, 3);

  const resized = store.updateNote('melody-1', created.id, { duration_ticks: 960 });
  assert.equal(resized.duration_ticks, 960);

  assert.equal(store.deleteNote('melody-1', created.id), true);
  assert.equal(useMusicStore.getState().editedMusicJson.tracks[0].events.length, 2);

  assert.equal(store.undoNoteEdit(), true);
  assert.equal(useMusicStore.getState().editedMusicJson.tracks[0].events.length, 3);
  assert.equal(store.redoNoteEdit(), true);
  assert.equal(useMusicStore.getState().editedMusicJson.tracks[0].events.length, 2);
});

test('store rejects invalid snap/zoom and recovers missing track selection', () => {
  resetStore();
  const store = useMusicStore.getState();
  store.setPianoRollSnap('1/3');
  assert.equal(useMusicStore.getState().pianoRollSnap, '1/8');
  store.setPianoRollZoom(99);
  assert.equal(useMusicStore.getState().pianoRollZoom, 0.05);

  store.setEditedMusicJson({
    ...BASE,
    tracks: [BASE.tracks[1]],
  });
  assert.equal(useMusicStore.getState().pianoRollTrackId, 'bass-1');
});

test('updateNote skipHistory does not grow undo stack on every call', () => {
  resetStore();
  const store = useMusicStore.getState();
  const snapshot = {
    editedMusicJson: useMusicStore.getState().editedMusicJson,
    pianoRollTrackId: 'melody-1',
    pianoRollNoteId: 'n1',
  };
  store.updateNote('melody-1', 'n1', { start_tick: 240 }, { historySnapshot: snapshot });
  assert.equal(useMusicStore.getState().noteEditUndoStack.length, 1);
  store.updateNote('melody-1', 'n1', { start_tick: 480 }, { skipHistory: true });
  store.updateNote('melody-1', 'n1', { start_tick: 720 }, { skipHistory: true });
  assert.equal(useMusicStore.getState().noteEditUndoStack.length, 1);
  assert.equal(
    useMusicStore.getState().editedMusicJson.tracks[0].events.find((event) => event.id === 'n1').start_tick,
    720,
  );
});

test('AI edit failure leaves composition and history unchanged', () => {
  resetStore();
  const before = structuredClone(useMusicStore.getState().editedMusicJson);
  const store = useMusicStore.getState();
  store.setAiEditSelection({ startBar: 1, endBar: 2, trackMode: 'current' });
  store.setAiEditInstruction('make this phrase more dramatic but keep the harmony');
  assert.equal(store.startAiEdit(), true);
  store.failAiEdit('provider failed');
  const after = useMusicStore.getState();
  assert.deepEqual(after.editedMusicJson, before);
  assert.equal(after.noteEditUndoStack.length, 0);
  assert.equal(after.noteEditRedoStack.length, 0);
  assert.equal(after.aiEditStatus, 'error');
  assert.match(after.aiEditError, /provider failed/);
});

test('AI edit success pushes one undo snapshot and supports undo/redo', () => {
  resetStore();
  const store = useMusicStore.getState();
  const before = structuredClone(useMusicStore.getState().editedMusicJson);
  store.setAiEditSelection({ startBar: 1, endBar: 1, trackMode: 'current' });
  store.setAiEditInstruction('make this phrase more dramatic but keep the harmony');
  store.startAiEdit();

  const edited = structuredClone(before);
  edited.tracks[0].events = [
    { type: 'note', id: 'n1', pitch: 'G4', start_tick: 0, duration_ticks: 480, velocity: 100 },
    { type: 'note', id: 'n2', pitch: 'E4', start_tick: 0, duration_ticks: 480, velocity: 88 },
  ];
  assert.equal(store.completeAiEdit({ composition: edited, musicxml: '<score/>', warnings: ['ok'] }), true);

  const afterApply = useMusicStore.getState();
  assert.equal(afterApply.noteEditUndoStack.length, 1);
  assert.equal(afterApply.aiEditStatus, 'success');
  assert.equal(afterApply.editedMusicJson.tracks[0].events[0].pitch, 'G4');
  assert.equal(afterApply.playbackStatus, 'idle');

  assert.equal(store.undoNoteEdit(), true);
  assert.equal(useMusicStore.getState().editedMusicJson.tracks[0].events[0].pitch, 'C4');
  assert.equal(store.redoNoteEdit(), true);
  assert.equal(useMusicStore.getState().editedMusicJson.tracks[0].events[0].pitch, 'G4');
});

test('setEditedMusicJson migrates v1 payloads to v2 in store state', () => {
  resetStore();
  const v1 = {
    schema_version: 'composition.v1',
    tempo: 100,
    key: 'C major',
    time_signature: '4/4',
    ticks_per_quarter: 480,
    bar_count: 2,
    duration_ticks: 3840,
    sections: [
      { type: 'intro', start_bar: 1, bar_count: 2, start_tick: 0, duration_ticks: 3840 },
    ],
    tracks: BASE.tracks.map((track) => ({
      ...track,
      expression: undefined,
      dynamic_marks: undefined,
      sustain_pedals: undefined,
      automation: undefined,
      events: track.events.map((event) => ({
        type: 'note',
        id: event.id,
        pitch: event.pitch,
        start_tick: event.start_tick,
        duration_ticks: event.duration_ticks,
        velocity: event.velocity,
      })),
    })),
    harmony: [{ bar: 1, chord: 'C' }],
  };
  useMusicStore.getState().setEditedMusicJson(v1);
  assert.equal(useMusicStore.getState().editedMusicJson.schema_version, 'composition.v2');
});

test('composition edits clear stale musicXml when notation revision changes', () => {
  resetStore();
  useMusicStore.setState({ musicXml: '<score/>' });
  const current = structuredClone(useMusicStore.getState().editedMusicJson);
  useMusicStore.getState().setEditedMusicJson({ ...current, tempo: 110 });
  assert.equal(useMusicStore.getState().musicXml, '');
});

test('refreshMusicXmlFromEditedComposition discards stale preview responses', async (t) => {
  resetStore();
  let resolvePreview;
  const previousAdapter = axios.defaults.adapter;
  axios.defaults.adapter = async () => new Promise((resolve) => {
    resolvePreview = () => resolve({
      data: '<score/>',
      status: 200,
      statusText: 'OK',
      headers: {},
      config: {},
    });
  });
  t.after(() => {
    axios.defaults.adapter = previousAdapter;
  });

  const refreshPromise = useMusicStore.getState().refreshMusicXmlFromEditedComposition();
  const requestedRevision = useMusicStore.getState().notationRevision;
  const current = structuredClone(useMusicStore.getState().editedMusicJson);
  useMusicStore.getState().setEditedMusicJson({ ...current, tempo: 115 });
  resolvePreview();
  const result = await refreshPromise;
  assert.equal(result, null);
  assert.notEqual(useMusicStore.getState().notationRevision, requestedRevision);
  assert.equal(useMusicStore.getState().musicXml, '');
});

test('store tie and articulation edits preserve V2 metadata and undo/redo', () => {
  resetStore();
  const expressive = structuredClone(BASE);
  expressive.tempo_changes = [{ tick: 1920, bpm: 90 }];
  expressive.tracks[0].dynamic_marks = [{ tick: 480, level: 'mf' }];
  expressive.tracks[0].events = [
    { type: 'note', id: 'n1', pitch: 'C4', start_tick: 0, duration_ticks: 480, velocity: 90, articulations: [], tie: null },
    { type: 'note', id: 'n2', pitch: 'C4', start_tick: 480, duration_ticks: 480, velocity: 90, articulations: [], tie: null },
    { type: 'note', id: 'n3', pitch: 'E4', start_tick: 0, duration_ticks: 480, velocity: 88, articulations: [], tie: null },
  ];
  useMusicStore.setState({ editedMusicJson: expressive, generatedMusicJson: expressive });

  const store = useMusicStore.getState();
  store.selectPianoRollNote('n1');
  store.selectPianoRollNote('n2', { extend: true });
  assert.equal(useMusicStore.getState().applyTieChain('melody-1', ['n1', 'n2']), true);
  assert.equal(useMusicStore.getState().editedMusicJson.tracks[0].events[0].tie.type, 'start');
  assert.equal(useMusicStore.getState().editedMusicJson.tempo_changes.length, 1);
  assert.equal(useMusicStore.getState().editedMusicJson.tracks[0].dynamic_marks.length, 1);

  store.toggleNoteArticulation('melody-1', 'n1', 'tenuto');
  assert.deepEqual(
    useMusicStore.getState().editedMusicJson.tracks[0].events[0].articulations,
    ['tenuto'],
  );

  assert.equal(store.undoNoteEdit(), true);
  assert.deepEqual(
    useMusicStore.getState().editedMusicJson.tracks[0].events[0].articulations,
    [],
  );
  assert.equal(store.redoNoteEdit(), true);
  assert.deepEqual(
    useMusicStore.getState().editedMusicJson.tracks[0].events[0].articulations,
    ['tenuto'],
  );
});

test('invalid edited JSON remains visibly unsaved and blocks save', async () => {
  resetStore();
  useMusicStore.setState({
    currentProjectId: 'project-1',
    lastSavedPersistRevision: 'saved',
    saveStatus: 'saved',
  });
  const invalid = structuredClone(BASE);
  invalid.tracks[0].events[0].velocity = 200;
  useMusicStore.getState().setEditedMusicJson(invalid);
  assert.equal(useMusicStore.getState().saveStatus, 'unsaved');
  const saved = await useMusicStore.getState().saveCurrentProject({ reason: 'manual' });
  assert.equal(saved, null);
  assert.equal(useMusicStore.getState().saveStatus, 'unsaved');
  assert.match(useMusicStore.getState().saveError, /velocity/i);
});

const BAR = 1920;

function sixteenBarComposition() {
  const melody = [];
  const bass = [];
  const accompaniment = [];
  for (let bar = 0; bar < 16; bar += 1) {
    const start = bar * BAR;
    melody.push({
      type: 'note', pitch: ['C4', 'D4', 'E4', 'G4'][bar % 4],
      start_tick: start, duration_ticks: 960, velocity: 80,
    });
    bass.push({
      type: 'note', pitch: 'C2',
      start_tick: start, duration_ticks: BAR, velocity: 70,
    });
    accompaniment.push({
      type: 'note', pitch: 'E3',
      start_tick: start, duration_ticks: BAR, velocity: 55,
    });
  }
  return migrateV1ToV2({
    schema_version: 'composition.v1',
    tempo: 100,
    key: 'C major',
    time_signature: '4/4',
    ticks_per_quarter: 480,
    bar_count: 16,
    duration_ticks: 16 * BAR,
    sections: [{ type: 'verse', start_bar: 1, bar_count: 16, start_tick: 0, duration_ticks: 16 * BAR }],
    tracks: [
      {
        id: 'melody', name: 'Melody', instrument: 'piano', role: 'melody',
        midi_program: 0, channel: 1, events: melody,
      },
      {
        id: 'bass', name: 'Bass', instrument: 'bass', role: 'bass',
        midi_program: 32, channel: 2, events: bass,
      },
      {
        id: 'accompaniment', name: 'Pad', instrument: 'strings', role: 'harmony',
        midi_program: 48, channel: 3, events: accompaniment,
      },
    ],
    harmony: Array.from({ length: 16 }, (_, bar) => ({
      bar: bar + 1,
      chord: ['C', 'G', 'Am', 'F'][bar % 4],
    })),
  });
}

test('reharmonize preview does not dirty composition; apply preserves melody and undo restores', async (t) => {
  const composition = sixteenBarComposition();
  const melodyBefore = structuredClone(composition.tracks.find((track) => track.id === 'melody').events);
  const revision = 'rev-base-16';
  resetStore(composition);
  useMusicStore.setState({
    compositionRevision: revision,
    currentProjectId: 'project-harmony',
    lastSavedPersistRevision: 'persist-clean',
    saveStatus: 'saved',
    harmonySelectionStartBar: 9,
    harmonySelectionEndBar: 12,
    reharmonizeOperation: 'increase_tension',
    reharmonizeContentPolicy: 'preserve_melody_adapt_harmony',
    reharmonizeEngine: 'deterministic',
    reharmonizeTargetTrackIds: ['bass', 'accompaniment'],
    reharmonizeCandidate: null,
    reharmonizeStatus: 'idle',
  });

  const { compositionSourceFingerprint } = await import('../utils/compositionAnalysis.js');
  const { compositionRevisionKey } = await import('../utils/playbackPosition.js');
  const baseFingerprint = await compositionSourceFingerprint(composition);

  const candidate = structuredClone(composition);
  for (let bar = 8; bar < 12; bar += 1) {
    candidate.harmony[bar] = {
      start_tick: bar * BAR,
      duration_ticks: BAR,
      chord: 'E7(b9)',
    };
    const bassEvent = candidate.tracks.find((track) => track.id === 'bass').events[bar];
    bassEvent.pitch = 'E2';
  }

  const previousAdapter = axios.defaults.adapter;
  axios.defaults.adapter = async () => ({
    data: {
      base_fingerprint: baseFingerprint,
      proposal_fingerprint: 'd'.repeat(64),
      composition: candidate,
      harmony_changes: [{ kind: 'replaced', start_tick: 8 * BAR, duration_ticks: 4 * BAR, chord: 'E7(b9)' }],
      track_changes: [
        { track_id: 'bass', events_changed: 4, events_added: 0, events_removed: 0 },
        { track_id: 'accompaniment', events_changed: 0, events_added: 0, events_removed: 0 },
      ],
      preservation: [{ assertion: 'melody_events_exact', status: 'ok' }],
      compatibility: { status: 'compatible', findings: [] },
      provider: 'deterministic',
      model: null,
      warnings: [],
      start_tick: 8 * BAR,
      end_tick: 12 * BAR,
      active_key: 'C major',
      recommended_target_track_ids: ['bass', 'accompaniment'],
    },
    status: 200,
    statusText: 'OK',
    headers: {},
    config: {},
  });
  t.after(() => {
    axios.defaults.adapter = previousAdapter;
  });

  const previewOk = await useMusicStore.getState().startReharmonizePreview();
  assert.equal(previewOk, true);
  assert.equal(useMusicStore.getState().reharmonizeStatus, 'ready');
  assert.equal(useMusicStore.getState().saveStatus, 'saved');
  assert.deepEqual(
    useMusicStore.getState().editedMusicJson.tracks.find((track) => track.id === 'melody').events,
    melodyBefore,
  );
  assert.equal(useMusicStore.getState().compositionRevision, revision);

  const applyOk = await useMusicStore.getState().applyReharmonizePreview();
  assert.equal(applyOk, true);
  const after = useMusicStore.getState();
  assert.equal(after.reharmonizeStatus, 'idle');
  assert.equal(after.saveStatus, 'unsaved');
  assert.deepEqual(
    after.editedMusicJson.tracks.find((track) => track.id === 'melody').events,
    melodyBefore,
  );
  assert.equal(after.editedMusicJson.harmony[8].chord, 'E7(b9)');
  assert.equal(
    after.editedMusicJson.tracks.find((track) => track.id === 'bass').events[8].pitch,
    'E2',
  );
  assert.notEqual(after.compositionRevision, revision);
  assert.equal(after.compositionRevision, compositionRevisionKey(after.editedMusicJson));

  assert.equal(useMusicStore.getState().undoNoteEdit(), true);
  assert.deepEqual(useMusicStore.getState().editedMusicJson, composition);
});

test('harmony timeline edits create one undo entry and preserve note events', () => {
  const composition = sixteenBarComposition();
  const eventsBefore = structuredClone(composition.tracks.map((track) => track.events));
  resetStore(composition);
  useMusicStore.setState({ compositionRevision: 'rev-h' });

  assert.equal(
    useMusicStore.getState().addHarmonySpan({
      start_tick: 16 * BAR,
      duration_ticks: BAR,
      chord: 'Dm',
    }),
    false,
  );

  const ok = useMusicStore.getState().replaceHarmonyRange({
    start_tick: 8 * BAR,
    duration_ticks: 4 * BAR,
    spans: [
      { start_tick: 8 * BAR, duration_ticks: 2 * BAR, chord: 'E7(b9)' },
      { start_tick: 10 * BAR, duration_ticks: 2 * BAR, chord: 'A7' },
    ],
  });
  assert.equal(ok, true);
  const mid = useMusicStore.getState().editedMusicJson;
  assert.equal(mid.harmony.some((span) => span.chord === 'E7(b9)'), true);
  assert.deepEqual(mid.tracks.map((track) => track.events), eventsBefore);
  assert.equal(useMusicStore.getState().noteEditUndoStack.length, 1);

  assert.equal(useMusicStore.getState().undoNoteEdit(), true);
  assert.deepEqual(useMusicStore.getState().editedMusicJson.harmony, composition.harmony);
});