import assert from 'node:assert/strict';
import test from 'node:test';

import axios from 'axios';
import { useMusicStore } from './musicStore.js';

const PREVIOUS = {
  id: 'ascore_0123456789abcdef',
  name: 'Exploration cue',
  schema_version: 'adaptive.score.v1',
  initial_state_id: 'state-exploration',
  default_state_id: 'state-exploration',
  states: [
    { id: 'state-exploration', name: 'Exploration' },
  ],
  transitions: [],
};

function installAxiosStub(handler) {
  const previousAdapter = axios.defaults.adapter;
  axios.defaults.adapter = handler;
  return () => {
    axios.defaults.adapter = previousAdapter;
  };
}

function seedScore() {
  useMusicStore.setState({
    currentProjectId: 'p1',
    adaptiveScoreId: PREVIOUS.id,
    adaptiveDocumentRevision: 2,
    adaptiveBindingStatus: 'fresh',
    adaptiveScore: PREVIOUS,
    adaptiveFindings: [],
    adaptiveSelectedStateId: 'state-exploration',
    adaptiveCommandError: '',
    adaptiveStatus: 'ready',
    editedMusicJson: { schema_version: 'composition.v2', tracks: [] },
    workingVersion: 3,
  });
}

test('command success replaces the score and bumps the revision', async () => {
  seedScore();
  const restore = installAxiosStub(async (config) => {
    const body = JSON.parse(config.data);
    assert.equal(body.expected_document_revision, 2);
    assert.equal(body.op, 'create_state');
    return {
      status: 200,
      data: {
        score: {
          ...PREVIOUS,
          states: [
            ...PREVIOUS.states,
            { id: 'state-combat', name: 'Combat' },
          ],
        },
        document_revision: 3,
        binding_status: 'fresh',
        findings: [],
        is_default: true,
        created_at: 't',
        updated_at: 't',
      },
    };
  });
  try {
    await useMusicStore.getState().runAdaptiveScoreCommand('create_state', { name: 'Combat' });
    const state = useMusicStore.getState();
    assert.equal(state.adaptiveDocumentRevision, 3);
    assert.equal(state.adaptiveScore.states.length, 2);
    assert.equal(state.editedMusicJson.schema_version, 'composition.v2');
    assert.equal(state.workingVersion, 3);
  } finally {
    restore();
  }
});

test('422 keeps the previous score and stores findings', async () => {
  seedScore();
  const restore = installAxiosStub(async () => {
    const error = new Error('bad loop');
    error.response = {
      status: 422,
      data: {
        detail: {
          code: 'loop_bounds',
          message: 'Loop on state-combat ends at bar 2 before start bar 4.',
          details: {
            findings: [
              {
                code: 'loop_bounds',
                severity: 'error',
                target_id: 'state-combat',
                message: 'Loop on state-combat ends at bar 2 before start bar 4.',
              },
            ],
          },
        },
      },
    };
    throw error;
  });
  try {
    await useMusicStore.getState().runAdaptiveScoreCommand('assign_loop', {
      state_id: 'state-combat',
      enabled: true,
      start_bar: 4,
      end_bar: 2,
    });
    const state = useMusicStore.getState();
    assert.equal(state.adaptiveDocumentRevision, 2);
    assert.equal(state.adaptiveScore, PREVIOUS);
    assert.equal(state.adaptiveFindings[0].code, 'loop_bounds');
    assert.match(state.adaptiveFindings[0].message, /state-combat/);
  } finally {
    restore();
  }
});

test('select changes only the selected state id', () => {
  seedScore();
  useMusicStore.getState().selectAdaptiveState('state-combat');
  const state = useMusicStore.getState();
  assert.equal(state.adaptiveSelectedStateId, 'state-combat');
  assert.equal(state.adaptiveScore, PREVIOUS);
  assert.equal(state.adaptiveDocumentRevision, 2);
});

test('opening another project clears the adaptive session', async () => {
  seedScore();
  useMusicStore.setState({
    adaptiveScheduledTransition: { request_id: 'treq_aaaaaaaa', latency_ms: 1500 },
  });
  const restore = installAxiosStub(async () => ({
    status: 200,
    data: {
      id: 'p2',
      name: 'Next',
      composition: {
        schema_version: 'composition.v2',
        tempo: 100,
        key: 'C major',
        time_signature: '4/4',
        ticks_per_quarter: 480,
        duration_ticks: 1920,
        bar_count: 1,
        sections: [],
        tracks: [],
      },
      active_branch_id: null,
      working_version: 0,
    },
  }));
  try {
    await useMusicStore.getState().openProject('p2');
    const state = useMusicStore.getState();
    assert.equal(state.currentProjectId, 'p2');
    assert.equal(state.adaptiveScore, null);
    assert.equal(state.adaptiveScoreId, null);
    assert.equal(state.adaptiveSelectedStateId, null);
    assert.deepEqual(state.adaptiveFindings, []);
    assert.equal(state.adaptiveScheduledTransition, null);
  } finally {
    restore();
  }
});

test('schedule stores latency and a conflict leaves the pending object', async () => {
  seedScore();
  const pending = {
    request_id: 'treq_aaaaaaaa',
    latency_ms: 1500,
    boundary_tick: 3840,
    quantization: 'bar',
  };
  useMusicStore.setState({ adaptiveScheduledTransition: pending });
  const restore = installAxiosStub(async (config) => {
    if (config.url.endsWith('/transition-requests') && config.method === 'post') {
      const body = JSON.parse(config.data);
      assert.equal(body.from_state_id, 'state-exploration');
      assert.equal(body.position_tick, 2400);
      assert.equal(body.quantization, undefined);
      return {
        status: 201,
        data: {
          request_id: 'treq_bbbbbbbb',
          quantization: 'bar',
          boundary_tick: 3840,
          latency_ms: 1500,
          realization: { kind: 'cut' },
        },
      };
    }
    const error = new Error('conflict');
    error.response = {
      status: 409,
      data: { detail: { code: 'adaptive_score_conflict', message: 'stale' } },
    };
    throw error;
  });
  try {
    await useMusicStore.getState().scheduleAdaptiveTransition('state-combat', 2400);
    assert.equal(useMusicStore.getState().adaptiveScheduledTransition.latency_ms, 1500);
    axios.defaults.adapter = async () => {
      const error = new Error('conflict');
      error.response = {
        status: 409,
        data: { detail: { code: 'adaptive_score_conflict', message: 'stale' } },
      };
      throw error;
    };
    await useMusicStore.getState().scheduleAdaptiveTransition('state-combat', 1);
    assert.equal(useMusicStore.getState().adaptiveScheduledTransition.request_id, 'treq_bbbbbbbb');
    assert.equal(useMusicStore.getState().adaptiveScheduledTransition.latency_ms, 1500);
  } finally {
    restore();
  }
});
