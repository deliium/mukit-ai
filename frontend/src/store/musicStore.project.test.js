import assert from 'node:assert/strict';
import test from 'node:test';

import axios from 'axios';
import { AUTOSAVE_DEBOUNCE_MS, useMusicStore } from './musicStore.js';

const COMPOSITION = {
  schema_version: 'composition.v1',
  tempo: 100,
  key: 'C major',
  time_signature: '4/4',
  ticks_per_quarter: 480,
  bar_count: 1,
  duration_ticks: 1920,
  sections: [
    { type: 'intro', start_bar: 1, bar_count: 1, start_tick: 0, duration_ticks: 1920 },
  ],
  tracks: [
    {
      id: 'piano-1',
      name: 'Piano',
      instrument: 'piano',
      role: 'harmony',
      midi_program: 0,
      channel: 1,
      events: [
        { type: 'note', id: 'n1', pitch: 'C4', start_tick: 0, duration_ticks: 480, velocity: 80 },
      ],
    },
  ],
  harmony: [{ bar: 1, chord: 'C' }],
};

function installAxiosStub(handler) {
  const previousAdapter = axios.defaults.adapter;
  axios.defaults.adapter = handler;
  return () => {
    axios.defaults.adapter = previousAdapter;
  };
}

function resetProjectState(overrides = {}) {
  useMusicStore.setState({
    activeView: 'home',
    currentProjectId: null,
    currentProjectName: '',
    projectList: [],
    projectListStatus: 'idle',
    saveStatus: 'saved',
    saveError: '',
    lastSavedRevision: 'empty',
    compositionRevision: 'empty',
    generationMeta: null,
    generatedMusicJson: null,
    editedMusicJson: null,
    musicXml: '',
    uiError: '',
    noteEditUndoStack: [],
    noteEditRedoStack: [],
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
    ...overrides,
  });
}

test('open project hydrates composition and generation metadata without keys', async (t) => {
  const restore = installAxiosStub(async (config) => {
    assert.equal(config.url, '/projects/p1');
    return {
      data: {
        id: 'p1',
        name: 'Opened',
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-02T00:00:00Z',
        composition: structuredClone(COMPOSITION),
        generation_provider: 'openai',
        generation_model: 'gpt-4o-mini',
        generation_prompt: { genre: 'ambient' },
      },
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    };
  });
  t.after(restore);

  resetProjectState();
  await useMusicStore.getState().openProject('p1');
  const state = useMusicStore.getState();
  assert.equal(state.activeView, 'composer');
  assert.equal(state.currentProjectId, 'p1');
  assert.equal(state.currentProjectName, 'Opened');
  assert.equal(state.editedMusicJson.schema_version, 'composition.v1');
  assert.equal(state.generationMeta.provider, 'openai');
  assert.equal(state.generationMeta.model, 'gpt-4o-mini');
  assert.equal(state.generationMeta.prompt.genre, 'ambient');
  assert.equal(state.saveStatus, 'saved');
  assert.ok(!JSON.stringify(state.generationMeta).includes('api_key'));
});

test('dirty to saving to saved transitions and autosave debounce cancel/fire', async (t) => {
  const patchCalls = [];
  const restore = installAxiosStub(async (config) => {
    if (config.method === 'patch' || config.method === 'PATCH') {
      patchCalls.push(config);
      return {
        data: { id: 'p1', name: 'Opened', composition: config.data?.composition || null },
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
      };
    }
    return {
      data: {
        id: 'p1',
        name: 'Opened',
        composition: structuredClone(COMPOSITION),
        generation_provider: null,
        generation_model: null,
        generation_prompt: null,
      },
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    };
  });
  t.after(restore);

  resetProjectState();
  await useMusicStore.getState().openProject('p1');

  const timers = [];
  const originalSetTimeout = globalThis.setTimeout;
  const originalClearTimeout = globalThis.clearTimeout;
  globalThis.setTimeout = (fn, delay) => {
    const handle = { fn, delay, cleared: false };
    timers.push(handle);
    return handle;
  };
  globalThis.clearTimeout = (handle) => {
    if (handle) {
      handle.cleared = true;
    }
  };

  try {
    useMusicStore.getState().createNote('piano-1', {
      pitch: 'E4',
      start_tick: 480,
      duration_ticks: 240,
    });
    assert.equal(useMusicStore.getState().saveStatus, 'unsaved');
    assert.equal(timers.length, 1);
    assert.equal(timers[0].delay, AUTOSAVE_DEBOUNCE_MS);

    useMusicStore.getState().createNote('piano-1', {
      pitch: 'G4',
      start_tick: 720,
      duration_ticks: 240,
    });
    assert.equal(timers[0].cleared, true);
    assert.equal(timers.length, 2);

    await timers[1].fn();
    for (let attempt = 0; attempt < 30; attempt += 1) {
      if (useMusicStore.getState().saveStatus === 'saved') {
        break;
      }
      await new Promise((resolve) => originalSetTimeout(resolve, 0));
    }
    assert.equal(useMusicStore.getState().saveStatus, 'saved');
    assert.ok(patchCalls.length >= 1);
  } finally {
    globalThis.setTimeout = originalSetTimeout;
    globalThis.clearTimeout = originalClearTimeout;
  }
});

test('completeGeneration captures provider/model/prompt and marks dirty for open project', async () => {
  resetProjectState({
    currentProjectId: 'p1',
    currentProjectName: 'Opened',
    activeView: 'composer',
    editedMusicJson: structuredClone(COMPOSITION),
    generatedMusicJson: structuredClone(COMPOSITION),
    compositionRevision: 'rev-a',
    lastSavedRevision: 'rev-a',
    saveStatus: 'saved',
  });

  const composition = structuredClone(COMPOSITION);
  composition.tracks[0].events.push({
    type: 'note',
    id: 'n2',
    pitch: 'D4',
    start_tick: 480,
    duration_ticks: 480,
    velocity: 80,
  });

  useMusicStore.getState().completeGeneration({
    music: composition,
    musicxml: '<xml/>',
    warnings: [],
    provider: 'deepseek',
    model: 'deepseek-chat',
  });

  const state = useMusicStore.getState();
  assert.equal(state.generationMeta.provider, 'deepseek');
  assert.equal(state.generationMeta.model, 'deepseek-chat');
  assert.ok(state.generationMeta.prompt);
  assert.ok(!JSON.stringify(state.generationMeta).includes('api_key'));
  assert.equal(state.saveStatus, 'unsaved');
});

test('deleteProjectById clears active project', async (t) => {
  const deleted = [];
  const restore = installAxiosStub(async (config) => {
    if (config.method === 'delete' || config.method === 'DELETE') {
      deleted.push(config.url);
      return { data: null, status: 204, statusText: 'No Content', headers: {}, config };
    }
    if (config.url === '/projects') {
      return { data: { projects: [] }, status: 200, statusText: 'OK', headers: {}, config };
    }
    return {
      data: {
        id: 'p1',
        name: 'Opened',
        composition: structuredClone(COMPOSITION),
      },
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    };
  });
  t.after(restore);

  resetProjectState();
  await useMusicStore.getState().openProject('p1');
  await useMusicStore.getState().deleteProjectById('p1');
  const state = useMusicStore.getState();
  assert.deepEqual(deleted, ['/projects/p1']);
  assert.equal(state.currentProjectId, null);
  assert.equal(state.activeView, 'home');
  assert.equal(state.editedMusicJson, null);
  assert.equal(state.saveStatus, 'saved');
});

test('saveCurrentProject no-ops without open project', async () => {
  resetProjectState();
  const result = await useMusicStore.getState().saveCurrentProject({ reason: 'manual' });
  assert.equal(result, null);
});
