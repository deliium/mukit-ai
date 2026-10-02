import React, { useMemo } from 'react';
import styled from 'styled-components';

import {
  layoutDependencyGraph,
  reviewUpdateEdgeId,
  statusBadge,
} from '../utils/dependencyGraphLayout.js';

const Frame = styled.div`
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
`;

const Canvas = styled.svg`
  width: 100%;
  max-width: 720px;
  height: auto;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
`;

const Actions = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
`;

function nodeByKey(placed, nodeKey) {
  return placed.find((node) => node.nodeKey === nodeKey) || null;
}

const DependencyGraph = ({ graph, rootKey, onReview, onAccept }) => {
  const placed = useMemo(
    () => layoutDependencyGraph(graph, rootKey),
    [graph, rootKey],
  );
  const edges = Array.isArray(graph?.edges) ? graph.edges : [];
  const width = placed.reduce((max, node) => Math.max(max, node.x + node.width + 16), 320);
  const height = placed.reduce((max, node) => Math.max(max, node.y + 48), 80);

  if (!graph) {
    return null;
  }

  return (
    <Frame>
      <Canvas viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Dependency graph">
        {edges.map((edge) => {
          const from = nodeByKey(placed, edge.upstream_node_key);
          const to = nodeByKey(placed, edge.downstream_node_key);
          if (!from || !to) {
            return null;
          }
          return (
            <line
              key={edge.edge_id}
              x1={from.x + from.width / 2}
              y1={from.y + 28}
              x2={to.x + to.width / 2}
              y2={to.y + 8}
              stroke="#64748b"
              strokeWidth="1"
            />
          );
        })}
        {placed.map((node) => (
          <g key={node.nodeKey}>
            <rect
              x={node.x}
              y={node.y}
              width={node.width}
              height="32"
              rx="6"
              fill="#ffffff"
              stroke="#cbd5e1"
            />
            <text x={node.x + 8} y={node.y + 20} fontSize="12" fill="#0f172a">
              {(node.label || node.nodeKey).slice(0, 28)}
            </text>
          </g>
        ))}
      </Canvas>
      <Actions>
        {edges.map((edge) => {
          if (edge.status !== 'stale' && edge.status !== 'downstream_of_stale' && edge.status !== 'missing') {
            return null;
          }
          const actionable = edge.status === 'stale' || edge.status === 'downstream_of_stale';
          return (
            <span key={edge.edge_id}>
              {statusBadge(edge.status)}
              {actionable ? (
                <>
                  <button type="button" onClick={() => onReview(reviewUpdateEdgeId(edge.edge_id))}>
                    Review update
                  </button>
                  <button type="button" onClick={() => onAccept(edge.edge_id)}>
                    Accept current
                  </button>
                </>
              ) : null}
            </span>
          );
        })}
      </Actions>
    </Frame>
  );
};

export default DependencyGraph;
