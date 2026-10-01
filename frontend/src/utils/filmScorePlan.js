/** Display helpers for film.score.plan.v1. They do not recompute tempo. */

export function tempoChangeLines(plan) {
  const changes = plan?.tempo_strategy?.changes;
  if (!Array.isArray(changes) || changes.length === 0) {
    return [];
  }
  return changes.map((change) => ({
    tick: change.tick,
    bpm: change.bpm,
    sectionId: change.section_id,
    reasonCode: change.reason_code,
  }));
}

export function sectionLines(plan) {
  const sections = Array.isArray(plan?.sections) ? plan.sections : [];
  return sections.map((section) => ({
    id: section.id,
    label: section.label || section.id,
    startBar: section.start_bar,
    barCount: section.bar_count,
    bpm: section.tempo_bpm,
    density: section.density,
  }));
}

export function sparseDialogueRegions(plan) {
  const regions = Array.isArray(plan?.density_regions) ? plan.density_regions : [];
  return regions.filter((region) => region.density === 'sparse');
}

export function hitStatusLines(plan) {
  const rows = Array.isArray(plan?.hit_alignments) ? plan.hit_alignments : [];
  return rows.map((row) => ({
    cueId: row.cue_id,
    status: row.status,
    tempoChangeAdded: Boolean(row.tempo_change_added),
  }));
}

export function canCommitFilmScore(preview) {
  return Boolean(preview && preview.candidate_fingerprint);
}

const PROFILE_STRENGTHS = new Set(['off', 'light', 'normal', 'strong']);

/** The commit CAS revision is the one on the preview plan. */
export function scoringRevisionForCommit(preview) {
  const revision = preview?.plan?.scoring_document_revision;
  if (!Number.isInteger(revision) || revision < 0) {
    return null;
  }
  return revision;
}

/**
 * Preview body from the Agents-tab fields. Empty optional fields are omitted.
 * Returns `{ error }` when a filled duration is not a finite window.
 */
export function filmScoreRequestBody({
  brief = '',
  instruments = '',
  profileId = '',
  profileStrength = 'off',
  motifIds = [],
  targetDurationSeconds = '',
  replaceExisting = false,
} = {}) {
  const strength = PROFILE_STRENGTHS.has(profileStrength) ? profileStrength : 'off';
  const body = {
    brief: String(brief).trim(),
    instruments: String(instruments).split(',').map((item) => item.trim()).filter(Boolean),
    profile_strength: strength,
    motif_ids: motifIds.map((item) => String(item).trim()).filter(Boolean).slice(0, 16),
    replace_existing: Boolean(replaceExisting),
  };
  const profile = String(profileId).trim();
  if (profile) {
    body.profile_id = profile.slice(0, 80);
  }
  const durationText = String(targetDurationSeconds).trim();
  if (durationText) {
    const duration = Number(durationText);
    if (!Number.isFinite(duration) || duration <= 0 || duration > 3600) {
      return { error: 'film_score_invalid' };
    }
    body.target_duration_seconds = duration;
  }
  return { body };
}

/** Autosave sends the edited composition only. The session preview stays off that object. */
export function autosaveBodyFromState(state) {
  return { composition: state?.editedMusicJson ?? null };
}
