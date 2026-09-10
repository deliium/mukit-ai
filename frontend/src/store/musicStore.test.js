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
    currentProjectId: null,
    activeBranchId: null,
    workingVersion: null,
    workingFingerprint: null,
    currentRevisionId: null,
    generationStatus: 'idle',
    generationCandidate: null,
    generationAuditionActive: false,
    generationCompareResult: null,
    generationRequestCapture: null,
    generationMeta: null,
    generatedMusicJson: composition,
    editedMusicJson: composition,
    musicXml: '',
    compositionRevision: 'test',
    trackControls: {},
    pianoRollTrackId: 'melody-1',
    pianoRollNoteId: null,
    pianoRollNoteIds: [],
    editorSelectionRefs: [],
    editorSelectionPrimary: null,
    editorSelectionAnchor: null,
    editorClipboard: null,
    editorCommandFeedback: null,
    hiddenTrackIds: [],
    lockedTrackIds: [],
    pianoRollSnap: '1/8',
    pianoRollZoom: 0.05,
    pianoRollEditStatus: 'idle',
    pianoRollNotationStatus: 'idle',
    pianoRollNotationError: '',
    compositionEditUndoStack: [],
    compositionEditRedoStack: [],
    editCursorTick: 0,
    viewportScrollRequest: null,
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
    playbackLoop: null,
    playbackAutoFollow: false,
    playbackTransportIntent: null,
    uiError: '',
    warnings: [],
    prompt: {
      genre: 'ambient',
      mood: 'cinematic',
      key: '',
      time_signature: '4/4',
      tempo_min: 80,
      tempo_max: 120,
      instruments: 'piano',
      sections: 'intro:4',
      complexity: 'moderate',
      duration_bars: 8,
      instructions: '',
    },
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

  assert.equal(store.undoCompositionEdit(), true);
  assert.equal(useMusicStore.getState().editedMusicJson.tracks[0].events.length, 3);
  assert.equal(store.redoCompositionEdit(), true);
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
  assert.equal(useMusicStore.getState().compositionEditUndoStack.length, 1);
  store.updateNote('melody-1', 'n1', { start_tick: 480 }, { skipHistory: true });
  store.updateNote('melody-1', 'n1', { start_tick: 720 }, { skipHistory: true });
  assert.equal(useMusicStore.getState().compositionEditUndoStack.length, 1);
  assert.equal(
    useMusicStore.getState().editedMusicJson.tracks[0].events.find((event) => event.id === 'n1').start_tick,
    720,
  );
});

test('AI edit failure leaves composition and history unchanged', async () => {
  resetStore();
  const before = structuredClone(useMusicStore.getState().editedMusicJson);
  const store = useMusicStore.getState();
  store.setAiEditSelection({ startBar: 1, endBar: 2, trackMode: 'current' });
  store.setAiEditInstruction('make this phrase more dramatic but keep the harmony');
  assert.equal(await store.startAiEdit(), true);
  store.failAiEdit('provider failed');
  const after = useMusicStore.getState();
  assert.deepEqual(after.editedMusicJson, before);
  assert.equal(after.compositionEditUndoStack.length, 0);
  assert.equal(after.compositionEditRedoStack.length, 0);
  assert.equal(after.aiEditStatus, 'error');
  assert.match(after.aiEditError, /provider failed/);
});

test('AI edit success stages candidate then Apply pushes one undo snapshot', async () => {
  resetStore();
  useMusicStore.setState({ currentProjectId: null });
  const store = useMusicStore.getState();
  const before = structuredClone(useMusicStore.getState().editedMusicJson);
  store.setAiEditSelection({ startBar: 1, endBar: 1, trackMode: 'current' });
  store.setAiEditInstruction('make this phrase more dramatic but keep the harmony');
  assert.equal(await store.startAiEdit(), true);

  const edited = structuredClone(before);
  edited.tracks[0].events = [
    { type: 'note', id: 'n1', pitch: 'G4', start_tick: 0, duration_ticks: 480, velocity: 100 },
    { type: 'note', id: 'n2', pitch: 'E4', start_tick: 0, duration_ticks: 480, velocity: 88 },
  ];
  assert.equal(await store.completeAiEdit({ composition: edited, musicxml: '<score/>', warnings: ['ok'] }), true);

  const staged = useMusicStore.getState();
  assert.deepEqual(staged.editedMusicJson, before);
  assert.equal(staged.aiEditStatus, 'success');
  assert.ok(staged.aiEditCandidate);
  assert.equal(staged.compositionEditUndoStack.length, 0);

  assert.equal(await store.applyAiEditCandidate(), true);
  const afterApply = useMusicStore.getState();
  assert.equal(afterApply.compositionEditUndoStack.length, 1);
  assert.equal(afterApply.aiEditCandidate, null);
  assert.equal(afterApply.editedMusicJson.tracks[0].events[0].pitch, 'G4');
  assert.equal(afterApply.playbackStatus, 'idle');

  assert.equal(store.undoCompositionEdit(), true);
  assert.equal(useMusicStore.getState().editedMusicJson.tracks[0].events[0].pitch, 'C4');
  assert.equal(store.redoCompositionEdit(), true);
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

  assert.equal(store.undoCompositionEdit(), true);
  assert.deepEqual(
    useMusicStore.getState().editedMusicJson.tracks[0].events[0].articulations,
    [],
  );
  assert.equal(store.redoCompositionEdit(), true);
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
    currentProjectId: null,
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

  const { compositionEditFingerprint } = await import('../utils/compositionCandidates.js');
  const { compositionRevisionKey } = await import('../utils/playbackPosition.js');
  const baseFingerprint = await compositionEditFingerprint(composition);

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
  const proposalFingerprint = await compositionEditFingerprint(candidate);

  const previousAdapter = axios.defaults.adapter;
  axios.defaults.adapter = async () => ({
    data: {
      base_fingerprint: baseFingerprint,
      proposal_fingerprint: proposalFingerprint,
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
  assert.equal(after.compositionEditUndoStack.length, 1);
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

  assert.equal(useMusicStore.getState().undoCompositionEdit(), true);
  assert.deepEqual(useMusicStore.getState().editedMusicJson, composition);
});

test('reharmonize reject and compare leave working composition unchanged', async (t) => {
  const composition = sixteenBarComposition();
  const revision = 'rev-reject-compare';
  resetStore(composition);
  useMusicStore.setState({
    compositionRevision: revision,
    saveStatus: 'saved',
    harmonySelectionStartBar: 9,
    harmonySelectionEndBar: 12,
    reharmonizeTargetTrackIds: ['bass', 'accompaniment'],
    reharmonizeContentPolicy: 'preserve_melody_adapt_harmony',
    reharmonizeEngine: 'deterministic',
  });
  const { compositionEditFingerprint } = await import('../utils/compositionCandidates.js');
  const candidate = structuredClone(composition);
  candidate.harmony[8] = {
    start_tick: 8 * BAR,
    duration_ticks: BAR,
    chord: 'E7(b9)',
  };
  const baseFingerprint = await compositionEditFingerprint(composition);
  const proposalFingerprint = await compositionEditFingerprint(candidate);
  const previousAdapter = axios.defaults.adapter;
  axios.defaults.adapter = async () => ({
    data: {
      base_fingerprint: baseFingerprint,
      proposal_fingerprint: proposalFingerprint,
      composition: candidate,
      harmony_changes: [{ kind: 'replaced', start_tick: 8 * BAR, duration_ticks: 4 * BAR, chord: 'E7(b9)' }],
      track_changes: [],
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

  assert.equal(await useMusicStore.getState().startReharmonizePreview(), true);
  const before = structuredClone(useMusicStore.getState().editedMusicJson);
  const compare = useMusicStore.getState().refreshReharmonizeComparison();
  assert.ok(compare);
  assert.equal(compare.identical, false);
  assert.ok(useMusicStore.getState().reharmonizeCompareResult);
  assert.equal(useMusicStore.getState().setReharmonizeAuditionActive(true), true);
  assert.equal(useMusicStore.getState().reharmonizeAuditionActive, true);
  assert.equal(useMusicStore.getState().rejectReharmonizePreview(), true);
  assert.equal(useMusicStore.getState().reharmonizeCandidate, null);
  assert.equal(useMusicStore.getState().reharmonizeCompareResult, null);
  assert.equal(useMusicStore.getState().reharmonizeAuditionActive, false);
  assert.deepEqual(useMusicStore.getState().editedMusicJson, before);
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
  assert.equal(useMusicStore.getState().compositionEditUndoStack.length, 1);

  assert.equal(useMusicStore.getState().undoCompositionEdit(), true);
  assert.deepEqual(useMusicStore.getState().editedMusicJson.harmony, composition.harmony);
});

test('failed validation leaves composition and history untouched', () => {
  resetStore();
  const before = useMusicStore.getState().editedMusicJson;
  const undoBefore = useMusicStore.getState().compositionEditUndoStack.length;
  useMusicStore.getState().selectPianoRollNote('n1');
  useMusicStore.setState({ editCursorTick: 960 });

  const rejected = useMusicStore.getState().updateNote('melody-1', 'n1', { pitch: 'not-a-pitch' });
  assert.equal(rejected, null);
  assert.equal(useMusicStore.getState().editedMusicJson, before);
  assert.equal(useMusicStore.getState().compositionEditUndoStack.length, undoBefore);
  assert.equal(useMusicStore.getState().editCursorTick, 960);
});

test('valid JSON edit records one history entry; invalid JSON does not', () => {
  resetStore();
  useMusicStore.setState({ editCursorTick: 480 });
  const before = useMusicStore.getState().editedMusicJson;

  const valid = structuredClone(before);
  valid.tempo = 110;
  useMusicStore.getState().setEditedMusicJson(valid);
  assert.equal(useMusicStore.getState().compositionEditUndoStack.length, 1);
  assert.equal(useMusicStore.getState().editedMusicJson.tempo, 110);
  assert.equal(useMusicStore.getState().editCursorTick, 480);
  assert.equal(useMusicStore.getState().compositionEditRedoStack.length, 0);

  const invalid = structuredClone(useMusicStore.getState().editedMusicJson);
  invalid.tracks[0].events[0].velocity = 999;
  useMusicStore.getState().setEditedMusicJson(invalid);
  assert.equal(useMusicStore.getState().compositionEditUndoStack.length, 1);
  assert.equal(useMusicStore.getState().editedMusicJson.tracks[0].events[0].velocity, 999);

  assert.equal(useMusicStore.getState().undoCompositionEdit(), true);
  assert.equal(useMusicStore.getState().editedMusicJson.tempo, 100);
  assert.equal(useMusicStore.getState().editCursorTick, 480);
});

test('new transaction clears redo stack and bounds undo to 50 entries', () => {
  resetStore();
  const store = useMusicStore.getState();
  for (let i = 0; i < 52; i += 1) {
    store.createNote('melody-1', {
      pitch: 'C5',
      start_tick: Math.min(3000, i * 10),
      duration_ticks: 60,
    });
  }
  assert.equal(useMusicStore.getState().compositionEditUndoStack.length, 50);

  assert.equal(useMusicStore.getState().undoCompositionEdit(), true);
  assert.equal(useMusicStore.getState().compositionEditRedoStack.length, 1);

  useMusicStore.getState().createNote('melody-1', {
    pitch: 'D5',
    start_tick: 120,
    duration_ticks: 60,
  });
  assert.equal(useMusicStore.getState().compositionEditRedoStack.length, 0);
  assert.ok(useMusicStore.getState().compositionEditUndoStack.length <= 50);
});

test('undo restores selection and composition transaction invalidates arrangement preview', () => {
  resetStore();
  useMusicStore.setState({
    arrangementStatus: 'ready',
    arrangementCandidates: [{ candidate_id: 'c1' }],
    arrangementSelectedCandidateId: 'c1',
    editCursorTick: 720,
  });
  const store = useMusicStore.getState();
  store.selectPianoRollNote('n1');
  const created = store.createNote('melody-1', {
    pitch: 'F4',
    start_tick: 960,
    duration_ticks: 240,
  });
  assert.ok(created?.id);
  assert.equal(useMusicStore.getState().pianoRollNoteId, created.id);
  assert.equal(useMusicStore.getState().editCursorTick, 720);
  assert.notEqual(useMusicStore.getState().arrangementStatus, 'ready');

  assert.equal(store.undoCompositionEdit(), true);
  assert.equal(useMusicStore.getState().pianoRollNoteId, 'n1');
  assert.equal(useMusicStore.getState().editCursorTick, 720);
});

test('composition transaction marks project dirty for autosave revision', () => {
  resetStore();
  useMusicStore.setState({
    currentProjectId: 'project-txn',
    lastSavedPersistRevision: 'persist-clean',
    saveStatus: 'saved',
  });
  const revisionBefore = useMusicStore.getState().compositionRevision;
  useMusicStore.getState().createNote('melody-1', {
    pitch: 'B4',
    start_tick: 1440,
    duration_ticks: 120,
  });
  const after = useMusicStore.getState();
  assert.equal(after.saveStatus, 'unsaved');
  assert.notEqual(after.compositionRevision, revisionBefore);
});

test('editor selection supports multi-track refs toggle and range clear', () => {
  resetStore();
  const store = useMusicStore.getState();
  store.setEditorSelection({
    refs: [{ trackId: 'melody-1', eventId: 'n1' }],
    primary: { trackId: 'melody-1', eventId: 'n1' },
  });
  store.toggleEditorSelectionRef({ trackId: 'bass-1', eventId: 'b1' });
  let state = useMusicStore.getState();
  assert.equal(state.editorSelectionRefs.length, 2);
  assert.equal(state.pianoRollTrackId, 'bass-1');
  assert.equal(state.pianoRollNoteId, 'b1');

  store.extendEditorSelectionTo({ trackId: 'melody-1', eventId: 'n2' });
  state = useMusicStore.getState();
  assert.ok(state.editorSelectionRefs.length >= 2);

  store.clearEditorSelection();
  state = useMusicStore.getState();
  assert.deepEqual(state.editorSelectionRefs, []);
  assert.equal(state.pianoRollNoteId, null);
});

test('clipboard copy/cut/paste/duplicate/delete use one history entry each', () => {
  resetStore();
  const store = useMusicStore.getState();
  store.setEditorSelection({
    refs: [
      { trackId: 'melody-1', eventId: 'n1' },
      { trackId: 'melody-1', eventId: 'n2' },
    ],
    primary: { trackId: 'melody-1', eventId: 'n1' },
  });

  const copied = store.copySelection();
  assert.equal(copied.ok, true);
  assert.ok(useMusicStore.getState().editorClipboard);
  assert.equal(useMusicStore.getState().compositionEditUndoStack.length, 0);

  useMusicStore.getState().setEditorSelection({
    refs: [{ trackId: 'melody-1', eventId: 'n1' }],
    primary: { trackId: 'melody-1', eventId: 'n1' },
  });
  const duplicated = useMusicStore.getState().duplicateSelection();
  assert.equal(duplicated.ok, true);
  assert.equal(useMusicStore.getState().compositionEditUndoStack.length, 1);
  assert.ok(useMusicStore.getState().editorSelectionRefs.length >= 1);

  useMusicStore.setState({ editCursorTick: 1920 });
  const pasted = useMusicStore.getState().pasteClipboard();
  assert.equal(pasted.ok, true);
  assert.equal(useMusicStore.getState().compositionEditUndoStack.length, 2);

  const beforeCutEvents = useMusicStore.getState().editedMusicJson.tracks[0].events.length;
  useMusicStore.getState().setEditorSelection({
    refs: useMusicStore.getState().editorSelectionRefs.slice(0, 1),
    primary: useMusicStore.getState().editorSelectionRefs[0],
  });
  const cut = useMusicStore.getState().cutSelection();
  assert.equal(cut.ok, true);
  assert.equal(useMusicStore.getState().compositionEditUndoStack.length, 3);
  assert.ok(
    useMusicStore.getState().editedMusicJson.tracks[0].events.length < beforeCutEvents,
  );

  useMusicStore.getState().selectAllVisible();
  const deleted = useMusicStore.getState().deleteSelection();
  assert.equal(deleted.ok, true);
  assert.equal(useMusicStore.getState().compositionEditUndoStack.length, 4);
  assert.equal(useMusicStore.getState().editorSelectionRefs.length, 0);
});

test('locked and empty selection guards reject mutating clipboard commands', () => {
  resetStore();
  const store = useMusicStore.getState();
  assert.equal(store.cutSelection().ok, false);
  assert.equal(store.pasteClipboard().ok, false);
  assert.equal(useMusicStore.getState().editorCommandFeedback?.code, 'empty_clipboard');

  store.setEditorSelection({
    refs: [{ trackId: 'melody-1', eventId: 'n1' }],
    primary: { trackId: 'melody-1', eventId: 'n1' },
  });
  store.setLockedTrackIds(['melody-1']);
  const cut = store.cutSelection();
  assert.equal(cut.ok, false);
  assert.equal(cut.code, 'locked_targets');
  assert.equal(useMusicStore.getState().compositionEditUndoStack.length, 0);

  store.copySelection();
  store.setLockedTrackIds(['melody-1']);
  const paste = store.pasteClipboard();
  assert.equal(paste.ok, false);
  assert.equal(paste.code, 'locked_targets');
});

test('editor prefs hide/lock reconcile unknown ids and enforce locks on transforms', () => {
  resetStore();
  const store = useMusicStore.getState();
  store.setHiddenTrackIds(['melody-1', 'ghost-track']);
  store.setLockedTrackIds(['bass-1', 'missing-track']);
  assert.deepEqual(useMusicStore.getState().hiddenTrackIds, ['melody-1']);
  assert.deepEqual(useMusicStore.getState().lockedTrackIds, ['bass-1']);

  store.setEditorSelection({
    refs: [{ trackId: 'bass-1', eventId: 'b1' }],
    primary: { trackId: 'bass-1', eventId: 'b1' },
  });
  assert.equal(store.transposeSelection(1).ok, false);
  assert.equal(store.quantizeSelection({ strength: 100 }).ok, false);
  assert.equal(store.setSelectionVelocity(80).ok, false);
  assert.equal(useMusicStore.getState().compositionEditUndoStack.length, 0);

  store.toggleLockedTrackId('bass-1');
  assert.deepEqual(useMusicStore.getState().lockedTrackIds, []);
  assert.equal(store.transposeSelection(1).ok, true);
  assert.equal(useMusicStore.getState().compositionEditUndoStack.length, 1);
});

test('bulk expression transforms commit one history entry each', () => {
  resetStore();
  const store = useMusicStore.getState();
  store.setEditorSelection({
    refs: [
      { trackId: 'melody-1', eventId: 'n1' },
      { trackId: 'melody-1', eventId: 'n2' },
    ],
    primary: { trackId: 'melody-1', eventId: 'n1' },
  });

  assert.equal(store.quantizeSelection({ mode: 'start', strength: 100 }).ok, true);
  assert.equal(store.setSelectionVelocity(70).ok, true);
  assert.equal(store.deltaSelectionVelocity(5).ok, true);
  assert.equal(store.setSelectionNoteLength({ toGrid: true }).ok, true);
  assert.equal(store.nudgeSelectionNoteLength(1).ok, true);
  assert.equal(store.setSelectionNoteLength({ quantizeEnds: true, strength: 100 }).ok, true);
  assert.equal(store.humanizeSelection({
    timingAmount: 4,
    velocityAmount: 3,
    random: () => 0.25,
  }).ok, true);
  assert.equal(store.setSelectionArticulation('accent', 'set').ok, true);
  assert.equal(useMusicStore.getState().compositionEditUndoStack.length, 8);

  const events = useMusicStore.getState().editedMusicJson.tracks[0].events;
  assert.ok(events.every((event) => event.velocity >= 1 && event.velocity <= 127));
  assert.ok(events.some((event) => (event.articulations || []).includes('accent')));
});

test('dynamics upsert/remove use history and stay sorted unique', () => {
  resetStore();
  const store = useMusicStore.getState();
  useMusicStore.setState({ editCursorTick: 480, aiEditStartBar: 2 });

  assert.equal(store.upsertDynamicMark({ level: 'mf', at: 'cursor' }).ok, true);
  assert.equal(store.upsertDynamicMark({ level: 'f', at: 'bar' }).ok, true);
  assert.equal(store.upsertDynamicMark({ level: 'p', tick: 0 }).ok, true);
  let marks = useMusicStore.getState().editedMusicJson.tracks[0].dynamic_marks;
  assert.deepEqual(marks.map((mark) => mark.tick), [0, 480, 1920]);
  assert.deepEqual(marks.map((mark) => mark.level), ['p', 'mf', 'f']);
  assert.equal(useMusicStore.getState().compositionEditUndoStack.length, 3);

  assert.equal(store.upsertDynamicMark({ level: 'mp', tick: 480 }).ok, true);
  marks = useMusicStore.getState().editedMusicJson.tracks[0].dynamic_marks;
  assert.equal(marks.find((mark) => mark.tick === 480).level, 'mp');
  assert.equal(marks.filter((mark) => mark.tick === 480).length, 1);

  assert.equal(store.removeDynamicMark({ tick: 480 }).ok, true);
  marks = useMusicStore.getState().editedMusicJson.tracks[0].dynamic_marks;
  assert.deepEqual(marks.map((mark) => mark.tick), [0, 1920]);

  assert.equal(store.undoCompositionEdit(), true);
  marks = useMusicStore.getState().editedMusicJson.tracks[0].dynamic_marks;
  assert.ok(marks.some((mark) => mark.tick === 480));
});

test('generation apply and import reconcile hidden/locked track prefs', async () => {
  resetStore();
  useMusicStore.setState({
    hiddenTrackIds: ['melody-1', 'stale-hidden'],
    lockedTrackIds: ['bass-1', 'stale-locked'],
    editorSelectionRefs: [{ trackId: 'melody-1', eventId: 'n1' }],
    editorClipboard: { version: 1, notes: [] },
  });

  const next = structuredClone(BASE);
  next.tracks = next.tracks.filter((track) => track.id === 'melody-1');
  await useMusicStore.getState().startGeneration();
  await useMusicStore.getState().completeGeneration({
    music: next,
    musicxml: '<score/>',
    warnings: [],
  });
  await useMusicStore.getState().applyGenerationCandidate();

  const afterGen = useMusicStore.getState();
  assert.deepEqual(afterGen.hiddenTrackIds, ['melody-1']);
  assert.deepEqual(afterGen.lockedTrackIds, []);
  assert.deepEqual(afterGen.editorSelectionRefs, []);
  assert.equal(afterGen.editorClipboard, null);

  useMusicStore.setState({
    hiddenTrackIds: ['melody-1', 'ghost'],
    lockedTrackIds: ['gone'],
  });
  const imported = structuredClone(BASE);
  imported.tracks = imported.tracks.filter((track) => track.id === 'bass-1');
  assert.equal(await useMusicStore.getState().completeImport({
    composition: imported,
    musicxml: '',
  }), true);
  const afterImport = useMusicStore.getState();
  assert.deepEqual(afterImport.hiddenTrackIds, []);
  assert.deepEqual(afterImport.lockedTrackIds, []);
});

test('articulation selection reports skipped tie-incompatible notes without partial violation', () => {
  resetStore();
  const composition = structuredClone(BASE);
  composition.tracks[0].events = [
    {
      type: 'note',
      id: 't1',
      pitch: 'C4',
      start_tick: 0,
      duration_ticks: 480,
      velocity: 90,
      articulations: [],
      tie: null,
    },
    {
      type: 'note',
      id: 't2',
      pitch: 'C4',
      start_tick: 480,
      duration_ticks: 480,
      velocity: 90,
      articulations: [],
      tie: null,
    },
    {
      type: 'note',
      id: 'free',
      pitch: 'E4',
      start_tick: 0,
      duration_ticks: 480,
      velocity: 88,
      articulations: [],
      tie: null,
    },
  ];
  useMusicStore.setState({
    editedMusicJson: composition,
    generatedMusicJson: composition,
  });
  const store = useMusicStore.getState();
  assert.equal(store.applyTieChain('melody-1', ['t1', 't2']), true);
  store.setEditorSelection({
    refs: [
      { trackId: 'melody-1', eventId: 't2' },
      { trackId: 'melody-1', eventId: 'free' },
    ],
    primary: { trackId: 'melody-1', eventId: 'free' },
  });

  const result = store.setSelectionArticulation('staccato', 'set');
  assert.equal(result.ok, true);
  assert.ok((result.summary?.skippedCount || 0) >= 1);
  const events = useMusicStore.getState().editedMusicJson.tracks[0].events;
  assert.deepEqual(events.find((event) => event.id === 'free').articulations, ['staccato']);
  assert.deepEqual(events.find((event) => event.id === 't2').articulations, []);
  assert.equal(useMusicStore.getState().editorCommandFeedback?.skippedCount, 1);
});

test('setEditCursorTick clamps without creating history', () => {
  resetStore();
  const store = useMusicStore.getState();
  const undoBefore = useMusicStore.getState().compositionEditUndoStack.length;
  assert.equal(store.setEditCursorTick(960), 960);
  assert.equal(useMusicStore.getState().editCursorTick, 960);
  assert.equal(store.setEditCursorTick(999999), BASE.duration_ticks);
  assert.equal(useMusicStore.getState().editCursorTick, BASE.duration_ticks);
  assert.equal(store.setEditCursorTick(-20), 0);
  assert.equal(useMusicStore.getState().compositionEditUndoStack.length, undoBefore);
});

test('bar and section navigation updates cursor and viewport request', () => {
  resetStore();
  const multi = structuredClone(BASE);
  multi.bar_count = 8;
  multi.duration_ticks = 8 * 1920;
  multi.sections = [
    {
      id: 'intro',
      type: 'intro',
      start_bar: 1,
      bar_count: 2,
      start_tick: 0,
      duration_ticks: 3840,
    },
    {
      id: 'verse',
      type: 'verse',
      start_bar: 3,
      bar_count: 6,
      start_tick: 3840,
      duration_ticks: 6 * 1920,
    },
  ];
  multi.harmony = [{ bar: 1, chord: 'C' }];
  useMusicStore.setState({
    editedMusicJson: multi,
    generatedMusicJson: multi,
    editCursorTick: 0,
  });

  const store = useMusicStore.getState();
  assert.equal(store.gotoNextBar(), 1920);
  assert.equal(useMusicStore.getState().editCursorTick, 1920);
  assert.ok(useMusicStore.getState().viewportScrollRequest?.id >= 1);
  assert.equal(useMusicStore.getState().viewportScrollRequest.centerTick, 1920);

  assert.equal(store.gotoBar(3), 3840);
  assert.equal(store.gotoPrevBar(), 1920);

  assert.equal(store.gotoSection('id:verse'), 3840);
  assert.equal(store.gotoNextSection(), 3840);
  assert.equal(store.gotoPrevSection(), 0);
  assert.equal(store.gotoSection('missing'), null);
});

test('variable-meter bar navigation uses compiled starts', () => {
  resetStore();
  const variable = structuredClone(BASE);
  variable.bar_count = 4;
  variable.duration_ticks = 6720;
  variable.time_signature_changes = [{ tick: 3840, time_signature: '3/4' }];
  variable.sections = [{
    type: 'intro',
    start_bar: 1,
    bar_count: 4,
    start_tick: 0,
    duration_ticks: 6720,
  }];
  variable.harmony = [{ bar: 1, chord: 'C' }];
  useMusicStore.setState({
    editedMusicJson: variable,
    generatedMusicJson: variable,
    editCursorTick: 0,
  });
  const store = useMusicStore.getState();
  assert.equal(store.gotoBar(3), 3840);
  assert.equal(store.gotoNextBar(), 5280);
  assert.equal(store.gotoPrevBar(), 3840);
});

test('zoom fit and selection update zoom plus viewport request', () => {
  resetStore();
  const store = useMusicStore.getState();
  store.setEditorSelection({
    refs: [{ trackId: 'melody-1', eventId: 'n1' }],
    primary: { trackId: 'melody-1', eventId: 'n1' },
  });

  const fitZoom = store.zoomToFit({ clientWidth: 400 });
  assert.ok(fitZoom >= 0.01 && fitZoom <= 0.25);
  assert.equal(useMusicStore.getState().pianoRollZoom, fitZoom);
  assert.equal(useMusicStore.getState().viewportScrollRequest.reason, 'zoomToFit');

  const selection = store.zoomToSelection({ clientWidth: 300 });
  assert.ok(selection);
  assert.equal(useMusicStore.getState().viewportScrollRequest.reason, 'zoomToSelection');
  assert.ok(useMusicStore.getState().pianoRollZoom >= 0.01);

  const beforeIn = useMusicStore.getState().pianoRollZoom;
  store.zoomIn();
  assert.ok(useMusicStore.getState().pianoRollZoom >= beforeIn);
  store.zoomOut();
});

test('composition replacement clears viewport request and resets cursor', async () => {
  resetStore();
  useMusicStore.setState({
    editCursorTick: 1200,
    viewportScrollRequest: { id: 9, scrollLeft: 40, centerTick: 1200, reason: 'gotoBar' },
  });
  await useMusicStore.getState().startGeneration();
  await useMusicStore.getState().completeGeneration({
    music: structuredClone(BASE),
    musicxml: '<score/>',
    warnings: [],
  });
  await useMusicStore.getState().applyGenerationCandidate();
  const after = useMusicStore.getState();
  assert.equal(after.editCursorTick, 0);
  assert.equal(after.viewportScrollRequest, null);

  useMusicStore.setState({
    editCursorTick: 800,
    viewportScrollRequest: { id: 10, scrollLeft: 10, centerTick: 800, reason: 'gotoBar' },
  });
  assert.equal(await useMusicStore.getState().completeImport({
    composition: structuredClone(BASE),
    musicxml: '',
  }), true);
  const afterImport = useMusicStore.getState();
  assert.equal(afterImport.editCursorTick, 0);
  assert.equal(afterImport.viewportScrollRequest, null);
});

test('playFromCursor and loop selection transport actions', () => {
  resetStore();
  const store = useMusicStore.getState();
  store.setEditCursorTick(960);
  const intent = store.playFromCursor();
  assert.equal(intent.type, 'play');
  assert.equal(intent.startTick, 960);
  assert.equal(useMusicStore.getState().playbackTransportIntent.startTick, 960);

  store.setEditorSelection({
    refs: [
      { trackId: 'melody-1', eventId: 'n1' },
      { trackId: 'melody-1', eventId: 'n2' },
    ],
  });
  const loop = store.setLoopFromSelection();
  assert.ok(loop);
  assert.equal(loop.startTick, 0);
  assert.equal(loop.endTick, 480);
  assert.equal(loop.enabled, true);

  store.setPlaybackLoopEnabled(false);
  assert.equal(useMusicStore.getState().playbackLoop.enabled, false);
  store.setPlaybackLoopEnabled(true);
  assert.equal(useMusicStore.getState().playbackLoop.enabled, true);

  store.clearEditorSelection();
  store.setAiEditSelection({ startBar: 2, endBar: 2 });
  const barLoop = store.setLoopFromSelection();
  assert.ok(barLoop);
  assert.equal(barLoop.startTick, 1920);
  assert.equal(barLoop.endTick, 3840);

  store.clearPlaybackLoop();
  assert.equal(useMusicStore.getState().playbackLoop, null);
  store.clearAiEditSelection();
  store.clearEditorSelection();
  assert.equal(store.setLoopFromSelection(), null);

  useMusicStore.setState({ playbackStatus: 'playing' });
  const pauseIntent = store.togglePlaybackTransport();
  assert.equal(pauseIntent.type, 'pause');
  useMusicStore.setState({ playbackStatus: 'paused' });
  assert.equal(store.togglePlaybackTransport().type, 'resume');
  useMusicStore.setState({ playbackStatus: 'idle' });
  assert.equal(store.togglePlaybackTransport().type, 'play');
  assert.equal(store.togglePlaybackTransport().startTick, null);
});

test('composition edits clamp or clear stale playback loops', async () => {
  resetStore();
  const store = useMusicStore.getState();
  store.setPlaybackLoop({ startTick: 0, endTick: 3000, enabled: true });
  assert.equal(useMusicStore.getState().playbackLoop.endTick, 3000);

  // Direct reconcile path used by transactions/replacements.
  const clamped = store.setPlaybackLoop(
    reconcileViaStore(store, { startTick: 0, endTick: 3000, enabled: true }, 1920),
  );
  assert.ok(clamped);
  assert.equal(clamped.endTick, 1920);

  assert.equal(
    store.setPlaybackLoop(
      reconcileViaStore(store, { startTick: 1800, endTick: 1900, enabled: true }, 1000),
    ),
    null,
  );
  assert.equal(useMusicStore.getState().playbackLoop, null);

  store.setPlaybackLoop({ startTick: 0, endTick: 480, enabled: true });
  await store.startGeneration();
  await store.completeGeneration({
    music: structuredClone(BASE),
    musicxml: '<score/>',
    warnings: [],
  });
  await store.applyGenerationCandidate();
  assert.equal(useMusicStore.getState().playbackLoop, null);
});

function reconcileViaStore(store, loop, durationTicks) {
  // Force clamp against a synthetic duration without needing a full valid rewrite.
  const composition = {
    ...useMusicStore.getState().editedMusicJson,
    duration_ticks: durationTicks,
  };
  useMusicStore.setState({ editedMusicJson: composition });
  return store.setPlaybackLoop(loop);
}
