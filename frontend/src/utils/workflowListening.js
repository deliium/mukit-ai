/**
 * Blinded listening packet helpers.
 * Packets carry labels and compositions only. Metric keys are rejected.
 */

const FORBIDDEN_KEYS = new Set([
  'hard_constraint_compliance',
  'structural_compliance',
  'motif_recurrence',
  'section_contrast',
  'tonal_consistency',
  'instrument_range_correctness',
  'revision_preservation',
  'invalid_composition',
  'generation_latency_ms',
  'process_rss_kb',
  'remote_cost_micros',
  'agent_calls',
  'model_calls',
  'revision_passes',
  'failed_stages',
  'recovered_stages',
  'time_to_valid_ms',
  'cost',
  'latency',
  'latency_ms',
  'remote_cost',
  'provider_reported_cost_micros',
  'musical_quality',
  'musical_quality_claim',
  'arm',
  'arm_id',
  'pipeline_id',
  'v3_direct',
  'v4_multi_agent',
  'v4_iterative_revision',
  'v4_autonomous',
]);

const LABELS = Object.freeze(['A', 'B', 'C']);

function walkForbidden(value, seen) {
  if (value == null || typeof value !== 'object') {
    return;
  }
  if (seen.has(value)) {
    return;
  }
  seen.add(value);
  if (Array.isArray(value)) {
    value.forEach((item) => walkForbidden(item, seen));
    return;
  }
  Object.keys(value).forEach((key) => {
    if (FORBIDDEN_KEYS.has(key)) {
      const error = new Error('packet contains a metric field');
      error.code = 'packet_metric_forbidden';
      throw error;
    }
    walkForbidden(value[key], seen);
  });
}

export function packetLabels(packet) {
  const clips = Array.isArray(packet?.clips) ? packet.clips : [];
  const labels = clips.map((clip) => clip?.label).filter((label) => LABELS.includes(label));
  const unique = [...new Set(labels)];
  if (unique.length < 2 || unique.length > 3 || unique.length !== labels.length) {
    const error = new Error('packet labels must be A/B or A/B/C');
    error.code = 'packet_labels';
    throw error;
  }
  return unique;
}

export function assertPacketHasNoMetrics(packet) {
  walkForbidden(packet, new Set());
  packetLabels(packet);
  return true;
}

export function judgmentFromWinner(packet, winner, comment) {
  assertPacketHasNoMetrics(packet);
  const labels = packetLabels(packet);
  if (!labels.includes(winner)) {
    const error = new Error('winner is not a packet label');
    error.code = 'winner_not_in_packet';
    throw error;
  }
  const text = typeof comment === 'string' ? comment.trim() : '';
  return {
    schema_version: 'workflow.listening_judgment.v1',
    benchmark_id: packet.benchmark_id,
    benchmark_version: packet.benchmark_version,
    suite_sha256: packet.suite_sha256,
    packet_sha256: packet.packet_sha256,
    case_id: packet.case_id,
    winner,
    comment: text ? text.slice(0, 240) : null,
  };
}
