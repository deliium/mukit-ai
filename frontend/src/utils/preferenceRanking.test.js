import assert from 'node:assert/strict';
import test from 'node:test';

import { orderCandidatesByRanking } from './preferenceRanking.js';

function candidates() {
  return [
    { candidate_id: 'cand_sparse_00001', pitch: 'C4' },
    { candidate_id: 'cand_middle_00001', pitch: 'E4' },
    { candidate_id: 'cand_dense_000001', pitch: 'G4' },
  ];
}

test('orderCandidatesByRanking follows the ranked ids', () => {
  const ordered = orderCandidatesByRanking(candidates(), {
    ranking_applied: true,
    ordered_candidate_ids: ['cand_dense_000001', 'cand_middle_00001', 'cand_sparse_00001'],
  });
  assert.deepEqual(ordered.map((item) => item.candidate_id), [
    'cand_dense_000001',
    'cand_middle_00001',
    'cand_sparse_00001',
  ]);
  assert.equal(candidates()[0].candidate_id, 'cand_sparse_00001');
});

test('orderCandidatesByRanking keeps input order when ranking is not applied', () => {
  const input = candidates();
  const ordered = orderCandidatesByRanking(input, {
    ranking_applied: false,
    ordered_candidate_ids: ['cand_dense_000001', 'cand_sparse_00001', 'cand_middle_00001'],
  });
  assert.deepEqual(ordered.map((item) => item.candidate_id), input.map((item) => item.candidate_id));
  assert.notEqual(ordered, input);
});

test('unknown ranked ids stay at the end in their original order', () => {
  const ordered = orderCandidatesByRanking(candidates(), {
    ranking_applied: true,
    ordered_candidate_ids: ['cand_middle_00001'],
  });
  assert.deepEqual(ordered.map((item) => item.candidate_id), [
    'cand_middle_00001',
    'cand_sparse_00001',
    'cand_dense_000001',
  ]);
});
