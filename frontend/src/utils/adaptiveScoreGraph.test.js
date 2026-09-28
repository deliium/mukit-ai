import assert from 'node:assert/strict';
import test from 'node:test';

import { adaptiveMaterialLabel, layoutAdaptiveScoreGraph } from './adaptiveScoreGraph.js';

const SCORE = {
  states: [
    {
      id: 'state-exploration',
      name: 'Exploration',
      material: { kind: 'section', section_id: 'section-1' },
    },
    {
      id: 'state-combat',
      name: 'Combat',
      material: { kind: 'section', section_id: 'section-2' },
    },
    {
      id: 'state-victory',
      name: 'Victory',
      material: { kind: 'bar_range', start_bar: 1, end_bar: 4 },
    },
  ],
  transitions: [
    {
      id: 'to-combat',
      from_state_id: 'state-exploration',
      to_state_id: 'state-combat',
    },
    {
      id: 'to-victory',
      from_state_id: 'state-combat',
      to_state_id: 'state-victory',
    },
  ],
};

test('three states stay in order and the selected id is flagged', () => {
  const layout = layoutAdaptiveScoreGraph(SCORE, 'state-combat');
  assert.deepEqual(layout.nodes.map((node) => node.id), [
    'state-exploration',
    'state-combat',
    'state-victory',
  ]);
  assert.equal(layout.nodes[1].selected, true);
  assert.equal(layout.nodes[0].selected, false);
  assert.ok(layout.nodes[1].x > layout.nodes[0].x);
});

test('edges keep their endpoints', () => {
  const layout = layoutAdaptiveScoreGraph(SCORE, 'state-exploration');
  assert.deepEqual(
    layout.edges.map((edge) => [edge.id, edge.from, edge.to]),
    [
      ['to-combat', 'state-exploration', 'state-combat'],
      ['to-victory', 'state-combat', 'state-victory'],
    ],
  );
});

test('bar range label names the inclusive bars', () => {
  assert.equal(adaptiveMaterialLabel(SCORE.states[2].material), 'Bars 1–4');
  const layout = layoutAdaptiveScoreGraph(SCORE, null);
  assert.equal(layout.nodes[2].materialLabel, 'Bars 1–4');
});

test('empty score has no nodes or edges', () => {
  const layout = layoutAdaptiveScoreGraph({ states: [], transitions: [] }, null);
  assert.deepEqual(layout.nodes, []);
  assert.deepEqual(layout.edges, []);
  assert.equal(layout.width, 0);
  assert.equal(layout.height, 0);
});
