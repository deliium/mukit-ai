import assert from 'node:assert/strict';
import test from 'node:test';

import { projectPersistRevisionKey } from './projectPersistRevision.js';
import { buildThemeReuseRequest, reuseParameters, themeReuseForProject } from './musicalUniverseReuse.js';

const branch = {
  branch_id: 'branch-1',
  expected_active_branch_id: 'branch-1',
  expected_working_version: 3,
  expected_head_revision_id: 'rev-9',
  expected_source_fingerprint: 'a'.repeat(64),
};

const spec = {
  operation: 'transpose',
  parameters: { transpose_semitones: 2 },
  destinationProjectId: 'project-b',
  destinationTrackId: 'track_melody',
  destinationStartBar: 2,
  branch,
  expectedUniverseRevision: 4,
};

function cleanState() {
  const editedMusicJson = { schema_version: 'composition.v2' };
  const generationMeta = null;
  return {
    currentProjectId: 'project-a',
    saveStatus: 'saved',
    editedMusicJson,
    generationMeta,
    lastSavedPersistRevision: projectPersistRevisionKey(editedMusicJson, generationMeta),
    activeBranchId: 'branch-1',
    workingVersion: 3,
    currentRevisionId: 'rev-9',
    workingFingerprint: 'a'.repeat(64),
  };
}

test('reuse request names the transform and the destination branch', () => {
  const request = buildThemeReuseRequest(spec);
  assert.equal(request.operation, 'transpose');
  assert.equal(request.parameters.transpose_semitones, 2);
  assert.equal(request.destination_project_id, 'project-b');
  assert.equal(request.destination_track_id, 'track_melody');
  assert.equal(request.destination_start_bar, 2);
  assert.equal(request.branch_id, 'branch-1');
  assert.equal(request.expected_active_branch_id, 'branch-1');
  assert.equal(request.expected_working_version, 3);
  assert.equal(request.expected_head_revision_id, 'rev-9');
  assert.equal(request.expected_source_fingerprint, 'a'.repeat(64));
  assert.equal(request.expected_universe_revision, 4);
  assert.equal('events' in request, false);
  assert.equal('pitch' in request, false);
  assert.equal('composition' in request, false);
});

test('dirty drafts disable reuse and a clean project enables the payload', () => {
  const clean = cleanState();
  for (const saveStatus of ['unsaved', 'saving', 'conflict']) {
    const result = themeReuseForProject({ ...clean, saveStatus }, spec);
    assert.equal(result.enabled, false);
    assert.equal(result.request, null);
  }
  const mismatched = themeReuseForProject(
    { ...clean, lastSavedPersistRevision: 'stale-persist-revision' },
    spec,
  );
  assert.equal(mismatched.enabled, false);
  const enabled = themeReuseForProject(clean, spec);
  assert.equal(enabled.enabled, true);
  assert.equal(enabled.request.operation, 'transpose');
  assert.equal('composition' in enabled.request, false);
});

test('each mechanical operation sends only its own parameters', () => {
  const fields = {
    semitones: 2,
    axis: 'D4',
    numerator: 2,
    denominator: 1,
    steps: 3,
    interval: -2,
    stepTicks: 960,
  };
  assert.deepEqual(reuseParameters('repeat', fields), {});
  assert.deepEqual(reuseParameters('transpose', fields), { transpose_semitones: 2 });
  assert.deepEqual(reuseParameters('inversion', { ...fields, axis: '  ' }), {});
  assert.deepEqual(reuseParameters('inversion', fields), { inversion_axis_pitch: 'D4' });
  assert.deepEqual(reuseParameters('augmentation', fields), {
    time_scale_numerator: 2,
    time_scale_denominator: 1,
  });
  assert.deepEqual(reuseParameters('diminution', fields), {
    time_scale_numerator: 2,
    time_scale_denominator: 1,
  });
  assert.deepEqual(reuseParameters('sequence', fields), {
    sequence_steps: 3,
    sequence_interval_semitones: -2,
    sequence_step_ticks: 960,
  });
  const sequenceRequest = buildThemeReuseRequest({
    ...spec,
    operation: 'sequence',
    parameters: reuseParameters('sequence', fields),
  });
  assert.equal('pitch' in sequenceRequest, false);
  assert.equal('pitch' in sequenceRequest.parameters, false);
  assert.equal('events' in sequenceRequest, false);
  assert.equal('composition' in sequenceRequest, false);
});

test('calling the reuse helper does not fetch', () => {
  let fetched = false;
  const previous = globalThis.fetch;
  globalThis.fetch = () => {
    fetched = true;
    return Promise.resolve();
  };
  try {
    themeReuseForProject(cleanState(), spec);
    buildThemeReuseRequest(spec);
  } finally {
    globalThis.fetch = previous;
  }
  assert.equal(fetched, false);
});
