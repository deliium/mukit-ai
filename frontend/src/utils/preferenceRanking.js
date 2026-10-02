/** Reorder a session candidate list from preference.ranking.v1. */

export function orderCandidatesByRanking(candidates, ranking) {
  const source = Array.isArray(candidates) ? candidates.slice() : [];
  if (!ranking || ranking.ranking_applied === false) {
    return source;
  }
  const orderedIds = Array.isArray(ranking.ordered_candidate_ids)
    ? ranking.ordered_candidate_ids
    : [];
  const byId = new Map();
  source.forEach((item) => {
    if (item && typeof item.candidate_id === 'string' && !byId.has(item.candidate_id)) {
      byId.set(item.candidate_id, item);
    }
  });
  const ordered = [];
  const used = new Set();
  orderedIds.forEach((id) => {
    const item = byId.get(id);
    if (item && !used.has(id)) {
      ordered.push(item);
      used.add(id);
    }
  });
  source.forEach((item) => {
    const id = item?.candidate_id;
    if (!used.has(id)) {
      ordered.push(item);
      if (typeof id === 'string') {
        used.add(id);
      }
    }
  });
  return ordered;
}
