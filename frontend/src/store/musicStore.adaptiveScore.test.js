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

test('layer map stores the response and a project switch clears it', async () => {
  seedScore();
  useMusicStore.setState({
    adaptiveLayerMuteSnapshot: { 'track-bass': true },
    editedMusicJson: { schema_version: 'composition.v2', tracks: [{ id: 'track-bass', events: [] }] },
  });
  const compositionBefore = useMusicStore.getState().editedMusicJson;
  const mapped = {
    schema_version: 'adaptive.layer.intensity.v1',
    layers: [
      {
        layer_id: 'layer-bass',
        role: 'bass',
        material_kind: 'track_range',
        track_ids: ['track-bass'],
        active: true,
        audible: true,
        reason: 'in_window',
      },
    ],
  };
  const restore = installAxiosStub(async () => ({ status: 200, data: mapped }));
  try {
    await useMusicStore.getState().mapAdaptiveLayers(0.5, 0);
    assert.equal(useMusicStore.getState().adaptiveLayerIntensity.layers[0].layer_id, 'layer-bass');
    assert.equal(useMusicStore.getState().editedMusicJson, compositionBefore);
  } finally {
    restore();
  }
  const open = installAxiosStub(async () => ({
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
    assert.equal(state.adaptiveLayerIntensity, null);
    assert.equal(state.adaptiveLayerPlanPreview, null);
    assert.equal(state.adaptiveLayerMuteSnapshot, null);
    assert.equal(state.adaptiveLayerIntensityError, '');
  } finally {
    open();
  }
});

test('session mute follows active track rows and restore writes the snapshot', () => {
  seedScore();
  const compositionBefore = useMusicStore.getState().editedMusicJson;
  useMusicStore.setState({
    trackControls: {
      'track-bass': { muted: true },
      'track-perc': { muted: false },
      'track-shared': { muted: true },
      'track-drums': { muted: true },
    },
    adaptiveLayerIntensity: {
      layers: [
        { layer_id: 'layer-bass', material_kind: 'track_range', track_ids: ['track-bass'], active: true },
        { layer_id: 'layer-perc', material_kind: 'track_range', track_ids: ['track-perc'], active: false },
        { layer_id: 'layer-low', material_kind: 'track_range', track_ids: ['track-shared'], active: false },
        { layer_id: 'layer-high', material_kind: 'track_range', track_ids: ['track-shared'], active: true },
      ],
    },
  });
  useMusicStore.getState().applyAdaptiveLayerSessionMute();
  const muted = (id) => useMusicStore.getState().trackControls[id].muted;
  assert.equal(muted('track-bass'), false);
  assert.equal(muted('track-perc'), true);
  assert.equal(muted('track-shared'), false);
  assert.equal(muted('track-drums'), true);
  assert.deepEqual(useMusicStore.getState().adaptiveLayerMuteSnapshot, {
    'track-bass': true,
    'track-perc': false,
    'track-shared': true,
  });
  assert.equal(useMusicStore.getState().editedMusicJson, compositionBefore);
  useMusicStore.getState().restoreAdaptiveLayerMute();
  assert.equal(muted('track-bass'), true);
  assert.equal(muted('track-perc'), false);
  assert.equal(muted('track-shared'), true);
  assert.equal(useMusicStore.getState().adaptiveLayerMuteSnapshot, null);
});

test('apply plan threads document revision from the first command', async () => {
  seedScore();
  const revisions = [];
  const restore = installAxiosStub(async (config) => {
    const body = JSON.parse(config.data);
    revisions.push(body.expected_document_revision);
    const next = body.expected_document_revision + 1;
    return {
      status: 200,
      data: {
        score: PREVIOUS,
        document_revision: next,
        binding_status: 'fresh',
        findings: [],
        is_default: true,
        created_at: 't',
        updated_at: 't',
      },
    };
  });
  try {
    await useMusicStore.getState().applyAdaptiveLayerPlan([
      { name: 'Pad', role: 'ambient', intensity_min: 0, intensity_max: 1, mix_hint: 'bed', material: { kind: 'section', section_id: 's' } },
      { name: 'Bass', role: 'bass', intensity_min: 0.5, intensity_max: 1, mix_hint: 'bed', material: { kind: 'section', section_id: 's' } },
    ]);
    assert.deepEqual(revisions, [2, 3]);
  } finally {
    restore();
  }
});

function playbackSnapshot(overrides = {}) {
  return {
    schema_version: 'adaptive.playback.runtime.v1',
    playback_id: 'pbr_0123abcd',
    mode: 'simulation',
    transport: 'playing',
    runtime_state_id: 'state-exploration',
    position_tick: 0,
    bar: 1,
    beat: 1,
    queue: [],
    pending_transition: null,
    instructions: {
      stop: false,
      seek_tick: null,
      loop: { enabled: false, start_tick: 0, end_tick: 0 },
      track_gains: [],
    },
    telemetry: { last_event: 'started', step_count: 1, rejected_request_count: 0 },
    warnings: [],
    ...overrides,
  };
}

test('playback snapshot stays on the session and a rejected state leaves the composition', async () => {
  seedScore();
  const composition = useMusicStore.getState().editedMusicJson;
  const started = playbackSnapshot();
  const rejected = playbackSnapshot({
    warnings: [{ code: 'dangling_state_ref', severity: 'warning', target_id: null, message: 'missing' }],
    telemetry: { last_event: 'request_rejected', step_count: 2, rejected_request_count: 1 },
  });
  const restore = installAxiosStub(async (config) => {
    if (String(config.url).endsWith('/playback') && config.method === 'post') {
      const body = JSON.parse(config.data);
      assert.equal(body.mode, 'simulation');
      assert.equal(body.expected_document_revision, 2);
      return { status: 200, data: started };
    }
    if (String(config.url).endsWith('/playback/commands')) {
      const body = JSON.parse(config.data);
      assert.equal(body.to_state_id, 'state-missing');
      return { status: 200, data: rejected };
    }
    return {
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
    };
  });
  try {
    await useMusicStore.getState().startAdaptivePlayback('simulation');
    assert.equal(useMusicStore.getState().adaptivePlayback.playback_id, 'pbr_0123abcd');
    assert.equal(useMusicStore.getState().adaptiveSelectedStateId, 'state-exploration');
    await useMusicStore.getState().requestAdaptivePlaybackState('state-missing');
    const state = useMusicStore.getState();
    assert.equal(state.adaptivePlayback.transport, 'playing');
    assert.equal(state.adaptivePlayback.instructions.stop, false);
    assert.equal(state.editedMusicJson, composition);
    await useMusicStore.getState().openProject('p2');
    assert.equal(useMusicStore.getState().adaptivePlayback, null);
    assert.equal(useMusicStore.getState().adaptiveSelectedStateId, null);
  } finally {
    restore();
  }
});

test('context sample does not select a card or post playback commands', async () => {
  seedScore();
  const calls = [];
  let selections = 0;
  const originalSelect = useMusicStore.getState().selectAdaptiveState;
  useMusicStore.setState({
    selectAdaptiveState: (...args) => {
      selections += 1;
      return originalSelect(...args);
    },
  });
  const restore = installAxiosStub(async (config) => {
    calls.push(`${config.method} ${config.url}`);
    if (String(config.url).endsWith('/context/samples') && config.method === 'post') {
      return {
        status: 200,
        data: {
          schema_version: 'adaptive.musical_context.v1',
          context_id: 'actx_0123abcd',
          sample_index: 1,
          musical_state_id: 'state-exploration',
          intensity: null,
          dwell_count: 0,
          emitted: [],
          warnings: [],
          telemetry: {
            sample_count: 1,
            state_change_count: 0,
            intensity_emit_count: 0,
            rejected_sample_count: 0,
          },
          document_revision: 2,
        },
      };
    }
    return { status: 200, data: null };
  });
  try {
    const selected = useMusicStore.getState().adaptiveSelectedStateId;
    const snapshot = await useMusicStore.getState().sendAdaptiveContextSample({
      schema_version: 'adaptive.context.external.v1',
      values: { danger: 0.49 },
    });
    assert.equal(snapshot.musical_state_id, 'state-exploration');
    assert.equal(selections, 0);
    assert.equal(useMusicStore.getState().adaptiveSelectedStateId, selected);
    assert.equal(calls.some((entry) => entry.includes('/playback/commands')), false);
    assert.equal(useMusicStore.getState().adaptiveMusicalContextError, '');
  } finally {
    useMusicStore.setState({ selectAdaptiveState: originalSelect });
    restore();
  }
});

function continuationSnapshot(overrides = {}) {
  return {
    schema_version: 'adaptive.runtime.continuation.v1',
    continuation_id: 'arcn_0123abcd',
    job_id: 'arcj_0123abcd',
    mode: 'continuation',
    anchor_bar: 1,
    reserved_start_bar: 1,
    reserved_end_bar: 5,
    target_start_bar: 6,
    target_end_bar: 13,
    deadline_tick: 9600,
    fallback_kind: 'reuse_loop',
    source: 'fallback',
    job_status: 'pending',
    applicable: true,
    audible: false,
    runtime_state_id: 'state-exploration',
    intensity: 0.2,
    context: {
      schema_version: 'adaptive.runtime.context.v1',
      theme_ids: [],
      harmony_tail: null,
      harmony_chord_count: 0,
      recent_start_bar: 1,
      recent_end_bar: 1,
      repetition_count: 0,
      energy: [0.2],
    },
    warnings: [],
    telemetry: {
      arm_count: 1,
      model_apply_count: 0,
      late_discard_count: 0,
      failure_count: 0,
      fallback_count: 1,
    },
    document_revision: 2,
    ...overrides,
  };
}

test('Fill ahead with Continuous toggle latches continuous on start', async () => {
  seedScore();
  useMusicStore.setState({
    adaptivePlayback: playbackSnapshot(),
    adaptiveContinuation: null,
    adaptiveContinuousEnabled: true,
    adaptiveContinuationError: '',
  });
  let startBody = null;
  const restore = installAxiosStub(async (config) => {
    const url = String(config.url);
    if (url.endsWith('/continuation') && config.method === 'post') {
      startBody = JSON.parse(config.data);
      return {
        status: 200,
        data: continuationSnapshot({
          continuous: true,
          music_state: {
            schema_version: 'adaptive.runtime.music_state.v1',
            active_theme_ids: [],
            motif_usage: [],
            harmony_trajectory: [],
            repetition_history: [],
            energy: [],
            tension: [],
            orchestration_history: [],
            runtime_state_id: 'state-exploration',
            summary_digest: null,
            virtual_bar: 5,
            guard_flags: [],
          },
        }),
      };
    }
    if (url.endsWith('/continuation/maintain')) {
      return { status: 200, data: continuationSnapshot({ continuous: true }) };
    }
    if (url.includes('/continuation/buffer')) {
      return { status: 200, data: { schema_version: 'adaptive.runtime.buffer.v1', events: [] } };
    }
    return { status: 204, data: null };
  });
  try {
    await useMusicStore.getState().fillAdaptiveContinuation();
    assert.equal(startBody.continuous, true);
    assert.equal(startBody.mode, 'continuation');
    assert.equal(useMusicStore.getState().adaptiveContinuation.continuous, true);
  } finally {
    restore();
    useMusicStore.setState({
      adaptivePlayback: null,
      adaptiveContinuation: null,
      adaptiveContinuousEnabled: false,
      adaptiveContinuationError: '',
    });
  }
});

test('Continuous toggle defaults off and does not auto-arm', () => {
  seedScore();
  assert.equal(useMusicStore.getState().adaptiveContinuousEnabled, false);
  assert.equal(useMusicStore.getState().adaptiveContinuation, null);
});

test('a rejected maintain leaves playback playing', async () => {
  seedScore();
  useMusicStore.setState({
    adaptivePlayback: playbackSnapshot(),
    adaptiveContinuation: continuationSnapshot(),
  });
  const restore = installAxiosStub(async () => {
    const error = new Error('late');
    error.response = {
      status: 404,
      data: { detail: { code: 'continuation_not_running', message: 'not running' } },
    };
    throw error;
  });
  try {
    await useMusicStore.getState().maintainAdaptiveContinuation();
    const state = useMusicStore.getState();
    assert.equal(state.adaptivePlayback.transport, 'playing');
    assert.equal(state.adaptiveContinuationError, 'continuation_not_running');
  } finally {
    restore();
    useMusicStore.setState({ adaptivePlayback: null, adaptiveContinuation: null, adaptiveContinuationError: '' });
  }
});

test('a repeated maintain at the same bar keeps the job id', async () => {
  seedScore();
  useMusicStore.setState({
    adaptivePlayback: playbackSnapshot(),
    adaptiveContinuation: continuationSnapshot({ job_id: null, job_status: 'idle' }),
  });
  let maintains = 0;
  const restore = installAxiosStub(async (config) => {
    if (String(config.url).endsWith('/continuation') && config.method === 'post') {
      return { status: 200, data: continuationSnapshot({ job_id: null, job_status: 'idle' }) };
    }
    if (String(config.url).endsWith('/continuation/maintain')) {
      maintains += 1;
      return { status: 200, data: continuationSnapshot() };
    }
    return { status: 204, data: null };
  });
  try {
    const first = await useMusicStore.getState().maintainAdaptiveContinuation();
    const second = await useMusicStore.getState().maintainAdaptiveContinuation();
    assert.equal(maintains, 2);
    assert.equal(first.job_id, 'arcj_0123abcd');
    assert.equal(second.job_id, first.job_id);
    assert.equal(useMusicStore.getState().adaptivePlayback.transport, 'playing');
  } finally {
    restore();
    useMusicStore.setState({ adaptivePlayback: null, adaptiveContinuation: null });
  }
});
