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

function patchPayload(config) {
  const raw = config.data;
  if (typeof raw === 'string') {
    return JSON.parse(raw);
  }
  return raw || {};
}

function resetProjectState(overrides = {}) {
  useMusicStore.setState({
    activeView: 'home',
    currentProjectId: null,
    currentProjectName: '',
    activeBranchId: null,
    activeBranchName: null,
    currentRevisionId: null,
    currentRevisionSequence: null,
    workingVersion: null,
    workingFingerprint: null,
    saveConflict: null,
    projectList: [],
    projectListStatus: 'idle',
    saveStatus: 'saved',
    saveError: '',
    lastSavedPersistRevision: 'empty',
    compositionRevision: 'empty',
    generationMeta: null,
    generatedMusicJson: null,
    editedMusicJson: null,
    musicXml: '',
    uiError: '',
    editCursorTick: 0,
    compositionEditUndoStack: [],
    compositionEditRedoStack: [],
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

function historyProjectPayload(overrides = {}) {
  return {
    id: 'p1',
    name: 'Opened',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-02T00:00:00Z',
    composition: structuredClone(COMPOSITION),
    generation_provider: null,
    generation_model: null,
    generation_prompt: null,
    active_branch_id: 'b1',
    active_branch_name: 'Original',
    current_revision_id: 'r1',
    current_revision_sequence: 1,
    working_version: 0,
    working_fingerprint: 'composition.snapshot.v1:abcdef0123456789',
    ...overrides,
  };
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
  assert.equal(state.editedMusicJson.schema_version, 'composition.v2');
  assert.equal(state.generationMeta.provider, 'openai');
  assert.equal(state.generationMeta.model, 'gpt-4o-mini');
  assert.equal(state.generationMeta.prompt.genre, 'ambient');
  assert.equal(state.prompt.genre, 'ambient');
  assert.equal(state.prompt.mood, '');
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
    lastSavedPersistRevision: 'rev-a',
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
  assert.equal(useMusicStore.getState().saveStatus, 'error');
  assert.match(useMusicStore.getState().saveError, /No project open/i);
});

test('metadata-only JSON edit marks dirty and Save patches', async (t) => {
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

  const originalSetTimeout = globalThis.setTimeout;
  const originalClearTimeout = globalThis.clearTimeout;
  globalThis.setTimeout = () => ({ cleared: false });
  globalThis.clearTimeout = () => {};

  try {
    resetProjectState();
    await useMusicStore.getState().openProject('p1');
    assert.equal(useMusicStore.getState().saveStatus, 'saved');

    const edited = structuredClone(COMPOSITION);
    edited.harmony = [{ bar: 1, chord: 'Am' }];
    edited.key = 'A minor';
    useMusicStore.getState().setEditedMusicJson(edited);

    assert.equal(useMusicStore.getState().saveStatus, 'unsaved');
    await useMusicStore.getState().saveCurrentProject({ reason: 'manual' });
    assert.equal(useMusicStore.getState().saveStatus, 'saved');
    assert.equal(patchCalls.length, 1);
    const payload = patchPayload(patchCalls[0]);
    assert.equal(payload.composition.harmony[0].chord, 'Am');
    assert.equal(payload.composition.key, 'A minor');
    assert.equal(payload.clear_generation, true);
    assert.equal(payload.generation, undefined);
  } finally {
    globalThis.setTimeout = originalSetTimeout;
    globalThis.clearTimeout = originalClearTimeout;
  }
});

test('identical events with new generationMeta marks dirty and Save patches', async (t) => {
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

  const originalSetTimeout = globalThis.setTimeout;
  const originalClearTimeout = globalThis.clearTimeout;
  globalThis.setTimeout = () => ({ cleared: false });
  globalThis.clearTimeout = () => {};

  try {
    resetProjectState();
    await useMusicStore.getState().openProject('p1');
    assert.equal(useMusicStore.getState().saveStatus, 'saved');

    useMusicStore.getState().completeGeneration({
      music: structuredClone(COMPOSITION),
      musicxml: '<xml/>',
      warnings: [],
      provider: 'deepseek',
      model: 'deepseek-chat',
    });

    assert.equal(useMusicStore.getState().saveStatus, 'unsaved');
    await useMusicStore.getState().saveCurrentProject({ reason: 'manual' });
    assert.equal(useMusicStore.getState().saveStatus, 'saved');
    assert.equal(patchCalls.length, 1);
    const payload = patchPayload(patchCalls[0]);
    assert.equal(payload.generation.provider, 'deepseek');
    assert.equal(payload.generation.model, 'deepseek-chat');
  } finally {
    globalThis.setTimeout = originalSetTimeout;
    globalThis.clearTimeout = originalClearTimeout;
  }
});

test('manual Save with open project always invokes patch even when clean', async (t) => {
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
  assert.equal(useMusicStore.getState().saveStatus, 'saved');

  await useMusicStore.getState().saveCurrentProject({ reason: 'manual-force' });
  assert.equal(patchCalls.length, 1);
  assert.equal(useMusicStore.getState().saveStatus, 'saved');
});

test('autosave still skips when persist fingerprint matches', async (t) => {
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
  assert.equal(useMusicStore.getState().saveStatus, 'saved');

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
    useMusicStore.getState().scheduleAutosave();
    assert.equal(timers.length, 0);
    const result = await useMusicStore.getState().saveCurrentProject({ reason: 'autosave' });
    assert.equal(result, null);
    assert.equal(useMusicStore.getState().saveStatus, 'saved');
    assert.equal(patchCalls.length, 0);
  } finally {
    globalThis.setTimeout = originalSetTimeout;
    globalThis.clearTimeout = originalClearTimeout;
  }
});

test('prompt mood/genre edits dirty project and Save persists generation_prompt', async (t) => {
  const patchCalls = [];
  const restore = installAxiosStub(async (config) => {
    if (config.method === 'patch' || config.method === 'PATCH') {
      patchCalls.push(config);
      return {
        data: { id: 'p1', name: 'Opened', composition: null },
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
        generation_provider: 'openai',
        generation_model: 'gpt-4o-mini',
        generation_prompt: {
          genre: 'ambient',
          mood: 'cinematic',
          key: 'C major',
          time_signature: '4/4',
          tempo_min: 80,
          tempo_max: 120,
          instruments: ['piano'],
          sections: [{ type: 'intro', bars: 4 }],
          complexity: 'moderate',
          duration_bars: 8,
          instructions: null,
        },
      },
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    };
  });
  t.after(restore);

  const originalSetTimeout = globalThis.setTimeout;
  const originalClearTimeout = globalThis.clearTimeout;
  globalThis.setTimeout = () => ({ cleared: false });
  globalThis.clearTimeout = () => {};

  try {
    resetProjectState();
    await useMusicStore.getState().openProject('p1');
    assert.equal(useMusicStore.getState().prompt.genre, 'ambient');
    assert.equal(useMusicStore.getState().prompt.mood, 'cinematic');
    assert.equal(useMusicStore.getState().saveStatus, 'saved');

    useMusicStore.getState().updatePrompt('genre', 'rock');
    useMusicStore.getState().updatePrompt('mood', 'sad');
    assert.equal(useMusicStore.getState().saveStatus, 'unsaved');
    assert.equal(useMusicStore.getState().generationMeta.prompt.genre, 'rock');
    assert.equal(useMusicStore.getState().generationMeta.prompt.mood, 'sad');

    await useMusicStore.getState().saveCurrentProject({ reason: 'manual-force' });
    assert.equal(useMusicStore.getState().saveStatus, 'saved');
    assert.equal(patchCalls.length, 1);
    const payload = patchPayload(patchCalls[0]);
    assert.equal(payload.generation.prompt.genre, 'rock');
    assert.equal(payload.generation.prompt.mood, 'sad');
  } finally {
    globalThis.setTimeout = originalSetTimeout;
    globalThis.clearTimeout = originalClearTimeout;
  }
});

test('openProject restores composer form fields from generation_prompt', async (t) => {
  const restore = installAxiosStub(async (config) => ({
    data: {
      id: 'p1',
      name: 'Opened',
      composition: structuredClone(COMPOSITION),
      generation_provider: 'openai',
      generation_model: 'gpt-4o-mini',
      generation_prompt: {
        genre: 'rock',
        mood: 'sad',
        key: 'F# minor',
        time_signature: '4/4',
        tempo_min: 100,
        tempo_max: 120,
        instruments: ['piano', 'bass', 'violin'],
        sections: [
          { type: 'intro', bars: 4 },
          { type: 'verse', bars: 8 },
          { type: 'chorus', bars: 8 },
        ],
        complexity: 'complex',
        duration_bars: 20,
        instructions: null,
      },
    },
    status: 200,
    statusText: 'OK',
    headers: {},
    config,
  }));
  t.after(restore);

  resetProjectState({
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
  await useMusicStore.getState().openProject('p1');
  const state = useMusicStore.getState();
  assert.equal(state.prompt.genre, 'rock');
  assert.equal(state.prompt.mood, 'sad');
  assert.equal(state.prompt.key, 'F# minor');
  assert.equal(state.prompt.instruments, 'piano,bass,violin');
  assert.equal(state.prompt.sections, 'intro:4,verse:8,chorus:8');
  assert.equal(state.prompt.complexity, 'complex');
  assert.equal(state.prompt.duration_bars, 20);
});

test('open project without generation keeps generationMeta null', async (t) => {
  const restore = installAxiosStub(async (config) => ({
    data: {
      id: 'p1',
      name: 'Imported',
      composition: structuredClone(COMPOSITION),
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

  resetProjectState();
  await useMusicStore.getState().openProject('p1');
  const state = useMusicStore.getState();
  assert.equal(state.generationMeta, null);
  assert.equal(state.saveStatus, 'saved');

  useMusicStore.getState().updatePrompt('genre', 'jazz');
  assert.equal(useMusicStore.getState().generationMeta, null);
  assert.equal(useMusicStore.getState().saveStatus, 'saved');
});

test('completeImport installs V2, clears generation, and marks open project dirty', async () => {
  resetProjectState({
    currentProjectId: 'p1',
    currentProjectName: 'Open',
    activeView: 'composer',
    generationMeta: {
      provider: 'openai',
      model: 'gpt-4o-mini',
      prompt: { genre: 'ambient' },
    },
    lastSavedPersistRevision: 'rev-saved',
    saveStatus: 'saved',
    aiEditStartBar: 1,
    aiEditEndBar: 2,
    compositionEditUndoStack: [{ kind: 'edit' }],
  });

  const composition = structuredClone(COMPOSITION);
  const ok = useMusicStore.getState().completeImport({
    composition,
    musicxml: '<score/>',
    import_report: {
      status: 'approximated',
      issues: [{ code: 'tempo_defaulted', action: 'defaulted', severity: 'warning', message: 'tempo', count: 1 }],
      summary: { detected_format: 'midi', display_filename: 'x.mid', input_bytes: 1, target_ppq: 480, source_track_count: 1, result_track_count: 1, source_note_count: 1, result_note_count: 1, bar_count: 1, duration_ticks: 1920 },
    },
    notation_report: { status: 'exact' },
  });
  assert.equal(ok, true);
  const state = useMusicStore.getState();
  assert.equal(state.importStatus, 'success');
  assert.equal(state.editedMusicJson.schema_version, 'composition.v2');
  assert.equal(state.generatedMusicJson.schema_version, 'composition.v2');
  assert.equal(state.musicXml, '<score/>');
  assert.equal(state.generationMeta, null);
  assert.equal(state.aiEditStartBar, null);
  assert.deepEqual(state.compositionEditUndoStack, []);
  assert.equal(state.importReport.status, 'approximated');
  assert.equal(state.notationReport.status, 'exact');
  assert.equal(state.saveStatus, 'unsaved');
});

test('createImportedProject parses before create and skips create on parse failure', async (t) => {
  const createCalls = [];
  const restore = installAxiosStub(async (config) => {
    if (String(config.url || '').startsWith('/imports/')) {
      const error = new Error('Request failed');
      error.response = {
        status: 422,
        data: { detail: { code: 'import_malformed_source', message: 'MIDI upload is empty' } },
      };
      throw error;
    }
    if (config.method === 'post' || config.method === 'POST') {
      createCalls.push(config);
    }
    return { data: {}, status: 200, statusText: 'OK', headers: {}, config };
  });
  t.after(restore);

  resetProjectState();
  const file = { name: 'empty.mid', size: 0 };
  await assert.rejects(
    () => useMusicStore.getState().createImportedProject(file, { format: 'midi' }),
    /MIDI upload is empty|empty|malformed/i,
  );
  assert.equal(createCalls.length, 0);
  assert.equal(useMusicStore.getState().importStatus, 'error');
  assert.equal(useMusicStore.getState().currentProjectId, null);
});

test('createImportedProject creates project after successful parse with null generation', async (t) => {
  const createPayloads = [];
  const restore = installAxiosStub(async (config) => {
    if (config.url === '/imports/midi') {
      return {
        data: {
          composition: structuredClone(COMPOSITION),
          musicxml: '<score-partwise/>',
          import_report: {
            status: 'exact',
            issues: [],
            summary: {
              detected_format: 'midi',
              display_filename: 'song.mid',
              input_bytes: 8,
              target_ppq: 480,
              source_track_count: 1,
              result_track_count: 1,
              source_note_count: 1,
              result_note_count: 1,
              bar_count: 1,
              duration_ticks: 1920,
            },
          },
          notation_report: { status: 'exact' },
        },
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
      };
    }
    if (config.url === '/projects' && (config.method === 'post' || config.method === 'POST')) {
      const payload = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
      createPayloads.push(payload);
      return {
        data: {
          id: 'imported-1',
          name: payload.name,
          composition: payload.composition,
          generation_provider: null,
          generation_model: null,
          generation_prompt: null,
        },
        status: 201,
        statusText: 'Created',
        headers: {},
        config,
      };
    }
    return { data: {}, status: 200, statusText: 'OK', headers: {}, config };
  });
  t.after(restore);

  resetProjectState();
  const file = { name: 'song.mid', size: 4 };
  const result = await useMusicStore.getState().createImportedProject(file, { format: 'midi' });
  assert.equal(createPayloads.length, 1);
  assert.equal(createPayloads[0].name, 'song');
  assert.equal(createPayloads[0].generation, undefined);
  assert.equal(createPayloads[0].composition.schema_version, 'composition.v2');
  assert.equal(result.project.id, 'imported-1');
  const state = useMusicStore.getState();
  assert.equal(state.currentProjectId, 'imported-1');
  assert.equal(state.generationMeta, null);
  assert.equal(state.importStatus, 'success');
  assert.equal(state.saveStatus, 'saved');
  assert.equal(state.musicXml, '<score-partwise/>');
});

test('open project hydrates branch/head working fields', async (t) => {
  const restore = installAxiosStub(async () => ({
    data: historyProjectPayload({ active_branch_name: 'Original' }),
    status: 200,
    statusText: 'OK',
    headers: {},
    config: {},
  }));
  t.after(restore);

  resetProjectState();
  await useMusicStore.getState().openProject('p1');
  const state = useMusicStore.getState();
  assert.equal(state.activeBranchId, 'b1');
  assert.equal(state.activeBranchName, 'Original');
  assert.equal(state.currentRevisionId, 'r1');
  assert.equal(state.workingVersion, 0);
  assert.equal(state.workingFingerprint, 'composition.snapshot.v1:abcdef0123456789');
});

test('autosave patch includes CAS preconditions and updates working version', async (t) => {
  const patchPayloads = [];
  let workingVersion = 0;
  const restore = installAxiosStub(async (config) => {
    const method = String(config.method || 'get').toLowerCase();
    const url = String(config.url || '');
    if (method === 'patch') {
      const payload = patchPayload(config);
      patchPayloads.push(payload);
      workingVersion += 1;
      return {
        data: historyProjectPayload({
          working_version: workingVersion,
          working_fingerprint: 'composition.snapshot.v1:nextfingerprint01',
          composition: payload.composition,
        }),
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
      };
    }
    if (method === 'get' && url === '/projects/p1') {
      return {
        data: historyProjectPayload(),
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
      };
    }
    return { data: {}, status: 200, statusText: 'OK', headers: {}, config };
  });
  t.after(restore);

  resetProjectState();
  await useMusicStore.getState().openProject('p1');
  useMusicStore.getState().createNote('piano-1', {
    pitch: 'E4',
    start_tick: 480,
    duration_ticks: 240,
  });
  await useMusicStore.getState().saveCurrentProject({ reason: 'autosave' });
  assert.equal(patchPayloads.length, 1);
  assert.equal(patchPayloads[0].branch_id, 'b1');
  assert.equal(patchPayloads[0].expected_active_branch_id, 'b1');
  assert.equal(patchPayloads[0].expected_working_version, 0);
  assert.equal(patchPayloads[0].expected_source_fingerprint, 'composition.snapshot.v1:abcdef0123456789');
  assert.equal(useMusicStore.getState().workingVersion, 1);
  assert.equal(useMusicStore.getState().saveStatus, 'saved');
});

test('manual save drafts then promotes durable checkpoint', async (t) => {
  const calls = [];
  let workingVersion = 0;
  const restore = installAxiosStub(async (config) => {
    const method = String(config.method || 'get').toLowerCase();
    const url = String(config.url || '');
    if (method === 'get' && url === '/projects/p1') {
      return {
        data: historyProjectPayload(),
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
      };
    }
    if (method === 'patch') {
      workingVersion += 1;
      calls.push(['patch', patchPayload(config)]);
      return {
        data: historyProjectPayload({
          working_version: workingVersion,
          working_fingerprint: 'composition.snapshot.v1:draftfingerprint01',
        }),
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
      };
    }
    if (method === 'post' && url.endsWith('/revisions')) {
      calls.push(['commit', patchPayload(config)]);
      return {
        data: {
          project_id: 'p1',
          active_branch_id: 'b1',
          active_branch_name: 'Original',
          current_revision_id: 'r2',
          current_revision_sequence: 2,
          working_version: workingVersion,
          working_fingerprint: 'composition.snapshot.v1:draftfingerprint01',
          composition: structuredClone(COMPOSITION),
          revision_created: true,
          created_revision_ids: ['r2'],
          operation_type: 'manual-checkpoint',
        },
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
      };
    }
    return { data: {}, status: 200, statusText: 'OK', headers: {}, config };
  });
  t.after(restore);

  resetProjectState();
  await useMusicStore.getState().openProject('p1');
  await useMusicStore.getState().saveCurrentProject({ reason: 'manual-force' });
  assert.deepEqual(calls.map((item) => item[0]), ['patch', 'commit']);
  assert.equal(calls[1][1].operation_type, 'manual-checkpoint');
  assert.equal(useMusicStore.getState().currentRevisionId, 'r2');
  assert.equal(useMusicStore.getState().saveStatus, 'saved');
});

test('save conflict stops autosave retries and records bounded detail', async (t) => {
  const restore = installAxiosStub(async (config) => {
    const method = String(config.method || 'get').toLowerCase();
    const url = String(config.url || '');
    if (method === 'get' && url === '/projects/p1') {
      return {
        data: historyProjectPayload(),
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
      };
    }
    if (method === 'patch') {
      const error = new Error('conflict');
      error.response = {
        status: 409,
        data: {
          detail: {
            code: 'project_revision_conflict',
            project_id: 'p1',
            expected_working_version: 0,
            current_working_version: 3,
            composition: { leak: true },
          },
        },
      };
      throw error;
    }
    return { data: {}, status: 200, statusText: 'OK', headers: {}, config };
  });
  t.after(restore);

  resetProjectState();
  await useMusicStore.getState().openProject('p1');
  useMusicStore.getState().createNote('piano-1', {
    pitch: 'F4',
    start_tick: 960,
    duration_ticks: 240,
  });
  await assert.rejects(
    () => useMusicStore.getState().saveCurrentProject({ reason: 'autosave' }),
    (error) => error.name === 'ProjectRevisionConflictError',
  );
  const state = useMusicStore.getState();
  assert.equal(state.saveStatus, 'conflict');
  assert.equal(state.saveConflict.code, 'project_revision_conflict');
  assert.equal(state.saveConflict.composition, undefined);
  assert.equal(state.saveConflict.current_working_version, 3);

  const timers = [];
  const originalSetTimeout = globalThis.setTimeout;
  globalThis.setTimeout = (fn, delay) => {
    timers.push({ fn, delay });
    return timers.length;
  };
  try {
    useMusicStore.getState().scheduleAutosave();
    assert.equal(timers.length, 0);
  } finally {
    globalThis.setTimeout = originalSetTimeout;
  }
});
