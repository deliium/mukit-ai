import assert from 'node:assert/strict';
import test from 'node:test';

import axios from 'axios';
import { useMusicStore } from './musicStore.js';
import { resolvePlaybackSource } from '../utils/playbackSource.js';

const COMPOSITION_V2 = {
  schema_version: 'composition.v2',
  tempo: 100,
  key: 'C major',
  time_signature: '4/4',
  ticks_per_quarter: 480,
  bar_count: 1,
  duration_ticks: 1920,
  sections: [
    { id: 's1', type: 'intro', start_bar: 1, bar_count: 1, start_tick: 0, duration_ticks: 1920 },
  ],
  tracks: [
    {
      id: 'piano-1',
      name: 'Piano',
      instrument: 'piano',
      role: 'harmony',
      midi_program: 0,
      channel: 1,
      is_drum: false,
      volume: 100,
      pan: 0,
      expression: 127,
      events: [
        {
          type: 'note',
          id: 'n1',
          pitch: 'C4',
          start_tick: 0,
          duration_ticks: 480,
          velocity: 80,
        },
      ],
      dynamic_marks: [],
      sustain_pedals: [],
      automation: [],
    },
  ],
  harmony: [],
  markers: [],
  motifs: [],
  tempo_changes: [],
  time_signature_changes: [],
  key_changes: [],
};

const ALT_COMPOSITION = {
  ...structuredClone(COMPOSITION_V2),
  tracks: [
    {
      ...COMPOSITION_V2.tracks[0],
      events: [
        {
          type: 'note',
          id: 'n2',
          pitch: 'E4',
          start_tick: 0,
          duration_ticks: 480,
          velocity: 80,
        },
      ],
    },
  ],
};

const HEAD_FP = 'composition.snapshot.v1:abcdef0123456789abcd';
const ALT_FP = 'composition.snapshot.v1:fedcba9876543210fedc';

function installAxiosStub(handler) {
  const previousAdapter = axios.defaults.adapter;
  axios.defaults.adapter = handler;
  return () => {
    axios.defaults.adapter = previousAdapter;
  };
}

function resetState(overrides = {}) {
  useMusicStore.setState({
    activeView: 'composer',
    currentProjectId: 'p1',
    currentProjectName: 'Demo',
    activeBranchId: 'b1',
    activeBranchName: 'Original',
    currentRevisionId: 'r1',
    currentRevisionSequence: 1,
    workingVersion: 1,
    workingFingerprint: HEAD_FP,
    saveConflict: null,
    saveStatus: 'saved',
    saveError: '',
    lastSavedPersistRevision: 'seed',
    editedMusicJson: structuredClone(COMPOSITION_V2),
    generatedMusicJson: structuredClone(COMPOSITION_V2),
    compositionRevision: 'seed',
    generationMeta: null,
    compositionEditUndoStack: [],
    compositionEditRedoStack: [],
    developmentAuditionActive: false,
    arrangementAuditionMode: 'source',
    versionBranches: [],
    versionBranchesStatus: 'idle',
    versionBranchesError: '',
    versionRevisions: [],
    versionRevisionsStatus: 'idle',
    versionRevisionsError: '',
    versionRevisionsNextBefore: null,
    versionSelectedRevisionId: null,
    versionCompareRevisionId: null,
    versionRevisionDetails: {},
    versionCompareResult: null,
    versionCompareStatus: 'idle',
    versionCompareError: '',
    versionAuditionActive: false,
    versionAuditionTrackControls: {},
    versionActionStatus: 'idle',
    versionActionError: '',
    versionRestoreBlockReason: null,
    trackControls: {},
    playbackLoop: { startTick: 0, endTick: 1920, enabled: true },
    ...overrides,
  });
}

async function alignSavedFingerprint() {
  const { projectPersistRevisionKey } = await import('../utils/projectPersistRevision.js');
  const state = useMusicStore.getState();
  useMusicStore.setState({
    lastSavedPersistRevision: projectPersistRevisionKey(
      state.editedMusicJson,
      state.generationMeta,
    ),
    saveStatus: 'saved',
  });
}

test('loadVersionRevisions stores metadata only and paginates', async (t) => {
  const calls = [];
  const restore = installAxiosStub(async (config) => {
    calls.push(config.url);
    if (String(config.url).includes('before_sequence=2')) {
      return {
        data: {
          revisions: [
            {
              id: 'r0',
              project_id: 'p1',
              parent_revision_id: null,
              sequence: 1,
              name: null,
              operation_type: 'project-create',
              ai_provider: null,
              ai_model: null,
              has_user_instruction: false,
              affected_ranges: [],
              affected_track_ids: [],
              snapshot_fingerprint: HEAD_FP,
              created_at: '2026-01-01T00:00:00Z',
              summary: {},
            },
          ],
          next_before_sequence: null,
        },
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
      };
    }
    return {
      data: {
        revisions: [
          {
            id: 'r1',
            project_id: 'p1',
            parent_revision_id: 'r0',
            sequence: 2,
            name: 'Head',
            operation_type: 'manual-checkpoint',
            ai_provider: null,
            ai_model: null,
            has_user_instruction: false,
            affected_ranges: [],
            affected_track_ids: [],
            snapshot_fingerprint: HEAD_FP,
            created_at: '2026-01-02T00:00:00Z',
            summary: {},
          },
        ],
        next_before_sequence: 2,
      },
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    };
  });
  t.after(restore);

  resetState();
  await alignSavedFingerprint();
  await useMusicStore.getState().loadVersionRevisions({ reset: true });
  let state = useMusicStore.getState();
  assert.equal(state.versionRevisions.length, 1);
  assert.equal(state.versionRevisions[0].id, 'r1');
  assert.equal(state.versionRevisions[0].composition, undefined);
  assert.equal(state.versionRevisionsNextBefore, 2);

  await useMusicStore.getState().loadVersionRevisions({ reset: false });
  state = useMusicStore.getState();
  assert.equal(state.versionRevisions.length, 2);
  assert.equal(state.versionRevisionsNextBefore, null);
  assert.ok(calls.some((url) => String(url).includes('/revisions')));
});

test('selectVersionRevision lazy-loads detail and compare stays non-mutating', async (t) => {
  const restore = installAxiosStub(async (config) => {
    if (String(config.url).includes('/revisions/r0')) {
      return {
        data: {
          revision: {
            id: 'r0',
            project_id: 'p1',
            parent_revision_id: null,
            sequence: 1,
            name: null,
            operation_type: 'project-create',
            ai_provider: null,
            ai_model: null,
            has_user_instruction: false,
            affected_ranges: [],
            affected_track_ids: [],
            snapshot_fingerprint: ALT_FP,
            created_at: '2026-01-01T00:00:00Z',
            summary: {},
          },
          composition: structuredClone(ALT_COMPOSITION),
        },
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
      };
    }
    return { data: {}, status: 404, statusText: 'NF', headers: {}, config };
  });
  t.after(restore);

  resetState({
    versionRevisions: [
      {
        id: 'r1',
        snapshot_fingerprint: HEAD_FP,
        sequence: 2,
      },
      {
        id: 'r0',
        snapshot_fingerprint: ALT_FP,
        sequence: 1,
      },
    ],
  });
  await alignSavedFingerprint();
  const before = structuredClone(useMusicStore.getState().editedMusicJson);
  await useMusicStore.getState().selectVersionRevision('r0');
  const state = useMusicStore.getState();
  assert.equal(state.versionSelectedRevisionId, 'r0');
  assert.ok(state.versionRevisionDetails.r0);
  assert.equal(state.versionRevisionDetails.r0.composition.tracks[0].events[0].pitch, 'E4');
  assert.deepEqual(state.editedMusicJson, before);
  assert.equal(state.versionCompareStatus, 'ready');
  assert.equal(state.versionCompareResult.identical, false);
});

test('version audition does not mutate working composition and uses isolated mixer', async (t) => {
  resetState({
    versionSelectedRevisionId: 'r0',
    versionRevisionDetails: {
      r0: { revision: { id: 'r0' }, composition: structuredClone(ALT_COMPOSITION) },
    },
    trackControls: {
      'piano-1': { muted: false, solo: false, volumeMidi: 100 },
    },
  });
  await alignSavedFingerprint();
  const before = structuredClone(useMusicStore.getState().editedMusicJson);
  const ok = await useMusicStore.getState().setVersionAuditionActive(true);
  assert.equal(ok, true);
  let state = useMusicStore.getState();
  assert.equal(state.versionAuditionActive, true);
  assert.equal(state.developmentAuditionActive, false);
  assert.deepEqual(state.editedMusicJson, before);
  assert.ok(state.versionAuditionTrackControls['piano-1']);

  useMusicStore.getState().toggleVersionAuditionMute('piano-1');
  state = useMusicStore.getState();
  assert.equal(state.versionAuditionTrackControls['piano-1'].muted, true);
  assert.equal(state.trackControls['piano-1'].muted, false);

  const resolved = resolvePlaybackSource(state, {});
  assert.equal(resolved.source, 'version');
  assert.equal(resolved.composition.tracks[0].events[0].pitch, 'E4');
});

test('restore blocks dirty draft then succeeds after checkpoint path', async (t) => {
  const restoreCalls = [];
  const restore = installAxiosStub(async (config) => {
    if (String(config.url).includes('/restore')) {
      restoreCalls.push(config);
      return {
        data: {
          project_id: 'p1',
          active_branch_id: 'b1',
          active_branch_name: 'Original',
          current_revision_id: 'r2',
          current_revision_sequence: 3,
          working_version: 2,
          working_fingerprint: ALT_FP,
          composition: structuredClone(ALT_COMPOSITION),
          revision_created: true,
          created_revision_ids: ['r2'],
          operation_type: 'revision-restore',
        },
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
      };
    }
    if (String(config.url).includes('/revisions') && config.method === 'post') {
      return {
        data: {
          project_id: 'p1',
          active_branch_id: 'b1',
          active_branch_name: 'Original',
          current_revision_id: 'r1b',
          current_revision_sequence: 2,
          working_version: 2,
          working_fingerprint: HEAD_FP,
          composition: structuredClone(COMPOSITION_V2),
          revision_created: true,
          created_revision_ids: ['r1b'],
          operation_type: 'manual-checkpoint',
        },
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
      };
    }
    if (config.method === 'patch' || config.method === 'PATCH') {
      return {
        data: {
          id: 'p1',
          name: 'Demo',
          composition: structuredClone(COMPOSITION_V2),
          active_branch_id: 'b1',
          active_branch_name: 'Original',
          current_revision_id: 'r1',
          current_revision_sequence: 1,
          working_version: 1,
          working_fingerprint: HEAD_FP,
        },
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
      };
    }
    if (String(config.url).includes('/revisions?') || String(config.url).endsWith('/revisions')) {
      return {
        data: {
          revisions: [
            {
              id: 'r2',
              project_id: 'p1',
              parent_revision_id: 'r1',
              sequence: 3,
              name: null,
              operation_type: 'revision-restore',
              ai_provider: null,
              ai_model: null,
              has_user_instruction: false,
              affected_ranges: [],
              affected_track_ids: [],
              snapshot_fingerprint: ALT_FP,
              created_at: '2026-01-03T00:00:00Z',
              summary: {},
            },
            {
              id: 'r1',
              project_id: 'p1',
              parent_revision_id: null,
              sequence: 1,
              name: null,
              operation_type: 'project-create',
              ai_provider: null,
              ai_model: null,
              has_user_instruction: false,
              affected_ranges: [],
              affected_track_ids: [],
              snapshot_fingerprint: HEAD_FP,
              created_at: '2026-01-01T00:00:00Z',
              summary: {},
            },
          ],
          next_before_sequence: null,
        },
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
      };
    }
    return { data: {}, status: 404, statusText: 'NF', headers: {}, config };
  });
  t.after(restore);

  resetState({
    versionRevisions: [
      { id: 'r1', snapshot_fingerprint: HEAD_FP, sequence: 1 },
    ],
    saveStatus: 'unsaved',
    lastSavedPersistRevision: 'stale',
  });

  const blocked = await useMusicStore.getState().restoreVersionRevision('r0');
  assert.equal(blocked.ok, false);
  assert.equal(blocked.reason, 'dirty-draft');
  assert.equal(restoreCalls.length, 0);

  await alignSavedFingerprint();
  useMusicStore.setState({
    versionRevisions: [
      { id: 'r1', snapshot_fingerprint: HEAD_FP, sequence: 1 },
    ],
    workingFingerprint: HEAD_FP,
    currentRevisionId: 'r1',
  });

  const undoBefore = useMusicStore.getState().compositionEditUndoStack.length;
  const result = await useMusicStore.getState().restoreVersionRevision('r0');
  assert.equal(result.ok, true);
  assert.equal(restoreCalls.length, 1);
  const state = useMusicStore.getState();
  assert.equal(state.currentRevisionId, 'r2');
  assert.equal(state.editedMusicJson.tracks[0].events[0].pitch, 'E4');
  assert.equal(state.compositionEditUndoStack.length, undoBefore + 1);
  assert.equal(state.saveStatus, 'saved');
  assert.equal(state.versionAuditionActive, false);
});

test('stale version revision responses are ignored after project switch', async (t) => {
  let resolveDetail;
  const detailPromise = new Promise((resolve) => {
    resolveDetail = resolve;
  });
  const restore = installAxiosStub(async (config) => {
    if (String(config.url).includes('/revisions/r0')) {
      await detailPromise;
      return {
        data: {
          revision: { id: 'r0', snapshot_fingerprint: ALT_FP },
          composition: structuredClone(ALT_COMPOSITION),
        },
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
      };
    }
    return { data: {}, status: 404, statusText: 'NF', headers: {}, config };
  });
  t.after(restore);

  resetState();
  const pending = useMusicStore.getState().ensureVersionRevisionDetail('r0');
  useMusicStore.setState({ currentProjectId: 'p2' });
  resolveDetail();
  const detail = await pending;
  assert.equal(detail, null);
  assert.equal(useMusicStore.getState().versionRevisionDetails.r0, undefined);
});

test('null revision audition and compare are supported', async () => {
  resetState({
    versionSelectedRevisionId: 'r-null',
    versionRevisionDetails: {
      'r-null': { revision: { id: 'r-null' }, composition: null },
    },
  });
  await alignSavedFingerprint();
  const ok = await useMusicStore.getState().setVersionAuditionActive(true);
  assert.equal(ok, true);
  const state = useMusicStore.getState();
  assert.equal(state.versionAuditionActive, true);
  const resolved = resolvePlaybackSource(state, {});
  assert.equal(resolved.source, 'version');
  assert.equal(resolved.composition, null);

  await useMusicStore.getState().refreshVersionComparison();
  assert.equal(useMusicStore.getState().versionCompareStatus, 'ready');
  assert.equal(useMusicStore.getState().versionCompareResult.identical, false);
});
