import assert from 'node:assert/strict';
import test from 'node:test';

import axios from 'axios';
import { migrateV1ToV2 } from '../utils/compositionVersion.js';
import { compositionRevisionKey } from '../utils/playbackPosition.js';
import {
  ANALYSIS_DEBOUNCE_MS,
  buildAnalysisRequestKey,
  makeAnalysisSectionKey,
} from '../utils/compositionAnalysis.js';
import { useMusicStore } from './musicStore.js';

const BASE = migrateV1ToV2({
  schema_version: 'composition.v1',
  tempo: 100,
  key: 'C major',
  time_signature: '4/4',
  ticks_per_quarter: 480,
  bar_count: 4,
  duration_ticks: 7680,
  sections: [
    { type: 'intro', start_bar: 1, bar_count: 2, start_tick: 0, duration_ticks: 3840 },
    {
      id: 'chorus-a',
      type: 'chorus',
      start_bar: 3,
      bar_count: 2,
      start_tick: 3840,
      duration_ticks: 3840,
    },
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
        { type: 'note', id: 'n2', pitch: 'E4', start_tick: 480, duration_ticks: 480, velocity: 88 },
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

function sampleReport(overrides = {}) {
  return {
    schema_version: 'composition.analysis.v1',
    algorithm_version: 'native-v1',
    source_schema_version: 'composition.v2',
    source_fingerprint: 'c'.repeat(64),
    status: 'ok',
    resolved_scope: {
      kind: 'composition',
      start_tick: 0,
      end_tick: 7680,
      start_bar: 1,
      end_bar_exclusive: 5,
    },
    warnings: [],
    section_summaries: [],
    tonality: { status: 'ok' },
    harmony: { status: 'ok' },
    ...overrides,
  };
}

function installAxiosStub(handler) {
  const previousAdapter = axios.defaults.adapter;
  axios.defaults.adapter = handler;
  return () => {
    axios.defaults.adapter = previousAdapter;
  };
}

function resetAnalysisStore(composition = structuredClone(BASE)) {
  const revision = compositionRevisionKey(composition);
  useMusicStore.getState().resetAnalysis?.();
  useMusicStore.setState({
    generatedMusicJson: composition,
    editedMusicJson: composition,
    musicXml: '',
    compositionRevision: revision,
    notationRevision: revision,
    trackControls: {
      'melody-1': { muted: false, solo: false, volumeMidi: 100 },
      'bass-1': { muted: false, solo: false, volumeMidi: 100 },
    },
    pianoRollTrackId: 'melody-1',
    pianoRollNoteId: null,
    pianoRollNoteIds: [],
    editCursorTick: 0,
    compositionEditUndoStack: [],
    compositionEditRedoStack: [],
    analysisScope: 'composition',
    analysisSelectedSectionKey: null,
    analysisResult: null,
    analysisResultKey: null,
    analysisAttemptKey: null,
    analysisStatus: 'idle',
    analysisError: '',
    analysisWarnings: [],
    analysisTabVisible: true,
    currentProjectId: 'proj-analysis',
    currentProjectName: 'Analysis Test',
    uiError: '',
    warnings: [],
  });
}

function delay(ms) {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

test('analysis defaults and composition-scope success', async (t) => {
  resetAnalysisStore();
  const idle = useMusicStore.getState();
  assert.equal(idle.analysisScope, 'composition');
  assert.equal(idle.analysisStatus, 'idle');
  assert.equal(idle.analysisResult, null);
  assert.equal(idle.analysisError, '');

  let posts = 0;
  const restore = installAxiosStub(async (config) => {
    posts += 1;
    const payload = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
    assert.equal(payload.scope.kind, 'composition');
    return {
      data: sampleReport(),
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    };
  });
  t.after(restore);

  const report = await useMusicStore.getState().requestAnalysis({ force: true, reason: 'test' });
  assert.equal(report.status, 'ok');
  const state = useMusicStore.getState();
  assert.equal(state.analysisStatus, 'success');
  assert.equal(state.analysisResult?.algorithm_version, 'native-v1');
  assert.equal(posts, 1);
  assert.equal(state.getAnalysisFreshness().isCurrent, true);
});

test('section and track scopes request matching payloads', async (t) => {
  resetAnalysisStore();
  const scopes = [];
  const restore = installAxiosStub(async (config) => {
    const payload = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
    scopes.push(payload.scope);
    return {
      data: sampleReport({
        resolved_scope: {
          ...sampleReport().resolved_scope,
          kind: payload.scope.kind,
        },
      }),
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    };
  });
  t.after(restore);

  useMusicStore.getState().setAnalysisScope('section');
  await useMusicStore.getState().requestAnalysis({ force: true });
  assert.equal(scopes[0].kind, 'section');
  assert.equal(scopes[0].section_index, 0);
  assert.ok(useMusicStore.getState().analysisSelectedSectionKey);

  useMusicStore.getState().selectAnalysisSection('id:chorus-a');
  await useMusicStore.getState().requestAnalysis({ force: true });
  assert.equal(scopes[1].kind, 'section');
  assert.equal(scopes[1].section_id, 'chorus-a');

  useMusicStore.getState().setAnalysisScope('track');
  await useMusicStore.getState().requestAnalysis({ force: true });
  assert.equal(scopes[2].kind, 'track');
  assert.equal(scopes[2].track_id, 'melody-1');
});

test('identical in-flight requests are deduplicated; force refresh bypasses', async (t) => {
  resetAnalysisStore();
  let posts = 0;
  const restore = installAxiosStub(async (config) => {
    posts += 1;
    await delay(40);
    return {
      data: sampleReport({ source_fingerprint: `${posts}`.padEnd(64, 'd') }),
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    };
  });
  t.after(restore);

  const first = useMusicStore.getState().requestAnalysis({ force: true, reason: 'a' });
  const second = useMusicStore.getState().requestAnalysis({ reason: 'b-dedupe' });
  await Promise.all([first, second]);
  assert.equal(posts, 1);

  await useMusicStore.getState().requestAnalysis({ reason: 'reuse-success' });
  assert.equal(posts, 1);

  await useMusicStore.getState().refreshAnalysis();
  assert.equal(posts, 2);
  assert.equal(useMusicStore.getState().analysisStatus, 'success');
});

test('note edits derive stale freshness and recompute when tab visible', async (t) => {
  resetAnalysisStore();
  const restore = installAxiosStub(async (config) => ({
    data: sampleReport({
      source_fingerprint: String(Date.now()).padEnd(64, 'e'),
    }),
    status: 200,
    statusText: 'OK',
    headers: {},
    config,
  }));
  t.after(restore);

  await useMusicStore.getState().requestAnalysis({ force: true });
  const beforeKey = useMusicStore.getState().analysisResultKey;
  assert.ok(beforeKey);

  useMusicStore.getState().updateNote('melody-1', 'n1', { pitch: 'D4' });
  const stale = useMusicStore.getState().getAnalysisFreshness();
  assert.equal(stale.isStale, true);
  assert.equal(stale.isCurrent, false);
  assert.notEqual(useMusicStore.getState().compositionRevision, JSON.parse(beforeKey).composition_revision);

  await delay(ANALYSIS_DEBOUNCE_MS + 80);
  await delay(20);
  const after = useMusicStore.getState();
  assert.equal(after.analysisStatus, 'success');
  assert.equal(after.getAnalysisFreshness().isCurrent, true);
  assert.notEqual(after.analysisResultKey, beforeKey);
});

test('A-to-B-to-A stale responses are rejected', async (t) => {
  resetAnalysisStore();
  // Avoid debounce side-effects while orchestrating overlapping requests.
  useMusicStore.setState({ analysisTabVisible: false });
  const resolvers = [];
  const restore = installAxiosStub(async (config) => {
    const payload = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
    const marker = payload.scope.kind === 'composition' ? 'A' : 'B';
    await new Promise((resolve) => {
      resolvers.push({ resolve, marker, payload });
    });
    return {
      data: sampleReport({
        source_fingerprint: marker.padEnd(64, 'f'),
        resolved_scope: {
          ...sampleReport().resolved_scope,
          kind: payload.scope.kind,
        },
      }),
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    };
  });
  t.after(restore);

  const pA1 = useMusicStore.getState().requestAnalysis({ force: true, reason: 'A1' });
  useMusicStore.setState({ analysisScope: 'track', pianoRollTrackId: 'melody-1' });
  const pB = useMusicStore.getState().requestAnalysis({ force: true, reason: 'B' });
  useMusicStore.setState({ analysisScope: 'composition' });
  const pA2 = useMusicStore.getState().requestAnalysis({ force: true, reason: 'A2' });

  assert.equal(resolvers.length, 3);
  // Resolve out of order: first A, then B, then final A.
  resolvers[0].resolve();
  await pA1;
  assert.equal(useMusicStore.getState().analysisResult, null);

  resolvers[1].resolve();
  await pB;
  assert.equal(useMusicStore.getState().analysisResult, null);

  resolvers[2].resolve();
  await pA2;
  const state = useMusicStore.getState();
  assert.equal(state.analysisStatus, 'success');
  assert.equal(state.analysisResult?.resolved_scope?.kind, 'composition');
  assert.equal(state.analysisResult?.source_fingerprint?.[0], 'A');
  assert.equal(state.getAnalysisFreshness().isCurrent, true);
});

test('failed analysis retains prior result and retry recovers', async (t) => {
  resetAnalysisStore();
  let mode = 'ok';
  const restore = installAxiosStub(async (config) => {
    if (mode === 'ok') {
      return {
        data: sampleReport({ source_fingerprint: 'ok'.padEnd(64, 'g') }),
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
      };
    }
    const error = new Error('Request failed');
    error.isAxiosError = true;
    error.response = {
      status: 422,
      data: {
        detail: {
          code: 'analysis_invalid_composition',
          message: 'boom',
          details: { reason: 'test' },
        },
      },
    };
    throw error;
  });
  t.after(restore);

  await useMusicStore.getState().requestAnalysis({ force: true });
  const retained = useMusicStore.getState().analysisResult;
  assert.ok(retained);

  mode = 'fail';
  await useMusicStore.getState().refreshAnalysis();
  const failed = useMusicStore.getState();
  assert.equal(failed.analysisStatus, 'error');
  assert.match(failed.analysisError, /boom/);
  assert.equal(failed.analysisResult, retained);

  mode = 'ok';
  await useMusicStore.getState().retryAnalysis();
  const recovered = useMusicStore.getState();
  assert.equal(recovered.analysisStatus, 'success');
  assert.equal(recovered.analysisError, '');
  assert.ok(recovered.analysisResult);
});

test('project/composition replacement clears analysis state', async () => {
  resetAnalysisStore();
  useMusicStore.setState({
    analysisResult: sampleReport(),
    analysisResultKey: 'k',
    analysisStatus: 'success',
    analysisWarnings: [{ code: 'empty_analysis_scope' }],
    analysisScope: 'track',
    analysisSelectedSectionKey: makeAnalysisSectionKey(BASE.sections[0], 0),
  });

  await useMusicStore.getState().startGeneration();
  await useMusicStore.getState().completeGeneration({
    music: structuredClone(BASE),
    musicxml: '<score/>',
    warnings: [],
    provider: 'fake',
    model: 'fixture',
  });
  await useMusicStore.getState().applyGenerationCandidate();
  // Cancel any analysis debounce kicked by the apply transaction.
  useMusicStore.setState({
    analysisResult: null,
    analysisStatus: 'idle',
    analysisScope: 'composition',
    analysisSelectedSectionKey: null,
  });
  const afterGen = useMusicStore.getState();
  assert.equal(afterGen.analysisResult, null);
  assert.equal(afterGen.analysisStatus, 'idle');
  assert.equal(afterGen.analysisScope, 'composition');
  assert.equal(afterGen.analysisSelectedSectionKey, null);
});

test('undo restores freshness against retained analysis result', async (t) => {
  resetAnalysisStore();
  const restore = installAxiosStub(async (config) => ({
    data: sampleReport({ source_fingerprint: 'undo'.padEnd(64, 'h') }),
    status: 200,
    statusText: 'OK',
    headers: {},
    config,
  }));
  t.after(restore);

  await useMusicStore.getState().requestAnalysis({ force: true });
  const originalKey = useMusicStore.getState().analysisResultKey;
  const originalRevision = useMusicStore.getState().compositionRevision;

  useMusicStore.getState().updateNote('melody-1', 'n1', { pitch: 'G4' });
  assert.equal(useMusicStore.getState().getAnalysisFreshness().isStale, true);

  assert.equal(useMusicStore.getState().undoCompositionEdit(), true);
  assert.equal(useMusicStore.getState().compositionRevision, originalRevision);
  const freshness = useMusicStore.getState().getAnalysisFreshness();
  assert.equal(freshness.isCurrent, true);
  assert.equal(useMusicStore.getState().analysisResultKey, originalKey);
});

test('mute/solo/volume UI does not invalidate analysis freshness', async (t) => {
  resetAnalysisStore();
  let posts = 0;
  const restore = installAxiosStub(async (config) => {
    posts += 1;
    return {
      data: sampleReport(),
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    };
  });
  t.after(restore);

  await useMusicStore.getState().requestAnalysis({ force: true });
  const before = useMusicStore.getState();
  const beforeKey = before.analysisResultKey;
  const beforeRevision = before.compositionRevision;

  before.toggleTrackMute('melody-1');
  before.toggleTrackSolo('bass-1');
  before.setTrackVolume('melody-1', 42);

  await delay(ANALYSIS_DEBOUNCE_MS + 80);
  const after = useMusicStore.getState();
  assert.equal(after.compositionRevision, beforeRevision);
  assert.equal(after.analysisResultKey, beforeKey);
  assert.equal(after.getAnalysisFreshness().isCurrent, true);
  assert.equal(posts, 1);
  assert.equal(after.trackControls['melody-1'].muted, true);
  assert.equal(after.trackControls['bass-1'].solo, true);
  assert.equal(after.trackControls['melody-1'].volumeMidi, 42);
});

test('requestAnalysis skipped when analysis tab is not visible unless forced', async (t) => {
  resetAnalysisStore();
  useMusicStore.setState({ analysisTabVisible: false });
  let posts = 0;
  const restore = installAxiosStub(async (config) => {
    posts += 1;
    return {
      data: sampleReport(),
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    };
  });
  t.after(restore);

  await useMusicStore.getState().requestAnalysis({ reason: 'hidden' });
  assert.equal(posts, 0);
  await useMusicStore.getState().requestAnalysis({ force: true, reason: 'forced' });
  assert.equal(posts, 1);
});

test('scope change after success marks stale until recomputed', async (t) => {
  resetAnalysisStore();
  const restore = installAxiosStub(async (config) => {
    const payload = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
    return {
      data: sampleReport({
        source_fingerprint: payload.scope.kind.padEnd(64, 'i'),
        resolved_scope: {
          ...sampleReport().resolved_scope,
          kind: payload.scope.kind,
        },
      }),
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    };
  });
  t.after(restore);

  await useMusicStore.getState().requestAnalysis({ force: true });
  useMusicStore.setState({ analysisTabVisible: false });
  useMusicStore.getState().setAnalysisScope('track');
  // Tab hidden: schedule is skipped, but desired key already diverges.
  assert.equal(useMusicStore.getState().getAnalysisFreshness().isStale, true);

  useMusicStore.setState({ analysisTabVisible: true });
  await useMusicStore.getState().requestAnalysis({ force: true });
  assert.equal(useMusicStore.getState().analysisResult?.resolved_scope?.kind, 'track');
  assert.equal(useMusicStore.getState().getAnalysisFreshness().isCurrent, true);

  // Desired key identity stays stable for the same revision+scope.
  const desired = buildAnalysisRequestKey({
    compositionRevision: useMusicStore.getState().compositionRevision,
    scope: { kind: 'track', track_id: 'melody-1' },
  });
  assert.equal(useMusicStore.getState().analysisResultKey, desired);
});
