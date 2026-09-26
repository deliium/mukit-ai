/**
 * Musical board, brief preview gate, and safe instruction checks.
 *
 * The twin of backend autonomous_progress. Row text is the musical label.
 */

const DONE = new Set(['completed', 'skipped']);
const ACTIVE = new Set(['running', 'awaiting_approval']);

const STEPS = [
  ['composition_plan', 'Composition plan', ['plan']],
  ['harmony', 'Harmony', ['harmony_plan']],
  ['theme', '', ['motif_plan', 'symbolic']],
  ['critique', 'Critique', ['critique', 'revision']],
  ['arrangement', 'Arrangement', ['arrangement']],
  ['performance', 'Final performance', ['expression', 'render']],
];

const CHECKPOINT_STEP = {
  form: 'composition_plan',
  harmony: 'harmony',
  motif: 'theme',
  critique: 'critique',
  arrangement: 'arrangement',
  render: 'performance',
};

export const STAGE_MARKS = {
  done: '✓',
  current: '→',
  pending: '○',
  failed: '!',
};

const MELODY_PHRASES = [
  'new melody',
  'replace the melody',
  'change the melody',
  'different melody',
  'new theme',
];

function baseStatus(statuses) {
  if (statuses.some((status) => status === 'failed')) return 'failed';
  if (statuses.length > 0 && statuses.every((status) => DONE.has(status))) return 'done';
  if (statuses.some((status) => ACTIVE.has(status))) return 'current';
  return 'pending';
}

export function musicalProgress(stages, motifLabel, options = {}) {
  const byId = new Map(
    (Array.isArray(stages) ? stages : []).map((stage) => [
      String(stage?.stage_id),
      String(stage?.status || 'pending'),
    ]),
  );
  const rows = STEPS.map(([stepId, label, stageIds]) => ({
    step_id: stepId,
    label: stepId === 'theme' ? motifLabel : label,
    status: baseStatus(stageIds.map((stageId) => byId.get(stageId) || 'pending')),
    stage_ids: stageIds,
  }));
  const runStatus = options.runStatus || options.run_status || null;
  const checkpointId = options.checkpointId || options.checkpoint_id || null;
  if (runStatus === 'awaiting_approval' && CHECKPOINT_STEP[checkpointId]) {
    const target = CHECKPOINT_STEP[checkpointId];
    for (const row of rows) {
      if (row.step_id === target && row.status !== 'failed') row.status = 'current';
    }
  }
  if ((runStatus === 'paused' || runStatus === 'awaiting_approval')
    && !rows.some((row) => row.status === 'current')) {
    for (const row of rows) {
      if (row.status !== 'done' && row.status !== 'failed') {
        row.status = 'current';
        break;
      }
    }
  }
  return rows;
}

export function musicalBoardLine(row) {
  const mark = STAGE_MARKS[row.status] || STAGE_MARKS.pending;
  return `${mark} ${row.label}`;
}

export function briefFingerprint(brief) {
  const payload = {
    title: brief?.title || null,
    duration_seconds: brief?.duration_seconds ?? null,
    narrative: brief?.narrative || [],
    instrumentation: brief?.instrumentation || [],
    forbidden_instrument_families: brief?.forbidden_instrument_families || [],
    opening_key: brief?.opening_key || null,
    final_section_key: brief?.final_section_key || null,
    motif_label: brief?.motif_label || null,
    motif_must_remain_recognizable: Boolean(brief?.motif_must_remain_recognizable),
    time_signature: brief?.time_signature || '4/4',
    tempo_min: brief?.tempo_min ?? null,
    tempo_max: brief?.tempo_max ?? null,
    mood: brief?.mood || null,
    genre: brief?.genre || null,
  };
  return JSON.stringify(payload);
}

export function canStart(preview, brief) {
  if (!preview?.previewedFingerprint || !brief) return false;
  return preview.previewedFingerprint === briefFingerprint(brief);
}

export function instructionAllowed(text, { forbiddenFamilies = ['drums'] } = {}) {
  const folded = String(text || '').toLowerCase().replace(/\s+/g, ' ').trim();
  if (!folded) return false;
  if (MELODY_PHRASES.some((phrase) => folded.includes(phrase))) return false;
  for (const family of forbiddenFamilies) {
    const label = String(family || '').toLowerCase().trim();
    if (!label) continue;
    if (
      folded.includes(`add ${label}`)
      || folded.includes(`with ${label}`)
      || folded.includes(`include ${label}`)
    ) {
      return false;
    }
  }
  return true;
}

export function enabledActions(view) {
  const actions = [];
  const status = view?.status;
  const checkpoint = view?.checkpoint_id || null;
  if (status === 'running') {
    actions.push('pause', 'cancel');
  }
  if (status === 'paused' || status === 'failed') {
    actions.push('resume');
  }
  if (status === 'awaiting_approval' && checkpoint) {
    actions.push('approve');
  }
  if (checkpoint === 'arrangement') {
    actions.push('reject', 'instruction');
  } else if (status === 'paused' || status === 'awaiting_approval') {
    actions.push('instruction');
  }
  const stages = Array.isArray(view?.stages) ? view.stages : [];
  if (stages.some((stage) => (
    stage.status === 'failed'
    && stage.failure_code !== 'operation_cancelled'
    && stage.recoverable !== false
    && stage.recoverable !== 0
  ))) {
    actions.push('retry');
  }
  return actions;
}
