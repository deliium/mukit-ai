import assert from 'node:assert/strict';
import test from 'node:test';

import axios from 'axios';
import {
  ProjectRevisionConflictError,
  applyAsBranch,
  checkoutBranch,
  commitRevision,
  createBranch,
  createProject,
  deleteProject,
  duplicateProject,
  getProject,
  getRevision,
  listBranches,
  listProjects,
  listRevisions,
  nameRevision,
  patchProject,
  renameBranch,
  restoreRevision,
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
  await patchProject('p1', {
    name: 'Renamed',
    branch_id: 'b1',
    expected_active_branch_id: 'b1',
    expected_working_version: 2,
    expected_source_fingerprint: 'composition.snapshot.v1:abc',
  });
  await duplicateProject('p1');
  await deleteProject('p1');
  await listRevisions('proj/with spaces', {
    branchId: 'br/1',
    limit: 25,
    beforeSequence: 40,
  });
  await getRevision('p1', 'rev/2');
  await commitRevision('p1', { operation_type: 'manual-checkpoint' });
  await nameRevision('p1', 'rev/2', { name: 'Keep' });
  await restoreRevision('p1', 'rev/2', { branch_id: 'b1' });
  await listBranches('p1');
  await createBranch('p1', { name: 'Alt', from_revision_id: 'rev/1' });
  await renameBranch('p1', 'br/2', { name: 'Alt 2' });
  await checkoutBranch('p1', 'br/2', {
    expected_active_branch_id: 'b1',
    expected_working_version: 0,
    expected_head_revision_id: 'rev/1',
  });
  await applyAsBranch('p1', {
    name: 'Chorus B',
    operation_type: 'development-apply',
  });

  assert.deepEqual(
    calls.map((call) => [call.method, call.url]),
    [
      ['get', '/projects'],
      ['post', '/projects'],
      ['get', '/projects/p1'],
      ['patch', '/projects/p1'],
      ['post', '/projects/p1/duplicate'],
      ['delete', '/projects/p1'],
      [
        'get',
        '/projects/proj%2Fwith%20spaces/revisions?branch_id=br%2F1&limit=25&before_sequence=40',
      ],
      ['get', '/projects/p1/revisions/rev%2F2'],
      ['post', '/projects/p1/revisions'],
      ['patch', '/projects/p1/revisions/rev%2F2'],
      ['post', '/projects/p1/revisions/rev%2F2/restore'],
      ['get', '/projects/p1/branches'],
      ['post', '/projects/p1/branches'],
      ['patch', '/projects/p1/branches/br%2F2'],
      ['post', '/projects/p1/branches/br%2F2/checkout'],
      ['post', '/projects/p1/branches/apply-as-branch'],
    ],
  );

  const createData = typeof calls[1].data === 'string' ? JSON.parse(calls[1].data) : calls[1].data;
  const patchData = typeof calls[3].data === 'string' ? JSON.parse(calls[3].data) : calls[3].data;
  assert.equal(createData.name, 'Demo');
  assert.equal(patchData.name, 'Renamed');
  assert.equal(patchData.expected_working_version, 2);
});

test('projectApi preserves structured 409 conflict details', async (t) => {
  const previousAdapter = axios.defaults.adapter;
  axios.defaults.adapter = async () => {
    const error = new Error('conflict');
    error.response = {
      status: 409,
      data: {
        detail: {
          code: 'project_revision_conflict',
          project_id: 'p1',
          expected_working_version: 1,
          current_working_version: 2,
          expected_head_revision_id: 'r1',
          current_head_revision_id: 'r2',
          expected_source_fingerprint_prefix: 'abcd',
          current_working_fingerprint_prefix: 'efgh',
          composition: { should: 'never leak' },
          user_instruction: 'secret text',
        },
      },
    };
    throw error;
  };
  t.after(() => {
    axios.defaults.adapter = previousAdapter;
  });

  await assert.rejects(
    () => patchProject('p1', { composition: { schema_version: 'composition.v2' } }),
    (error) => {
      assert.equal(error instanceof ProjectRevisionConflictError, true);
      assert.equal(error.status, 409);
      assert.equal(error.code, 'project_revision_conflict');
      assert.equal(error.conflict.project_id, 'p1');
      assert.equal(error.conflict.expected_working_version, 1);
      assert.equal(error.conflict.current_working_version, 2);
      assert.equal(error.conflict.composition, undefined);
      assert.equal(error.conflict.user_instruction, undefined);
      return true;
    },
  );
});

test('projectApi falls back for malformed non-conflict errors', async (t) => {
  const previousAdapter = axios.defaults.adapter;
  axios.defaults.adapter = async () => {
    const error = new Error('boom');
    error.response = {
      status: 422,
      data: { detail: [{ msg: 'invalid' }] },
    };
    throw error;
  };
  t.after(() => {
    axios.defaults.adapter = previousAdapter;
  });

  await assert.rejects(
    () => getRevision('p1', 'r1'),
    (error) => {
      assert.equal(error instanceof ProjectRevisionConflictError, false);
      assert.equal(error.status, 422);
      assert.match(error.message, /invalid/);
      return true;
    },
  );
});

test('getRevision returns lazy detail payload shape', async (t) => {
  const previousAdapter = axios.defaults.adapter;
  axios.defaults.adapter = async () => ({
    data: {
      revision: {
        id: 'r1',
        project_id: 'p1',
        sequence: 1,
        operation_type: 'project-create',
        has_user_instruction: false,
        snapshot_fingerprint: 'composition.snapshot.v1:null',
      },
      composition: null,
    },
    status: 200,
    statusText: 'OK',
    headers: {},
    config: {},
  });
  t.after(() => {
    axios.defaults.adapter = previousAdapter;
  });

  const detail = await getRevision('p1', 'r1');
  assert.equal(detail.revision.id, 'r1');
  assert.equal(detail.composition, null);
  assert.equal(detail.revision.has_user_instruction, false);
});
