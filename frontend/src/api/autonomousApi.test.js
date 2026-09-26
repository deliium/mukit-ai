import assert from 'node:assert/strict';
import test from 'node:test';

import axios from 'axios';
import {
  approveAutonomousCheckpoint,
  branchAutonomousStage,
  instructAutonomousStage,
  openAutonomousStage,
  pauseAutonomousRun,
  previewAutonomousPlan,
  rejectAutonomousArrangement,
  retryAutonomousStage,
} from './musicApi.js';

test('autonomous control helpers hit the contract paths', async (t) => {
  const calls = [];
  const previousAdapter = axios.defaults.adapter;
  axios.defaults.adapter = async (config) => {
    calls.push({
      method: String(config.method || 'get').toLowerCase(),
      url: config.url,
      data: typeof config.data === 'string' ? JSON.parse(config.data) : config.data,
    });
    return {
      data: { run_id: 'run-1', status: 'paused', stages: [] },
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    };
  };
  const logs = [];
  const originalDebug = console.debug;
  const originalError = console.error;
  console.debug = (...args) => {
    logs.push(args);
  };
  console.error = (...args) => {
    logs.push(args);
  };
  t.after(() => {
    axios.defaults.adapter = previousAdapter;
    console.debug = originalDebug;
    console.error = originalError;
  });

  const brief = { schema_version: 'creative.brief.v1', duration_seconds: 150 };
  await previewAutonomousPlan(brief);
  await pauseAutonomousRun('11111111-1111-4111-8111-111111111111');
  await approveAutonomousCheckpoint('run-1', 'motif');
  await rejectAutonomousArrangement('run-1');
  await instructAutonomousStage(
    'run-1',
    'arrangement',
    'Keep the melody, but use a smaller string arrangement.',
  );
  await retryAutonomousStage('run-1', 'arrangement');
  await openAutonomousStage('run-1', 'symbolic');
  await branchAutonomousStage('run-1', 'symbolic', 'From theme');

  assert.deepEqual(calls.map((call) => [call.method, call.url]), [
    ['post', '/ai/agents/autonomous/plans'],
    ['post', '/ai/agents/autonomous/runs/pause'],
    ['post', '/ai/agents/autonomous/runs/run-1/checkpoints/motif/approve'],
    ['post', '/ai/agents/autonomous/runs/run-1/checkpoints/arrangement/reject'],
    ['post', '/ai/agents/autonomous/runs/run-1/stages/arrangement/instruction'],
    ['post', '/ai/agents/autonomous/runs/run-1/stages/arrangement/retry'],
    ['post', '/ai/agents/autonomous/runs/run-1/stages/symbolic/open'],
    ['post', '/ai/agents/autonomous/runs/run-1/stages/symbolic/branch'],
  ]);
  assert.equal(
    calls[1].data.operation_run_id,
    '11111111-1111-4111-8111-111111111111',
  );
  assert.equal(
    calls[4].data.text,
    'Keep the melody, but use a smaller string arrangement.',
  );
  const logged = JSON.stringify(logs);
  assert.equal(logged.includes('smaller string arrangement'), false);
});
