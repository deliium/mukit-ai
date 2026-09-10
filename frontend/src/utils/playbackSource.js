/**
 * Mutual-exclusive playback source resolution for working / preview / version audition.
 * Pure helpers — no logging of composition payloads.
 */

export const PLAYBACK_SOURCE_WORKING = 'working';
export const PLAYBACK_SOURCE_DEVELOPMENT = 'development';
export const PLAYBACK_SOURCE_ARRANGEMENT = 'arrangement';
export const PLAYBACK_SOURCE_VERSION = 'version';

/**
 * Resolve which composition the transport should play.
 * Priority: arrangement candidate → version audition → development audition → working.
 *
 * @param {object} state
 * @param {{
 *   findDevelopmentCandidateById?: Function,
 *   findArrangementCandidateById?: Function,
 *   arrangementCandidateMode?: string,
 * }} [deps]
 * @returns {{
 *   source: 'working'|'development'|'arrangement'|'version',
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
 * @param {'working'|'development'|'arrangement'|'version'} source
 * @param {string} arrangementSourceMode
 * @returns {object}
 */
export function exclusiveAuditionPatch(source, arrangementSourceMode = 'source') {
  if (source === PLAYBACK_SOURCE_ARRANGEMENT) {
    return {
      developmentAuditionActive: false,
      versionAuditionActive: false,
    };
  }
  if (source === PLAYBACK_SOURCE_DEVELOPMENT) {
    return {
      arrangementAuditionMode: arrangementSourceMode,
      versionAuditionActive: false,
    };
  }
  if (source === PLAYBACK_SOURCE_VERSION) {
    return {
      developmentAuditionActive: false,
      arrangementAuditionMode: arrangementSourceMode,
    };
  }
  return {
    developmentAuditionActive: false,
    arrangementAuditionMode: arrangementSourceMode,
    versionAuditionActive: false,
  };
}
