/**
 * Pure reuse request for a musical universe theme.
 * This module does not fetch and does not touch composition notes.
 */

import { projectPersistRevisionKey } from './projectPersistRevision.js';

const DIRTY_STATUSES = new Set(['unsaved', 'saving', 'conflict']);

export function universeReuseBlockReason(state) {
  if (!state?.currentProjectId) {
    return 'no-project';
  }
  if (DIRTY_STATUSES.has(state.saveStatus)) {
    return 'dirty-draft';
  }
  const persistRevision = projectPersistRevisionKey(state.editedMusicJson, state.generationMeta);
  if (persistRevision !== state.lastSavedPersistRevision) {
    return 'dirty-draft';
  }
  return null;
}

export function reuseParameters(operation, fields = {}) {
  if (operation === 'transpose') {
    return { transpose_semitones: Number(fields.semitones) };
  }
  if (operation === 'inversion') {
    const axis = String(fields.axis || '').trim();
    return axis ? { inversion_axis_pitch: axis } : {};
  }
  if (operation === 'augmentation' || operation === 'diminution') {
    return {
      time_scale_numerator: Number(fields.numerator),
      time_scale_denominator: Number(fields.denominator),
    };
  }
  if (operation === 'sequence') {
    return {
      sequence_steps: Number(fields.steps),
      sequence_interval_semitones: Number(fields.interval),
      sequence_step_ticks: Number(fields.stepTicks),
    };
  }
  return {};
}

export function buildThemeReuseRequest({
  operation,
  parameters = null,
  destinationProjectId,
  destinationTrackId,
  destinationStartBar,
  branch,
  expectedUniverseRevision,
}) {
  return {
    destination_project_id: destinationProjectId,
    destination_track_id: destinationTrackId,
    destination_start_bar: destinationStartBar,
    operation,
    parameters,
    branch_id: branch.branch_id,
    expected_active_branch_id: branch.expected_active_branch_id,
    expected_working_version: branch.expected_working_version,
    expected_head_revision_id: branch.expected_head_revision_id,
    expected_source_fingerprint: branch.expected_source_fingerprint,
    expected_universe_revision: expectedUniverseRevision,
  };
}

export function themeReuseForProject(state, spec) {
  const reason = universeReuseBlockReason(state);
  if (reason) {
    return { enabled: false, reason, request: null };
  }
  return {
    enabled: true,
    reason: null,
    request: buildThemeReuseRequest(spec),
  };
}
