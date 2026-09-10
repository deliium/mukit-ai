import assert from 'node:assert/strict';
import test from 'node:test';

import {
  PLAYBACK_SOURCE_ARRANGEMENT,
  PLAYBACK_SOURCE_DEVELOPMENT,
  PLAYBACK_SOURCE_VERSION,
  PLAYBACK_SOURCE_WORKING,
  exclusiveAuditionPatch,
  resolvePlaybackSource,
} from './playbackSource.js';

const working = { id: 'working' };
const development = { id: 'dev' };
const arrangement = { id: 'arr' };
const version = { id: 'ver' };

test('resolvePlaybackSource prefers arrangement over version and development', () => {
  const resolved = resolvePlaybackSource(
    {
      editedMusicJson: working,
      arrangementAuditionMode: 'candidate',
      arrangementCandidates: [{ candidate_id: 'a1', composition: arrangement }],
      arrangementSelectedCandidateId: 'a1',
      versionAuditionActive: true,
      versionSelectedRevisionId: 'r1',
      versionRevisionDetails: { r1: { composition: version } },
      developmentAuditionActive: true,
      developmentCandidates: [{ candidate_id: 'd1', composition: development }],
      developmentSelectedCandidateId: 'd1',
    },
    {
      findArrangementCandidateById: (list, id) => list.find((c) => c.candidate_id === id),
      findDevelopmentCandidateById: (list, id) => list.find((c) => c.candidate_id === id),
    },
  );
  assert.equal(resolved.source, PLAYBACK_SOURCE_ARRANGEMENT);
  assert.equal(resolved.composition, arrangement);
});

test('resolvePlaybackSource uses version audition before development', () => {
  const resolved = resolvePlaybackSource(
    {
      editedMusicJson: working,
      arrangementAuditionMode: 'source',
      versionAuditionActive: true,
      versionSelectedRevisionId: 'r1',
      versionRevisionDetails: { r1: { composition: version } },
      developmentAuditionActive: true,
      developmentCandidates: [{ candidate_id: 'd1', composition: development }],
      developmentSelectedCandidateId: 'd1',
    },
    {
      findDevelopmentCandidateById: (list, id) => list.find((c) => c.candidate_id === id),
    },
  );
  assert.equal(resolved.source, PLAYBACK_SOURCE_VERSION);
  assert.equal(resolved.composition, version);
});

test('resolvePlaybackSource allows null version composition', () => {
  const resolved = resolvePlaybackSource({
    editedMusicJson: working,
    versionAuditionActive: true,
    versionSelectedRevisionId: 'r1',
    versionRevisionDetails: { r1: { composition: null } },
  });
  assert.equal(resolved.source, PLAYBACK_SOURCE_VERSION);
  assert.equal(resolved.composition, null);
});

test('resolvePlaybackSource falls back to working', () => {
  const resolved = resolvePlaybackSource({ editedMusicJson: working });
  assert.equal(resolved.source, PLAYBACK_SOURCE_WORKING);
  assert.equal(resolved.composition, working);
});

test('exclusiveAuditionPatch clears competitors', () => {
  assert.deepEqual(exclusiveAuditionPatch(PLAYBACK_SOURCE_VERSION, 'source'), {
    developmentAuditionActive: false,
    arrangementAuditionMode: 'source',
  });
  assert.deepEqual(exclusiveAuditionPatch(PLAYBACK_SOURCE_DEVELOPMENT, 'source'), {
    arrangementAuditionMode: 'source',
    versionAuditionActive: false,
  });
  assert.deepEqual(exclusiveAuditionPatch(PLAYBACK_SOURCE_WORKING, 'source'), {
    developmentAuditionActive: false,
    arrangementAuditionMode: 'source',
    versionAuditionActive: false,
  });
});
