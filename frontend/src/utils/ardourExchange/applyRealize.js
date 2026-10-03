/**
 * Seed Zustand arrangement / development / harmony surfaces from an exchange
 * realize envelope, then call the existing Apply helpers.
 */

import { createAppLogger } from '../appLogger.js';

const logger = createAppLogger('ardourExchange.applyRealize');

const INSTRUMENT_LABEL_TO_ID = Object.freeze({
  violin: 'violin',
  cello: 'cello',
  'acoustic grand piano': 'acoustic_grand_piano',
  'string ensemble 1': 'string_ensemble_1',
  piano: 'acoustic_grand_piano',
});

function truncateId(value, max = 12) {
  if (value == null) return null;
  const text = String(value);
  return text.length <= max ? text : `${text.slice(0, max)}…`;
}

function instrumentIdFromInventoryRow(row) {
  if (!row) return 'acoustic_grand_piano';
  if (typeof row.instrument_id === 'string' && row.instrument_id) {
    return row.instrument_id;
  }
  const label = String(row.instrument || '').trim().toLowerCase();
  if (INSTRUMENT_LABEL_TO_ID[label]) return INSTRUMENT_LABEL_TO_ID[label];
  const program = row.midi_program;
  if (program === 40) return 'violin';
  if (program === 42) return 'cello';
  if (program === 48) return 'string_ensemble_1';
  if (program === 0) return 'acoustic_grand_piano';
  return 'acoustic_grand_piano';
}

function partsFromInventory(inventory, { asSource = false } = {}) {
  const rows = Array.isArray(inventory) ? inventory : [];
  return rows.map((row, index) => {
    const partId = row.part_id || `${asSource ? 'before' : 'after'}-${index + 1}`;
    const part = {
      part_id: partId,
      instrument_id: instrumentIdFromInventoryRow(row),
      role: row.role || 'other',
      doubling_policy: 'none',
    };
    if (asSource && row.track_id) {
      part.source_track_ids = [row.track_id];
    }
    return part;
  });
}

function findCandidate(envelope, candidateId) {
  const candidates = envelope?.preview?.candidates || [];
  if (!candidates.length) return null;
  if (candidateId) {
    return candidates.find((item) => item.candidate_id === candidateId) || null;
  }
  return candidates[0] || null;
}

/**
 * Build the store patch for a realize envelope + candidate (no Apply).
 */
export function buildRealizeSeedPatch(envelope, candidateId, { compositionRevision = null } = {}) {
  const surface = envelope?.surface;
  const candidate = findCandidate(envelope, candidateId);
  if (!surface || !candidate) {
    return null;
  }
  const preview = envelope.preview || {};
  const intent = envelope.intent || null;

  if (surface === 'arrangement') {
    const before = partsFromInventory(candidate.before_inventory, { asSource: true });
    const after = partsFromInventory(candidate.after_inventory, { asSource: false });
    const sourceTrackIds = before.flatMap((part) => part.source_track_ids || []);
    return {
      surface,
      intent,
      candidateId: candidate.candidate_id,
      patch: {
        arrangementStatus: 'ready',
        arrangementError: '',
        arrangementStaleReason: null,
        arrangementOperation: candidate.operation || preview.operation || 'change_instrumentation',
        arrangementSourceTrackIds: sourceTrackIds,
        arrangementProtectedTrackIds: [],
        arrangementInstrumentationBefore: before,
        arrangementInstrumentationAfter: after,
        arrangementPreserveMelody: true,
        arrangementPreserveHarmony: true,
        arrangementCandidates: preview.candidates || [candidate],
        arrangementSelectedCandidateId: candidate.candidate_id,
        arrangementBaseRevision: compositionRevision,
        arrangementEditSourceFingerprint:
          preview.edit_source_fingerprint || candidate.edit_source_fingerprint || null,
        arrangementResponseCatalogFingerprint:
          preview.catalog_fingerprint || candidate.catalog_fingerprint || null,
        arrangementRejectedAttempts: preview.rejected_attempts || [],
        arrangementWarnings: preview.warning_codes || [],
      },
    };
  }

  if (surface === 'development') {
    return {
      surface,
      intent,
      candidateId: candidate.candidate_id,
      patch: {
        developmentStatus: 'ready',
        developmentError: '',
        developmentOperation: candidate.operation || preview.operation || 'vary_section',
        developmentCandidates: preview.candidates || [candidate],
        developmentSelectedCandidateId: candidate.candidate_id,
        developmentBaseRevision: compositionRevision,
        developmentEditSourceFingerprint:
          preview.edit_source_fingerprint || candidate.edit_source_fingerprint || null,
      },
    };
  }

  if (surface === 'harmony') {
    const rawPreview = preview.preview || preview;
    return {
      surface,
      intent,
      candidateId: candidate.candidate_id,
      patch: {
        reharmonizeStatus: 'ready',
        reharmonizeError: '',
        reharmonizeOperation: 'reharmonize',
        reharmonizeContentPolicy: 'preserve_harmony_adapt_melody',
        reharmonizeEngine: 'deterministic',
        reharmonizeCandidate: candidate.composition || rawPreview.composition || null,
        reharmonizeBaseFingerprint:
          candidate.base_fingerprint || rawPreview.base_fingerprint || null,
        reharmonizeProposalFingerprint:
          candidate.proposal_fingerprint || rawPreview.proposal_fingerprint || null,
        reharmonizeBaseRevision: compositionRevision,
        reharmonizeStartTick: preview.start_tick ?? rawPreview.start_tick ?? null,
        reharmonizeEndTick: preview.end_tick ?? rawPreview.end_tick ?? null,
        reharmonizeActiveKey: preview.active_key ?? rawPreview.active_key ?? null,
        reharmonizeTargetTrackIds:
          preview.recommended_target_track_ids
          || rawPreview.recommended_target_track_ids
          || [],
        reharmonizeHarmonyChanges:
          candidate.harmony_changes || rawPreview.harmony_changes || [],
        reharmonizeTrackChanges: candidate.track_changes || rawPreview.track_changes || [],
        reharmonizePreservation: candidate.preservation || rawPreview.preservation || [],
        reharmonizeCompatibility: candidate.compatibility || rawPreview.compatibility || null,
        reharmonizeWarnings: candidate.warnings || rawPreview.warnings || [],
      },
    };
  }

  return null;
}

/**
 * @param {object} envelope realize response
 * @param {string|null} candidateId
 * @param {{
 *   getStore?: () => object,
 *   setState?: (patch: object) => void,
 *   applyArrangement?: () => Promise<boolean>,
 *   applyDevelopment?: () => Promise<boolean>,
 *   applyReharmonize?: () => Promise<boolean>,
 * }} [deps]
 */
async function resolveStoreApi(deps) {
  if (typeof deps.getStore === 'function' || typeof deps.setState === 'function') {
    const state = typeof deps.getStore === 'function' ? deps.getStore() : null;
    return {
      state,
      setState: deps.setState || (() => {
        throw new Error('setState dependency required when getStore is injected');
      }),
    };
  }
  const { useMusicStore } = await import('../../store/musicStore.js');
  return {
    state: useMusicStore.getState(),
    setState: (patch) => useMusicStore.setState(patch),
  };
}

export async function applyRealizeCandidate(envelope, candidateId, deps = {}) {
  const { state, setState } = await resolveStoreApi(deps);
  const compositionRevision = state?.compositionRevision ?? null;
  const seeded = buildRealizeSeedPatch(envelope, candidateId, { compositionRevision });
  if (!seeded) {
    logger.warn('missing candidate', {
      intent: envelope?.intent || null,
      surface: envelope?.surface || null,
      candidate_id: truncateId(candidateId),
    });
    return false;
  }

  setState(seeded.patch);

  let applyFn = null;
  if (seeded.surface === 'arrangement') {
    applyFn = deps.applyArrangement || state?.applySelectedArrangementCandidate;
  } else if (seeded.surface === 'development') {
    applyFn = deps.applyDevelopment || state?.applySelectedDevelopmentCandidate;
  } else if (seeded.surface === 'harmony') {
    applyFn = deps.applyReharmonize || state?.applyReharmonizePreview;
  }

  if (typeof applyFn !== 'function') {
    logger.warn('Apply helper missing', { surface: seeded.surface });
    return false;
  }

  const ok = await applyFn();
  if (!ok) {
    logger.warn('Apply false', {
      surface: seeded.surface,
      intent: seeded.intent,
      candidate_id: truncateId(seeded.candidateId),
    });
    return false;
  }

  logger.info('seed/Apply ok', {
    surface: seeded.surface,
    intent: seeded.intent,
    candidate_id: truncateId(seeded.candidateId),
  });
  return true;
}
