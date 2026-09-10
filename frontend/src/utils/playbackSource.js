/**
 * Mutual-exclusive playback source resolution for working / preview / version audition.
 * Pure helpers — no logging of composition payloads.
 */

import { createAppLogger } from './appLogger.js';

const logger = createAppLogger('playbackSource');

export const PLAYBACK_SOURCE_WORKING = 'working';
export const PLAYBACK_SOURCE_DEVELOPMENT = 'development';
export const PLAYBACK_SOURCE_ARRANGEMENT = 'arrangement';
export const PLAYBACK_SOURCE_VERSION = 'version';
export const PLAYBACK_SOURCE_GENERATION = 'generation';

/** Fine-grained audition kinds (subset of coarse `source` for AI previews). */
export const PLAYBACK_SOURCE_KIND_WORKING = 'working';
export const PLAYBACK_SOURCE_KIND_DEVELOPMENT = 'development';
export const PLAYBACK_SOURCE_KIND_ARRANGEMENT = 'arrangement';
export const PLAYBACK_SOURCE_KIND_VERSION = 'version';
export const PLAYBACK_SOURCE_KIND_GENERATION = 'generation';
export const PLAYBACK_SOURCE_KIND_AI_EDIT = 'ai_edit';
export const PLAYBACK_SOURCE_KIND_MOTIF = 'motif';
export const PLAYBACK_SOURCE_KIND_REHARMONIZE = 'reharmonize';

/** Mixer control buckets — AI preview kinds share one ephemeral scope. */
export const PLAYBACK_MIXER_SCOPE_WORKING = 'working';
export const PLAYBACK_MIXER_SCOPE_DEVELOPMENT = 'development';
export const PLAYBACK_MIXER_SCOPE_ARRANGEMENT = 'arrangement';
export const PLAYBACK_MIXER_SCOPE_VERSION = 'version';
export const PLAYBACK_MIXER_SCOPE_PREVIEW = 'preview';

const EMPTY_SOURCE_ID = '';

/**
 * Build a stable source key from kind + id.
 * @param {string} sourceKind
 * @param {string} [sourceId]
 * @returns {string}
 */
export function buildPlaybackSourceKey(sourceKind, sourceId = EMPTY_SOURCE_ID) {
  const kind = String(sourceKind || PLAYBACK_SOURCE_KIND_WORKING);
  const id = sourceId == null || sourceId === '' ? EMPTY_SOURCE_ID : String(sourceId);
  return id ? `${kind}:${id}` : kind;
}

/**
 * Map fine-grained kind → ephemeral mixer scope.
 * @param {string} sourceKind
 * @returns {string}
 */
export function mixerScopeForSourceKind(sourceKind) {
  switch (sourceKind) {
    case PLAYBACK_SOURCE_KIND_DEVELOPMENT:
      return PLAYBACK_MIXER_SCOPE_DEVELOPMENT;
    case PLAYBACK_SOURCE_KIND_ARRANGEMENT:
      return PLAYBACK_MIXER_SCOPE_ARRANGEMENT;
    case PLAYBACK_SOURCE_KIND_VERSION:
      return PLAYBACK_MIXER_SCOPE_VERSION;
    case PLAYBACK_SOURCE_KIND_GENERATION:
    case PLAYBACK_SOURCE_KIND_AI_EDIT:
    case PLAYBACK_SOURCE_KIND_MOTIF:
    case PLAYBACK_SOURCE_KIND_REHARMONIZE:
      return PLAYBACK_MIXER_SCOPE_PREVIEW;
    case PLAYBACK_SOURCE_KIND_WORKING:
    default:
      return PLAYBACK_MIXER_SCOPE_WORKING;
  }
}

/**
 * Coarse transport bucket used by existing UI (`working`…`generation`).
 * @param {string} sourceKind
 * @returns {string}
 */
export function coarseSourceForKind(sourceKind) {
  switch (sourceKind) {
    case PLAYBACK_SOURCE_KIND_DEVELOPMENT:
      return PLAYBACK_SOURCE_DEVELOPMENT;
    case PLAYBACK_SOURCE_KIND_ARRANGEMENT:
      return PLAYBACK_SOURCE_ARRANGEMENT;
    case PLAYBACK_SOURCE_KIND_VERSION:
      return PLAYBACK_SOURCE_VERSION;
    case PLAYBACK_SOURCE_KIND_GENERATION:
    case PLAYBACK_SOURCE_KIND_AI_EDIT:
    case PLAYBACK_SOURCE_KIND_MOTIF:
    case PLAYBACK_SOURCE_KIND_REHARMONIZE:
      return PLAYBACK_SOURCE_GENERATION;
    case PLAYBACK_SOURCE_KIND_WORKING:
    default:
      return PLAYBACK_SOURCE_WORKING;
  }
}

/**
 * @param {string} sourceKind
 * @param {string} [sourceId]
 * @param {object|null} composition
 * @returns {{
 *   source: string,
 *   sourceKind: string,
 *   sourceId: string,
 *   sourceKey: string,
 *   mixerScope: string,
 *   composition: object|null,
 * }}
 */
export function buildPlaybackSourceResult(sourceKind, sourceId, composition) {
  const kind = String(sourceKind || PLAYBACK_SOURCE_KIND_WORKING);
  const id = sourceId == null || sourceId === '' ? EMPTY_SOURCE_ID : String(sourceId);
  const result = {
    source: coarseSourceForKind(kind),
    sourceKind: kind,
    sourceId: id,
    sourceKey: buildPlaybackSourceKey(kind, id),
    mixerScope: mixerScopeForSourceKind(kind),
    composition: composition ?? null,
  };
  logger.debug('Resolved playback source', {
    sourceKind: result.sourceKind,
    sourceId: result.sourceId || null,
    sourceKey: result.sourceKey,
    mixerScope: result.mixerScope,
    hasComposition: Boolean(result.composition),
  });
  return result;
}

/**
 * Resolve which composition the transport should play.
 * Priority: arrangement → generation → ai_edit → motif → reharmonize → version → development → working.
 *
 * @param {object} state
 * @param {{
 *   findDevelopmentCandidateById?: Function,
 *   findArrangementCandidateById?: Function,
 *   arrangementCandidateMode?: string,
 * }} [deps]
 * @returns {{
 *   source: string,
 *   sourceKind: string,
 *   sourceId: string,
 *   sourceKey: string,
 *   mixerScope: string,
 *   composition: object|null,
 * }}
 */
export function resolvePlaybackSource(state, deps = {}) {
  const arrangementMode = deps.arrangementCandidateMode || 'candidate';
  if (state?.arrangementAuditionMode === arrangementMode) {
    const findArr = deps.findArrangementCandidateById;
    const candidate = typeof findArr === 'function'
      ? findArr(state.arrangementCandidates, state.arrangementSelectedCandidateId)
      : null;
    if (candidate?.composition) {
      const sourceId = candidate.candidate_id != null
        ? String(candidate.candidate_id)
        : String(state.arrangementSelectedCandidateId || EMPTY_SOURCE_ID);
      return buildPlaybackSourceResult(
        PLAYBACK_SOURCE_KIND_ARRANGEMENT,
        sourceId,
        candidate.composition,
      );
    }
  }

  if (state?.generationAuditionActive && state?.generationCandidate?.composition) {
    const candidate = state.generationCandidate;
    const sourceId = candidate.candidate_id != null
      ? String(candidate.candidate_id)
      : (candidate.id != null ? String(candidate.id) : 'generation');
    return buildPlaybackSourceResult(
      PLAYBACK_SOURCE_KIND_GENERATION,
      sourceId,
      candidate.composition,
    );
  }

  if (state?.aiEditAuditionActive && state?.aiEditCandidate?.composition) {
    const candidate = state.aiEditCandidate;
    const sourceId = candidate.candidate_id != null
      ? String(candidate.candidate_id)
      : (candidate.id != null ? String(candidate.id) : 'ai_edit');
    return buildPlaybackSourceResult(
      PLAYBACK_SOURCE_KIND_AI_EDIT,
      sourceId,
      candidate.composition,
    );
  }

  if (state?.motifAuditionActive && state?.motifCandidate?.composition) {
    const candidate = state.motifCandidate;
    const sourceId = candidate.candidate_id != null
      ? String(candidate.candidate_id)
      : (candidate.id != null ? String(candidate.id) : 'motif');
    return buildPlaybackSourceResult(
      PLAYBACK_SOURCE_KIND_MOTIF,
      sourceId,
      candidate.composition,
    );
  }

  if (state?.reharmonizeAuditionActive && state?.reharmonizeCandidate) {
    const candidate = state.reharmonizeCandidate;
    const composition = candidate.composition ?? candidate;
    const sourceId = candidate.candidate_id != null
      ? String(candidate.candidate_id)
      : (candidate.id != null ? String(candidate.id) : 'reharmonize');
    return buildPlaybackSourceResult(
      PLAYBACK_SOURCE_KIND_REHARMONIZE,
      sourceId,
      composition,
    );
  }

  if (state?.versionAuditionActive) {
    const revisionId = state.versionSelectedRevisionId;
    const detail = revisionId ? state.versionRevisionDetails?.[revisionId] : null;
    if (detail && Object.prototype.hasOwnProperty.call(detail, 'composition')) {
      return buildPlaybackSourceResult(
        PLAYBACK_SOURCE_KIND_VERSION,
        revisionId != null ? String(revisionId) : EMPTY_SOURCE_ID,
        detail.composition ?? null,
      );
    }
  }

  if (state?.developmentAuditionActive) {
    const findDev = deps.findDevelopmentCandidateById;
    const candidate = typeof findDev === 'function'
      ? findDev(state.developmentCandidates, state.developmentSelectedCandidateId)
      : null;
    if (candidate?.composition) {
      const sourceId = candidate.candidate_id != null
        ? String(candidate.candidate_id)
        : String(state.developmentSelectedCandidateId || EMPTY_SOURCE_ID);
      return buildPlaybackSourceResult(
        PLAYBACK_SOURCE_KIND_DEVELOPMENT,
        sourceId,
        candidate.composition,
      );
    }
  }

  return buildPlaybackSourceResult(
    PLAYBACK_SOURCE_KIND_WORKING,
    EMPTY_SOURCE_ID,
    state?.editedMusicJson ?? null,
  );
}

/**
 * Patch that clears competing audition modes when enabling one source.
 * @param {'working'|'development'|'arrangement'|'version'|'generation'} source
 * @param {string} arrangementSourceMode
 * @returns {object}
 */
export function exclusiveAuditionPatch(source, arrangementSourceMode = 'source') {
  const clearAiPreviews = {
    generationAuditionActive: false,
    aiEditAuditionActive: false,
    motifAuditionActive: false,
    reharmonizeAuditionActive: false,
  };
  if (source === PLAYBACK_SOURCE_ARRANGEMENT) {
    return {
      developmentAuditionActive: false,
      versionAuditionActive: false,
      ...clearAiPreviews,
    };
  }
  if (source === PLAYBACK_SOURCE_GENERATION) {
    return {
      developmentAuditionActive: false,
      arrangementAuditionMode: arrangementSourceMode,
      versionAuditionActive: false,
      // Caller enables the specific generation/ai-edit/motif/reharm audition flag.
      aiEditAuditionActive: false,
      motifAuditionActive: false,
      reharmonizeAuditionActive: false,
    };
  }
  if (source === PLAYBACK_SOURCE_DEVELOPMENT) {
    return {
      arrangementAuditionMode: arrangementSourceMode,
      versionAuditionActive: false,
      ...clearAiPreviews,
    };
  }
  if (source === PLAYBACK_SOURCE_VERSION) {
    return {
      developmentAuditionActive: false,
      arrangementAuditionMode: arrangementSourceMode,
      ...clearAiPreviews,
    };
  }
  return {
    developmentAuditionActive: false,
    arrangementAuditionMode: arrangementSourceMode,
    versionAuditionActive: false,
    ...clearAiPreviews,
  };
}
