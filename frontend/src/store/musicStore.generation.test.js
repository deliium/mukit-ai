import assert from 'node:assert/strict';
import test from 'node:test';

import axios from 'axios';
import { migrateV1ToV2 } from '../utils/compositionVersion.js';
import { useMusicStore } from './musicStore.js';

const BASE = migrateV1ToV2({
  schema_version: 'composition.v1',
  tempo: 100,
  key: 'C major',
  time_signature: '4/4',
  ticks_per_quarter: 480,
  bar_count: 2,
  duration_ticks: 3840,
  sections: [{ type: 'intro', start_bar: 1, bar_count: 2, start_tick: 0, duration_ticks: 3840 }],
  tracks: [
    {
      id: 'melody-1',
      name: 'Melody',
      instrument: 'piano',
      role: 'melody',
      midi_program: 0,
      channel: 1,
      events: [{ type: 'note', id: 'n1', pitch: 'C4', start_tick: 0, duration_ticks: 480, velocity: 90 }],
    },
  ],
  harmony: [{ bar: 1, chord: 'C' }],
});

const GENERATED = migrateV1ToV2({
  schema_version: 'composition.v1',
  tempo: 110,
  key: 'C major',
  time_signature: '4/4',
  ticks_per_quarter: 480,
  bar_count: 4,
  duration_ticks: 7680,
  sections: [{ type: 'verse', start_bar: 1, bar_count: 4, start_tick: 0, duration_ticks: 7680 }],
  tracks: [
    {
      id: 'melody-1',
      name: 'Melody',
      instrument: 'piano',
      role: 'melody',
      midi_program: 0,
      channel: 1,
      events: [
        { type: 'note', id: 'g1', pitch: 'E4', start_tick: 0, duration_ticks: 480, velocity: 90 },
        { type: 'note', id: 'g2', pitch: 'G4', start_tick: 480, duration_ticks: 480, velocity: 90 },
      ],
    },
  ],
  harmony: [{ bar: 1, chord: 'C' }],
});

function installAxiosStub(handler) {
  const previous = axios.defaults.adapter;
  axios.defaults.adapter = handler;
  return () => {
    axios.defaults.adapter = previous;
  };
}

function resetGenerationState(overrides = {}) {
  useMusicStore.setState({
    generationStatus: 'idle',
    uiError: '',
    warnings: [],
    selectedProvider: 'openai',
    selectedModel: 'gpt-test',
    availableLlmModels: [
      { provider: 'openai', model: 'gpt-test', display_name: 'OpenAI test', is_default: true },
    ],
    llmModelsLoaded: true,
    aiEditStatus: 'idle',
    aiEditError: '',
    aiEditWarnings: [],
    aiEditStartBar: 1,
    aiEditEndBar: 2,
    aiEditInstruction: 'make brighter',
    currentProjectId: null,
    activeBranchId: null,
    workingVersion: null,
    workingFingerprint: null,
    currentRevisionId: null,
    generationCandidate: null,
    generationAuditionActive: false,
    generationCompareResult: null,
    generationRequestCapture: null,
    generationMeta: null,
    editedMusicJson: structuredClone(BASE),
    generatedMusicJson: null,
    compositionRevision: 'base-rev',
    compositionEditUndoStack: [],
    compositionEditRedoStack: [],
    saveStatus: 'saved',
    lastSavedPersistRevision: 'saved',
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
      instructions: 'keep soft',
    },
    ...overrides,
  });
}

test('startGeneration is ignored while already loading', async () => {
  resetGenerationState({ generationStatus: 'loading' });
  const started = await useMusicStore.getState().startGeneration();
  assert.equal(started, false);
  assert.equal(useMusicStore.getState().generationStatus, 'loading');
});

test('startGeneration transitions idle to loading', async () => {
  resetGenerationState({ generationStatus: 'idle' });
  const started = await useMusicStore.getState().startGeneration();
  assert.equal(started, true);
  assert.equal(useMusicStore.getState().generationStatus, 'loading');
  assert.equal(useMusicStore.getState().uiError, '');
  assert.ok(useMusicStore.getState().generationRequestCapture);
});

test('completeGeneration stages candidate without mutating working composition', async () => {
  resetGenerationState();
  await useMusicStore.getState().startGeneration();
  const before = structuredClone(useMusicStore.getState().editedMusicJson);
  const ok = await useMusicStore.getState().completeGeneration({
    music: structuredClone(GENERATED),
    musicxml: '<score/>',
    warnings: ['soft'],
    provider: 'fake',
    model: 'fake-deterministic',
  });
  assert.equal(ok, true);
  const state = useMusicStore.getState();
  assert.deepEqual(state.editedMusicJson, before);
  assert.equal(state.generationStatus, 'success');
  assert.ok(state.generationCandidate);
  assert.equal(state.generationCandidate.composition.tempo, 110);
  assert.equal(state.generationCandidate.provider, 'fake');
});

test('rejectGenerationCandidate clears preview without touching working', async () => {
  resetGenerationState();
  await useMusicStore.getState().startGeneration();
  await useMusicStore.getState().completeGeneration({
    music: structuredClone(GENERATED),
    provider: 'fake',
    model: 'fake-deterministic',
  });
  const before = structuredClone(useMusicStore.getState().editedMusicJson);
  useMusicStore.getState().rejectGenerationCandidate();
  const state = useMusicStore.getState();
  assert.equal(state.generationCandidate, null);
  assert.deepEqual(state.editedMusicJson, before);
  assert.equal(state.generationStatus, 'idle');
});

test('applyGenerationCandidate local-only installs with one undo entry', async () => {
  resetGenerationState({ currentProjectId: null });
  await useMusicStore.getState().startGeneration();
  await useMusicStore.getState().completeGeneration({
    music: structuredClone(GENERATED),
    provider: 'fake',
    model: 'fake-deterministic',
  });
  const undoBefore = useMusicStore.getState().compositionEditUndoStack.length;
  const ok = await useMusicStore.getState().applyGenerationCandidate();
  assert.equal(ok, true);
  const state = useMusicStore.getState();
  assert.equal(state.editedMusicJson.tempo, 110);
  assert.equal(state.generationCandidate, null);
  assert.equal(state.compositionEditUndoStack.length, undoBefore + 1);
  assert.equal(state.generationMeta.provider, 'fake');
});

test('applyGenerationCandidate durable commit for open project', async (t) => {
  const calls = [];
  const restore = installAxiosStub(async (config) => {
    calls.push({ method: config.method, url: config.url, data: config.data });
    return {
      data: {
        project_id: 'p1',
        active_branch_id: 'b1',
        active_branch_name: 'Original',
        current_revision_id: 'r2',
        current_revision_sequence: 2,
        working_version: 2,
        working_fingerprint: 'composition.snapshot.v1:abcdef0123456789abcd',
        composition: structuredClone(GENERATED),
        revision_created: true,
        created_revision_ids: ['r2'],
        operation_type: 'generate-apply',
      },
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    };
  });
  t.after(restore);

  resetGenerationState({
    currentProjectId: 'p1',
    activeBranchId: 'b1',
    activeBranchName: 'Original',
    currentRevisionId: 'r1',
    workingVersion: 1,
    workingFingerprint: 'composition.snapshot.v1:0123456789abcdef0123',
    compositionRevision: 'base-rev',
  });
  await useMusicStore.getState().startGeneration();
  await useMusicStore.getState().completeGeneration({
    music: structuredClone(GENERATED),
    provider: 'fake',
    model: 'fake-deterministic',
  });
  const before = structuredClone(useMusicStore.getState().editedMusicJson);
  assert.equal(before.tempo, 100);
  const ok = await useMusicStore.getState().applyGenerationCandidate();
  assert.equal(ok, true);
  assert.equal(useMusicStore.getState().editedMusicJson.tempo, 110);
  assert.equal(useMusicStore.getState().currentRevisionId, 'r2');
  assert.equal(useMusicStore.getState().saveStatus, 'saved');
  assert.ok(calls.some((item) => String(item.url).endsWith('/revisions')));
  const body = typeof calls[0].data === 'string' ? JSON.parse(calls[0].data) : calls[0].data;
  assert.equal(body.operation_type, 'generate-apply');
  assert.equal(body.checkpoint_dirty_draft, true);
});

test('source edit during generation rejects stale completeGeneration', async () => {
  resetGenerationState();
  await useMusicStore.getState().startGeneration();
  useMusicStore.setState({ compositionRevision: 'edited-during-request' });
  const ok = await useMusicStore.getState().completeGeneration({
    music: structuredClone(GENERATED),
    provider: 'fake',
    model: 'fake-deterministic',
  });
  assert.equal(ok, false);
  assert.equal(useMusicStore.getState().generationCandidate, null);
  assert.equal(useMusicStore.getState().generationStatus, 'error');
  assert.equal(useMusicStore.getState().editedMusicJson.tempo, 100);
});

test('empty models leave llmReady false after setAvailableLlmModels', () => {
  resetGenerationState({ availableLlmModels: [{ provider: 'openai', model: 'x' }], llmModelsLoaded: false });
  useMusicStore.getState().setAvailableLlmModels([]);
  const state = useMusicStore.getState();
  assert.equal(state.llmModelsLoaded, true);
  assert.equal(state.availableLlmModels.length, 0);
});

test('startAiEdit is ignored while already loading', async () => {
  resetGenerationState({ aiEditStatus: 'loading' });
  const started = await useMusicStore.getState().startAiEdit();
  assert.equal(started, false);
  assert.equal(useMusicStore.getState().aiEditStatus, 'loading');
});
