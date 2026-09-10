/**
 * Mutual-exclusive playback source resolution for working / preview / version audition.
 * Pure helpers — no logging of composition payloads.
 */

export const PLAYBACK_SOURCE_WORKING = 'working';
export const PLAYBACK_SOURCE_DEVELOPMENT = 'development';
export const PLAYBACK_SOURCE_ARRANGEMENT = 'arrangement';
export const PLAYBACK_SOURCE_VERSION = 'version';
export const PLAYBACK_SOURCE_GENERATION = 'generation';

/**
 * Resolve which composition the transport should play.
 * Priority: arrangement → generation → version → development → working.
 *
 * @param {object} state
 * @param {{
 *   findDevelopmentCandidateById?: Function,
 *   findArrangementCandidateById?: Function,
 *   arrangementCandidateMode?: string,
 * }} [deps]
 * @returns {{
 *   source: 'working'|'development'|'arrangement'|'version'|'generation',
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
      return {
        source: PLAYBACK_SOURCE_ARRANGEMENT,
        composition: candidate.composition,
      };
    }
  }

  if (state?.generationAuditionActive && state?.generationCandidate?.composition) {
    return {
      source: PLAYBACK_SOURCE_GENERATION,
      composition: state.generationCandidate.composition,
    };
  }

  if (state?.aiEditAuditionActive && state?.aiEditCandidate?.composition) {
    return {
      source: PLAYBACK_SOURCE_GENERATION,
      composition: state.aiEditCandidate.composition,
    };
  }

  if (state?.motifAuditionActive && state?.motifCandidate?.composition) {
    return {
      source: PLAYBACK_SOURCE_GENERATION,
      composition: state.motifCandidate.composition,
    };
  }

  if (state?.reharmonizeAuditionActive && state?.reharmonizeCandidate) {
    return {
      source: PLAYBACK_SOURCE_GENERATION,
      composition: state.reharmonizeCandidate,
    };
  }

  if (state?.versionAuditionActive) {
    const revisionId = state.versionSelectedRevisionId;
    const detail = revisionId ? state.versionRevisionDetails?.[revisionId] : null;
    if (detail && Object.prototype.hasOwnProperty.call(detail, 'composition')) {
      return {
        source: PLAYBACK_SOURCE_VERSION,
        composition: detail.composition ?? null,
      };
    }
  }

  if (state?.developmentAuditionActive) {
    const findDev = deps.findDevelopmentCandidateById;
    const candidate = typeof findDev === 'function'
      ? findDev(state.developmentCandidates, state.developmentSelectedCandidateId)
      : null;
    if (candidate?.composition) {
      return {
        source: PLAYBACK_SOURCE_DEVELOPMENT,
        composition: candidate.composition,
      };
    }
  }

  return {
    source: PLAYBACK_SOURCE_WORKING,
    composition: state?.editedMusicJson ?? null,
  };
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
