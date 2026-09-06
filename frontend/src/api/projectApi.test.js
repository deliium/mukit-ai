import assert from 'node:assert/strict';
import test from 'node:test';

import axios from 'axios';
import {
  createProject,
  deleteProject,
  duplicateProject,
  getProject,
  listProjects,
  patchProject,
} from './projectApi.js';

test('projectApi helpers use expected HTTP methods and paths', async (t) => {
  const calls = [];
  const previousAdapter = axios.defaults.adapter;
  axios.defaults.adapter = async (config) => {
    calls.push({
      method: String(config.method || 'get').toLowerCase(),
      url: config.url,
      data: config.data,
    });
    return {
      data: config.method === 'delete' || config.method === 'DELETE'
        ? null
        : { id: 'p1', name: 'Demo', projects: [{ id: 'p1', name: 'Demo' }] },
      status: config.method === 'delete' || config.method === 'DELETE' ? 204 : 200,
      statusText: 'OK',
      headers: {},
      config,
    };
  };
  t.after(() => {
    axios.defaults.adapter = previousAdapter;
  });

  await listProjects();
  await createProject({ name: 'Demo' });
  await getProject('p1');
  await patchProject('p1', { name: 'Renamed' });
  await duplicateProject('p1');
  await deleteProject('p1');

  assert.deepEqual(
    calls.map((call) => [call.method, call.url]),
    [
      ['get', '/projects'],
      ['post', '/projects'],
      ['get', '/projects/p1'],
      ['patch', '/projects/p1'],
      ['post', '/projects/p1/duplicate'],
      ['delete', '/projects/p1'],
    ],
  );

  const createData = typeof calls[1].data === 'string' ? JSON.parse(calls[1].data) : calls[1].data;
  const patchData = typeof calls[3].data === 'string' ? JSON.parse(calls[3].data) : calls[3].data;
  assert.equal(createData.name, 'Demo');
  assert.equal(patchData.name, 'Renamed');
});
