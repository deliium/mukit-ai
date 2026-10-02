/** Place dependency nodes by the longest path from a theme. No packages. */

const NODE_WIDTH = 168;
const GAP_X = 16;
const GAP_Y = 72;

export function statusBadge(status) {
  if (status === 'stale') {
    return 'Out of date';
  }
  if (status === 'downstream_of_stale') {
    return 'May need regeneration';
  }
  if (status === 'missing') {
    return 'Missing';
  }
  return 'Fresh';
}

export function reviewUpdateEdgeId(edgeId) {
  if (!edgeId) {
    return null;
  }
  return edgeId;
}

export function layoutDependencyGraph(graph, rootKey) {
  const nodes = Array.isArray(graph?.nodes) ? graph.nodes : [];
  const edges = Array.isArray(graph?.edges) ? graph.edges : [];
  const depth = new Map();
  if (rootKey) {
    depth.set(rootKey, 0);
  }
  let changed = true;
  let guard = 0;
  while (changed && guard < nodes.length + 1) {
    changed = false;
    guard += 1;
    for (const edge of edges) {
      const upstream = depth.get(edge.upstream_node_key);
      if (upstream === undefined) {
        continue;
      }
      const next = upstream + 1;
      const current = depth.get(edge.downstream_node_key);
      if (current === undefined || next > current) {
        depth.set(edge.downstream_node_key, next);
        changed = true;
      }
    }
  }
  const columns = new Map();
  return nodes.map((node) => {
    const nodeDepth = depth.has(node.node_key) ? depth.get(node.node_key) : 0;
    const column = columns.get(nodeDepth) || 0;
    columns.set(nodeDepth, column + 1);
    return {
      nodeKey: node.node_key,
      label: node.label || '',
      depth: nodeDepth,
      x: column * (NODE_WIDTH + GAP_X),
      y: nodeDepth * GAP_Y,
      width: NODE_WIDTH,
    };
  });
}
