const COLUMN_COUNT = 4;
const CARD_WIDTH = 180;
const CARD_HEIGHT = 96;
const GAP_X = 48;
const GAP_Y = 36;

export function adaptiveMaterialLabel(material) {
  if (!material || typeof material !== 'object') {
    return 'No material';
  }
  if (material.kind === 'section' && material.section_id) {
    return `Section ${material.section_id}`;
  }
  if (material.kind === 'bar_range' && material.start_bar != null && material.end_bar != null) {
    return `Bars ${material.start_bar}–${material.end_bar}`;
  }
  if (typeof material.kind === 'string' && material.kind) {
    return material.kind;
  }
  return 'Material';
}

export function layoutAdaptiveScoreGraph(score, selectedStateId = null) {
  const states = Array.isArray(score?.states) ? score.states : [];
  const nodes = states.map((state, index) => {
    const column = index % COLUMN_COUNT;
    const row = Math.floor(index / COLUMN_COUNT);
    return {
      id: state.id,
      name: state.name,
      x: column * (CARD_WIDTH + GAP_X),
      y: row * (CARD_HEIGHT + GAP_Y),
      width: CARD_WIDTH,
      height: CARD_HEIGHT,
      selected: state.id === selectedStateId,
      materialLabel: adaptiveMaterialLabel(state.material),
    };
  });
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const edges = [];
  for (const transition of score?.transitions || []) {
    const from = byId.get(transition.from_state_id);
    const to = byId.get(transition.to_state_id);
    if (!from || !to) {
      continue;
    }
    edges.push({
      id: transition.id,
      from: transition.from_state_id,
      to: transition.to_state_id,
      x1: from.x + from.width,
      y1: from.y + from.height / 2,
      x2: to.x,
      y2: to.y + to.height / 2,
    });
  }
  const width = nodes.reduce((max, node) => Math.max(max, node.x + node.width), CARD_WIDTH);
  const height = nodes.reduce((max, node) => Math.max(max, node.y + node.height), CARD_HEIGHT);
  return {
    nodes,
    edges,
    width: states.length ? width : 0,
    height: states.length ? height : 0,
  };
}
