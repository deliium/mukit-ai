import assert from 'node:assert/strict';
import test from 'node:test';

import {
  PLAYBACK_MIXER_SCOPE_PREVIEW,
  PLAYBACK_MIXER_SCOPE_VERSION,
  PLAYBACK_MIXER_SCOPE_WORKING,
  PLAYBACK_SOURCE_ARRANGEMENT,
  PLAYBACK_SOURCE_DEVELOPMENT,
  PLAYBACK_SOURCE_GENERATION,
  PLAYBACK_SOURCE_KIND_AI_EDIT,
  PLAYBACK_SOURCE_KIND_MOTIF,
  PLAYBACK_SOURCE_KIND_REHARMONIZE,
  PLAYBACK_SOURCE_VERSION,
  PLAYBACK_SOURCE_WORKING,
  buildPlaybackSourceKey,
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
  assert.equal(resolved.sourceKind, 'arrangement');
  assert.equal(resolved.sourceId, 'a1');
  assert.equal(resolved.sourceKey, 'arrangement:a1');
  assert.equal(resolved.mixerScope, 'arrangement');
  assert.equal(resolved.composition, arrangement);
});

test('resolvePlaybackSource prefers generation over version and development', () => {
  const generation = { id: 'gen' };
  const resolved = resolvePlaybackSource(
    {
      editedMusicJson: working,
      arrangementAuditionMode: 'source',
      generationAuditionActive: true,
      generationCandidate: { candidate_id: 'g1', composition: generation },
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
  assert.equal(resolved.source, PLAYBACK_SOURCE_GENERATION);
  assert.equal(resolved.sourceKind, 'generation');
  assert.equal(resolved.sourceId, 'g1');
  assert.equal(resolved.sourceKey, 'generation:g1');
  assert.equal(resolved.mixerScope, PLAYBACK_MIXER_SCOPE_PREVIEW);
  assert.equal(resolved.composition, generation);
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
  assert.equal(resolved.sourceKind, 'version');
  assert.equal(resolved.sourceId, 'r1');
  assert.equal(resolved.sourceKey, 'version:r1');
  assert.equal(resolved.mixerScope, PLAYBACK_MIXER_SCOPE_VERSION);
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
  assert.equal(resolved.sourceKey, 'version:r1');
});

test('resolvePlaybackSource falls back to working', () => {
  const resolved = resolvePlaybackSource({ editedMusicJson: working });
  assert.equal(resolved.source, PLAYBACK_SOURCE_WORKING);
  assert.equal(resolved.sourceKind, 'working');
  assert.equal(resolved.sourceKey, 'working');
  assert.equal(resolved.mixerScope, PLAYBACK_MIXER_SCOPE_WORKING);
  assert.equal(resolved.composition, working);
});

test('exclusiveAuditionPatch clears competitors', () => {
  assert.deepEqual(exclusiveAuditionPatch(PLAYBACK_SOURCE_VERSION, 'source'), {
    developmentAuditionActive: false,
    arrangementAuditionMode: 'source',
    generationAuditionActive: false,
    aiEditAuditionActive: false,
    motifAuditionActive: false,
    reharmonizeAuditionActive: false,
  });
  assert.deepEqual(exclusiveAuditionPatch(PLAYBACK_SOURCE_DEVELOPMENT, 'source'), {
    arrangementAuditionMode: 'source',
    versionAuditionActive: false,
    generationAuditionActive: false,
    aiEditAuditionActive: false,
    motifAuditionActive: false,
    reharmonizeAuditionActive: false,
  });
  assert.deepEqual(exclusiveAuditionPatch(PLAYBACK_SOURCE_WORKING, 'source'), {
    developmentAuditionActive: false,
    arrangementAuditionMode: 'source',
    versionAuditionActive: false,
    generationAuditionActive: false,
    aiEditAuditionActive: false,
    motifAuditionActive: false,
    reharmonizeAuditionActive: false,
  });
});

test('resolvePlaybackSource uses AI edit audition after generation', () => {
  const edit = { id: 'edit' };
  const resolved = resolvePlaybackSource({
    editedMusicJson: working,
    aiEditAuditionActive: true,
    aiEditCandidate: { candidate_id: 'e1', composition: edit },
  });
  assert.equal(resolved.source, PLAYBACK_SOURCE_GENERATION);
  assert.equal(resolved.sourceKind, PLAYBACK_SOURCE_KIND_AI_EDIT);
  assert.equal(resolved.sourceId, 'e1');
  assert.equal(resolved.sourceKey, 'ai_edit:e1');
  assert.equal(resolved.mixerScope, PLAYBACK_MIXER_SCOPE_PREVIEW);
  assert.equal(resolved.composition, edit);
});

test('resolvePlaybackSource wires motif and reharmonize audition kinds', () => {
  const motif = { id: 'motif-comp' };
  const motifResolved = resolvePlaybackSource({
    editedMusicJson: working,
    motifAuditionActive: true,
    motifCandidate: { id: 'm1', composition: motif },
  });
  assert.equal(motifResolved.sourceKind, PLAYBACK_SOURCE_KIND_MOTIF);
  assert.equal(motifResolved.sourceKey, 'motif:m1');
  assert.equal(motifResolved.mixerScope, PLAYBACK_MIXER_SCOPE_PREVIEW);
  assert.equal(motifResolved.composition, motif);

  const reharm = { id: 'reharm-comp' };
  const reharmResolved = resolvePlaybackSource({
    editedMusicJson: working,
    reharmonizeAuditionActive: true,
    reharmonizeCandidate: reharm,
  });
  assert.equal(reharmResolved.sourceKind, PLAYBACK_SOURCE_KIND_REHARMONIZE);
  assert.equal(reharmResolved.sourceKey, buildPlaybackSourceKey('reharmonize', 'reharm-comp'));
  assert.equal(reharmResolved.composition, reharm);
});

test('generation outranks motif and ai_edit', () => {
  const generation = { id: 'gen' };
  const resolved = resolvePlaybackSource({
    editedMusicJson: working,
    generationAuditionActive: true,
    generationCandidate: { composition: generation },
    aiEditAuditionActive: true,
    aiEditCandidate: { composition: { id: 'edit' } },
    motifAuditionActive: true,
    motifCandidate: { composition: { id: 'motif' } },
  });
  assert.equal(resolved.sourceKind, 'generation');
  assert.equal(resolved.composition, generation);
});
