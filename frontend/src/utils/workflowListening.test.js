import assert from 'node:assert/strict';
import test from 'node:test';

import {
  assertPacketHasNoMetrics,
  judgmentFromWinner,
  packetLabels,
} from './workflowListening.js';

function clip(label) {
  return {
    label,
    composition: {
      schema_version: 'composition.v2',
      key: 'C major',
      bar_count: 4,
    },
  };
}

function packet(mode) {
  const labels = mode === 'abc' ? ['A', 'B', 'C'] : ['A', 'B'];
  return {
    schema_version: 'workflow.listening_packet.v1',
    benchmark_id: 'musical-workflows',
    benchmark_version: '1.0.0',
    suite_sha256: 'a'.repeat(64),
    packet_sha256: 'b'.repeat(64),
    case_id: 'melody-line',
    mode,
    clips: labels.map(clip),
  };
}

test('A/B packet labels', () => {
  assert.deepEqual(packetLabels(packet('ab')), ['A', 'B']);
  assert.equal(assertPacketHasNoMetrics(packet('ab')), true);
});

test('A/B/C packet labels', () => {
  assert.deepEqual(packetLabels(packet('abc')), ['A', 'B', 'C']);
  const judgment = judgmentFromWinner(packet('abc'), 'B', 'clearer line');
  assert.equal(judgment.winner, 'B');
  assert.equal(judgment.comment, 'clearer line');
  assert.equal(Object.hasOwn(judgment, 'hard_constraint_compliance'), false);
});

test('rejects packets that carry metric fields', () => {
  const hard = packet('abc');
  hard.hard_constraint_compliance = true;
  assert.throws(() => assertPacketHasNoMetrics(hard));
  const quality = packet('ab');
  quality.musical_quality = 1;
  assert.throws(() => assertPacketHasNoMetrics(quality));
});
