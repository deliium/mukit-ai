import assert from 'node:assert/strict';
import test from 'node:test';

import axios from 'axios';
import { useMusicStore } from '../store/musicStore.js';
import {
  autosaveBodyFromState,
  canCommitFilmScore,
  filmScoreRequestBody,
  hitStatusLines,
  scoringRevisionForCommit,
  sectionLines,
  sparseDialogueRegions,
  tempoChangeLines,
} from './filmScorePlan.js';

const VECTOR_A = {
  sections: [
    { id: 'sec_01', start_bar: 1, bar_count: 8, tempo_bpm: 120, density: 'moderate' },
  ],
  tempo_strategy: { policy: 'section_boundary', changes: [] },
  density_regions: [],
};

test('an empty tempo strategy lists no changes', () => {
  assert.deepEqual(tempoChangeLines(VECTOR_A), []);
  assert.equal(sectionLines(VECTOR_A)[0].barCount, 8);
  assert.equal(sectionLines(VECTOR_A)[0].bpm, 120);
  assert.equal(sectionLines(VECTOR_A)[0].label, 'sec_01');
});

test('vector B keeps the single section-boundary change', () => {
  const plan = {
    tempo_strategy: {
      policy: 'section_boundary',
      changes: [{ tick: 15360, bpm: 128, section_id: 'sec_02', reason_code: 'phrase_fit' }],
    },
  };
  assert.deepEqual(tempoChangeLines(plan), [
    { tick: 15360, bpm: 128, sectionId: 'sec_02', reasonCode: 'phrase_fit' },
  ]);
});

test('a section label and a tempo-change flag stay on the summary rows', () => {
  const plan = {
    sections: [{ id: 'sec_02', label: 'Chase', start_bar: 9, bar_count: 8, tempo_bpm: 128, density: 'moderate' }],
    hit_alignments: [
      { cue_id: 'hit_0000000b', status: 'aligned', tempo_change_added: true },
    ],
  };
  assert.equal(sectionLines(plan)[0].label, 'Chase');
  assert.equal(hitStatusLines(plan)[0].tempoChangeAdded, true);
});

test('commit uses the preview scoring revision', () => {
  const preview = { plan: { scoring_document_revision: 2 } };
  assert.equal(scoringRevisionForCommit(preview), 2);
  assert.equal(scoringRevisionForCommit({ plan: {} }), null);
});

test('the preview body keeps profile, motifs, and duration', () => {
  const prepared = filmScoreRequestBody({
    brief: 'Quiet room',
    instruments: 'acoustic_grand_piano',
    profileId: 'profile-1',
    profileStrength: 'light',
    motifIds: ['theme-a'],
    targetDurationSeconds: '180',
    replaceExisting: false,
  });
  assert.equal(prepared.error, undefined);
  assert.equal(prepared.body.profile_id, 'profile-1');
  assert.equal(prepared.body.profile_strength, 'light');
  assert.deepEqual(prepared.body.motif_ids, ['theme-a']);
  assert.equal(prepared.body.target_duration_seconds, 180);
  const omitted = filmScoreRequestBody({
    brief: 'Quiet room',
    instruments: 'acoustic_grand_piano',
    targetDurationSeconds: '',
  });
  assert.equal(Object.hasOwn(omitted.body, 'profile_id'), false);
  assert.equal(Object.hasOwn(omitted.body, 'target_duration_seconds'), false);
  assert.equal(filmScoreRequestBody({ targetDurationSeconds: '0' }).error, 'film_score_invalid');
});

test('commit stays disabled until a candidate fingerprint exists', () => {
  assert.equal(canCommitFilmScore(null), false);
  assert.equal(canCommitFilmScore({ candidate_fingerprint: null }), false);
  assert.equal(canCommitFilmScore({ candidate_fingerprint: 'abc' }), true);
});

test('a dialogue region stays sparse', () => {
  const plan = {
    density_regions: [{ cue_id: 'hit_dialogue', start_bar: 11, end_bar: 11, density: 'sparse' }],
    sections: [{ id: 'sec_02', start_bar: 9, bar_count: 8, density: 'sparse', tempo_bpm: 120 }],
  };
  const regions = sparseDialogueRegions(plan);
  assert.equal(regions.length, 1);
  assert.equal(regions[0].start_bar, 11);
  assert.equal(regions[0].end_bar, 11);
});

test('the session preview is absent from the composition autosave body', () => {
  const composition = { schema_version: 'composition.v2', bar_count: 4, tracks: [] };
  useMusicStore.setState({
    editedMusicJson: composition,
    filmScorePreview: {
      schema_version: 'film.score.preview.v1',
      plan: { schema_version: 'film.score.plan.v1', sections: [{ bar_count: 90 }] },
      candidate_fingerprint: 'fp',
    },
  });
  const body = autosaveBodyFromState(useMusicStore.getState());
  assert.equal(body.composition, composition);
  assert.equal(Object.hasOwn(body, 'filmScorePreview'), false);
  assert.equal(JSON.stringify(body).includes('film.score.plan.v1'), false);
  useMusicStore.setState({ filmScorePreview: null, editedMusicJson: null });
});

test('commit posts the preview scoring revision', async () => {
  const previousAdapter = axios.defaults.adapter;
  let commitBody = null;
  axios.defaults.adapter = async (config) => {
    const url = String(config.url || '');
    if (url.includes('/film-score/commit')) {
      commitBody = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
      return { data: { committed: true }, status: 200, statusText: 'OK', headers: {}, config };
    }
    if (url.includes('/video-scoring')) {
      return {
        data: { document_revision: 3, hit_points: [] },
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
      };
    }
    return {
      data: {
        id: 'p1',
        name: 'Scene',
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
        composition: null,
        active_branch_id: 'b1',
        current_revision_id: 'r1',
        working_version: 1,
        working_fingerprint: 'c'.repeat(64),
      },
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    };
  };
  useMusicStore.setState({
    currentProjectId: 'p1',
    activeBranchId: 'b1',
    workingVersion: 0,
    currentRevisionId: 'r1',
    workingFingerprint: 'a'.repeat(64),
    pictureScoring: { document_revision: 9 },
    filmScorePreview: {
      candidate: { schema_version: 'composition.v2' },
      candidate_fingerprint: 'b'.repeat(64),
      artifact_log: [],
      artifact_role_map: { brief: { artifact_id: 'x', content_type: 'agent.brief.v1' } },
      plan: { scoring_document_revision: 2 },
    },
  });
  try {
    const ok = await useMusicStore.getState().commitFilmScore({ replaceExisting: false });
    assert.equal(ok, true);
    assert.equal(commitBody.expected_document_revision, 2);
    assert.equal(useMusicStore.getState().filmScorePreview, null);
  } finally {
    axios.defaults.adapter = previousAdapter;
    useMusicStore.setState({
      currentProjectId: null,
      filmScorePreview: null,
      pictureScoring: null,
      filmScoreStatus: 'idle',
      filmScoreError: '',
    });
  }
});
