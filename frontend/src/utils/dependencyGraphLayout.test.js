import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  layoutDependencyGraph,
  reviewUpdateEdgeId,
  statusBadge,
} from './dependencyGraphLayout.js';

test('layout places three children on one depth with distinct x', () => {
  const root = 'theme:muniv_aaaaaaaaaaaaaaaa:theme_bbbbbbbb';
  const graph = {
    nodes: [
      { node_key: root, label: 'Theme A' },
      { node_key: 'motif:project-b:motif_1:occ_1', label: 'Exploration variation' },
      { node_key: 'motif:project-c:motif_2:occ_2', label: 'Combat variation' },
      { node_key: 'motif:project-d:motif_3:occ_3', label: 'Finale transformation' },
      { node_key: 'shared', label: 'Render' },
    ],
    edges: [
      { upstream_node_key: root, downstream_node_key: 'motif:project-b:motif_1:occ_1' },
      { upstream_node_key: root, downstream_node_key: 'motif:project-c:motif_2:occ_2' },
      { upstream_node_key: root, downstream_node_key: 'motif:project-d:motif_3:occ_3' },
      { upstream_node_key: 'motif:project-b:motif_1:occ_1', downstream_node_key: 'shared' },
      { upstream_node_key: 'motif:project-c:motif_2:occ_2', downstream_node_key: 'shared' },
    ],
  };
  const placed = layoutDependencyGraph(graph, root);
  const children = placed.filter((node) => node.depth === 1);
  assert.equal(children.length, 3);
  assert.equal(new Set(children.map((node) => node.x)).size, 3);
  assert.equal(new Set(children.map((node) => node.depth)).size, 1);
  const shared = placed.filter((node) => node.nodeKey === 'shared');
  assert.equal(shared.length, 1);
  assert.equal(shared[0].depth, 2);
});

test('status badges and review selection stay out of reuse', () => {
  assert.equal(statusBadge('stale'), 'Out of date');
  assert.equal(statusBadge('downstream_of_stale'), 'May need regeneration');
  const source = readFileSync(new URL('./dependencyGraphLayout.js', import.meta.url), 'utf8');
  assert.equal(source.includes('reuseMusicalUniverseTheme'), false);
  assert.equal(reviewUpdateEdgeId(undefined), null);
  assert.equal(reviewUpdateEdgeId('dep_0000000000000001'), 'dep_0000000000000001');
});
