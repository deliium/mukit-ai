/**
 * Composition development candidates: edit fingerprints, request defaults,
 * candidate normalization, and apply-time preservation verification.
 *
 * Preview candidates are ephemeral — never persist into project payloads.
 */

import { canonicalizeValue } from './compositionCanonical.js';
import {
  findSectionByAnalysisKey,
  listAnalysisSectionOptions,
  makeAnalysisSectionKey,
} from './compositionAnalysis.js';
import { compileTimeline } from './compositionTimeline.js';
import { isCanonicalComposition } from './musicJsonValidation.js';

export const EDIT_FINGERPRINT_PROFILE = 'composition.edit.v1';
export const EDIT_FINGERPRINT_LOG_PREFIX_LEN = 12;
export const DEVELOPMENT_ALGORITHM_VERSION = 'composition.development.v1';

export const DEVELOPMENT_OPERATIONS = Object.freeze(['continue', 'add_section', 'vary_section']);
export const DEVELOPMENT_INTENTS = Object.freeze(['continue', 'develop', 'contrast']);
export const VARIATION_STRENGTHS = Object.freeze(['conservative', 'balanced', 'experimental']);
export const DEVELOPMENT_SECTION_TYPES = Object.freeze([
  'intro',
  'verse',
  'chorus',
  'bridge',
  'outro',
  'solo',
  'break',
  'interlude',
  'prechorus',
  'other',
]);

export const DEVELOPMENT_MIN_CANDIDATE_COUNT = 1;
export const DEVELOPMENT_MAX_CANDIDATE_COUNT = 4;
export const DEVELOPMENT_MAX_OUTPUT_BARS = 64;
export const DEVELOPMENT_MAX_INSTRUCTION_CHARS = 500;

/** Re-export stable section keys for Develop UI without duplicating analysis helpers. */
export {
  makeAnalysisSectionKey as makeDevelopmentSectionKey,
  listAnalysisSectionOptions as listDevelopmentSectionOptions,
  findSectionByAnalysisKey as findSectionByDevelopmentKey,
};

export function editFingerprintLogPrefix(fingerprint) {
  return String(fingerprint || '').slice(0, EDIT_FINGERPRINT_LOG_PREFIX_LEN);
}

function canonicalEditJsonDumps(value) {
  return JSON.stringify(canonicalizeValue(value));
}

/**
 * Full-document projection matching backend composition.edit.v1.
 * Includes every persisted V2 field (expression, markers, motifs, …).
 */
export function fullDocumentEditProjection(composition) {
  if (!composition || typeof composition !== 'object' || Array.isArray(composition)) {
    return { fingerprint_profile: EDIT_FINGERPRINT_PROFILE, document: null };
  }
  return {
    fingerprint_profile: EDIT_FINGERPRINT_PROFILE,
    document: composition,
  };
}

export async function compositionEditFingerprint(composition) {
  const encoded = canonicalEditJsonDumps(fullDocumentEditProjection(composition));
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(encoded));
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('');
}

function eventFingerprint(event) {
  return [
    event?.type || 'note',
    event?.id ?? null,
    event?.pitch,
    Number(event?.start_tick),
    Number(event?.duration_ticks),
    Number(event?.velocity || 0),
  ].join('|');
}

function trackTopologyFingerprint(track) {
  return [
    track?.id,
    track?.name,
    track?.instrument,
    track?.role,
    track?.midi_program,
    track?.channel,
    Boolean(track?.is_drum),
    track?.staff ?? null,
  ].join('|');
}

/**
 * Default source/output controls for Develop tab modes.
 */
export function resolveDevelopmentDefaults(composition, {
  operation = 'continue',
  aiEditStartBar = null,
  aiEditEndBar = null,
} = {}) {
  const barCount = Number(composition?.bar_count) || 0;
  const sections = listAnalysisSectionOptions(composition);
  const trailing = sections.length ? sections[sections.length - 1] : null;

  if (operation === 'vary_section') {
    const start = Number.isInteger(aiEditStartBar) && aiEditStartBar >= 1
      ? aiEditStartBar
      : (trailing?.start_bar ?? 1);
    const end = Number.isInteger(aiEditEndBar) && aiEditEndBar >= start
      ? aiEditEndBar
      : (trailing
        ? (trailing.start_bar + trailing.bar_count - 1)
        : Math.max(1, barCount));
    return {
      operation,
      sourceStartBar: start,
      sourceEndBar: end,
      sourceSectionKey: trailing?.key ?? null,
      outputBars: null,
      targetSectionType: null,
    };
  }

  return {
    operation,
    sourceStartBar: trailing?.start_bar ?? (barCount > 0 ? 1 : null),
    sourceEndBar: trailing
      ? (trailing.start_bar + trailing.bar_count - 1)
      : (barCount > 0 ? barCount : null),
    sourceSectionKey: trailing?.key ?? null,
    outputBars: 8,
    targetSectionType: operation === 'add_section' ? 'verse' : null,
  };
}

export function normalizeDevelopmentRequest(payload) {
  const operation = payload?.operation;
  if (!DEVELOPMENT_OPERATIONS.includes(operation)) {
    return { ok: false, code: 'development_invalid_operation', message: 'Unsupported development operation' };
  }
  const intent = payload?.development_intent || 'continue';
  if (!DEVELOPMENT_INTENTS.includes(intent)) {
    return { ok: false, code: 'development_invalid_operation', message: 'Unsupported development intent' };
  }
  const strength = payload?.variation_strength;
  if (!VARIATION_STRENGTHS.includes(strength)) {
    return { ok: false, code: 'development_invalid_operation', message: 'Unsupported variation strength' };
  }
  const candidateCount = Number(payload?.candidate_count ?? 1);
  if (!Number.isInteger(candidateCount)
    || candidateCount < DEVELOPMENT_MIN_CANDIDATE_COUNT
    || candidateCount > DEVELOPMENT_MAX_CANDIDATE_COUNT) {
    return { ok: false, code: 'development_invalid_operation', message: 'candidate_count must be 1-4' };
  }

  let outputBars = payload?.output_bars;
  if (operation === 'vary_section') {
    if (outputBars != null) {
      return { ok: false, code: 'development_invalid_operation', message: 'vary_section omits output_bars' };
    }
  } else {
    outputBars = Number(outputBars);
    if (!Number.isInteger(outputBars) || outputBars < 1 || outputBars > DEVELOPMENT_MAX_OUTPUT_BARS) {
      return { ok: false, code: 'development_output_bars_required', message: 'output_bars must be 1-64' };
    }
  }

  const source = payload?.source && typeof payload.source === 'object' ? { ...payload.source } : null;
  if (operation === 'vary_section') {
    const hasSection = typeof source?.section_id === 'string' && source.section_id.trim();
    const hasBars = Number.isInteger(source?.start_bar) && Number.isInteger(source?.end_bar);
    if (!hasSection && !hasBars) {
      return { ok: false, code: 'development_source_required', message: 'vary_section requires a source' };
    }
  }

  let targetSectionType = payload?.target_section_type ?? null;
  if (operation === 'add_section') {
    if (!targetSectionType || !DEVELOPMENT_SECTION_TYPES.includes(targetSectionType)) {
      return {
        ok: false,
        code: 'development_unsupported_section_type',
        message: 'add_section requires a supported target_section_type',
      };
    }
  } else if (targetSectionType != null && !DEVELOPMENT_SECTION_TYPES.includes(targetSectionType)) {
    return {
      ok: false,
      code: 'development_unsupported_section_type',
      message: 'Unsupported target_section_type',
    };
  }

  let instruction = payload?.instruction ?? null;
  if (typeof instruction === 'string') {
    instruction = instruction.trim().slice(0, DEVELOPMENT_MAX_INSTRUCTION_CHARS) || null;
  } else {
    instruction = null;
  }

  return {
    ok: true,
    request: {
      composition: payload.composition,
      operation,
      source,
      output_bars: operation === 'vary_section' ? null : outputBars,
      target_section_type: targetSectionType,
      target_section_label: typeof payload?.target_section_label === 'string'
        ? payload.target_section_label.trim().slice(0, 80) || null
        : null,
      development_intent: intent,
      variation_strength: strength,
      candidate_count: candidateCount,
      allow_modulation: Boolean(payload?.allow_modulation),
      instruction,
      selection: payload?.selection && typeof payload.selection === 'object'
        ? payload.selection
        : {},
      options: payload?.options && typeof payload.options === 'object'
        ? payload.options
        : {},
    },
  };
}

function validateCandidateContract(candidate, index) {
  if (!candidate || typeof candidate !== 'object' || Array.isArray(candidate)) {
    return `Candidate ${index} is not an object`;
  }
  if (typeof candidate.candidate_id !== 'string' || candidate.candidate_id.trim().length < 8) {
    return `Candidate ${index} missing candidate_id`;
  }
  if (typeof candidate.candidate_fingerprint !== 'string' || candidate.candidate_fingerprint.length < 16) {
    return `Candidate ${index} missing candidate_fingerprint`;
  }
  if (typeof candidate.edit_source_fingerprint !== 'string' || candidate.edit_source_fingerprint.length < 16) {
    return `Candidate ${index} missing edit_source_fingerprint`;
  }
  if (!isCanonicalComposition(candidate.composition)) {
    return `Candidate ${index} composition is not canonical V2`;
  }
  // Full validateMusicJson runs after prepareCompositionForStore at the API boundary.
  if (!Array.isArray(candidate.preservation)) {
    return `Candidate ${index} missing preservation assertions`;
  }
  return null;
}

/**
 * Normalize multi-candidate preview response. Rejects duplicates / malformed entries.
 * Does not mutate the inbound response or compositions.
 */
export function normalizeDevelopmentPreviewResponse(response) {
  if (!response || typeof response !== 'object' || Array.isArray(response)) {
    return { ok: false, code: 'development_invalid_response', message: 'Empty development preview response' };
  }
  if (typeof response.edit_source_fingerprint !== 'string' || response.edit_source_fingerprint.length < 16) {
    return { ok: false, code: 'development_invalid_response', message: 'Missing edit_source_fingerprint' };
  }
  const candidates = Array.isArray(response.candidates) ? response.candidates : null;
  if (!candidates
    || candidates.length < DEVELOPMENT_MIN_CANDIDATE_COUNT
    || candidates.length > DEVELOPMENT_MAX_CANDIDATE_COUNT) {
    return { ok: false, code: 'development_invalid_response', message: 'candidates must contain 1-4 items' };
  }

  const ids = new Set();
  const normalized = [];
  for (let index = 0; index < candidates.length; index += 1) {
    const err = validateCandidateContract(candidates[index], index);
    if (err) {
      return { ok: false, code: 'development_invalid_response', message: err };
    }
    const id = candidates[index].candidate_id.trim();
    if (ids.has(id)) {
      return { ok: false, code: 'development_invalid_response', message: 'Duplicate candidate_id' };
    }
    ids.add(id);
    if (candidates[index].edit_source_fingerprint !== response.edit_source_fingerprint) {
      return {
        ok: false,
        code: 'development_invalid_response',
        message: 'Candidate edit_source_fingerprint mismatch',
      };
    }
    normalized.push({
      ...candidates[index],
      candidate_id: id,
      composition: candidates[index].composition,
    });
  }

  return {
    ok: true,
    response: {
      ...response,
      candidates: normalized,
      warning_codes: Array.isArray(response.warning_codes) ? response.warning_codes.slice(0, 32) : [],
    },
  };
}

export function findDevelopmentCandidateById(candidates, candidateId) {
  if (!Array.isArray(candidates) || typeof candidateId !== 'string') {
    return null;
  }
  return candidates.find((item) => item?.candidate_id === candidateId) || null;
}

function eventsOutsideRange(events, startTick, endTick) {
  return (events || [])
    .filter((event) => {
      const start = Number(event.start_tick);
      const end = start + Number(event.duration_ticks);
      return end <= startTick || start >= endTick;
    })
    .map(eventFingerprint);
}

function eventsInPrefix(events, seamTick) {
  return (events || [])
    .filter((event) => Number(event.start_tick) < seamTick)
    .map(eventFingerprint);
}

/**
 * Independently verify a selected candidate before apply.
 * Recomputes candidate fingerprint locally; does not trust the response value alone.
 */
export async function verifyDevelopmentCandidate({
  baseComposition,
  candidate,
  operation,
  responseSourceFingerprint,
}) {
  const failures = [];
  if (!candidate || !baseComposition) {
    return { ok: false, failures: [{ code: 'missing_candidate' }] };
  }

  const localSourceFp = await compositionEditFingerprint(baseComposition);
  if (responseSourceFingerprint && localSourceFp !== responseSourceFingerprint) {
    failures.push({ code: 'stale_edit_source_fingerprint' });
  }
  if (candidate.edit_source_fingerprint && localSourceFp !== candidate.edit_source_fingerprint) {
    failures.push({ code: 'stale_candidate_source_fingerprint' });
  }

  const localCandidateFp = await compositionEditFingerprint(candidate.composition);
  if (localCandidateFp !== candidate.candidate_fingerprint) {
    failures.push({ code: 'candidate_fingerprint_mismatch' });
  }

  const baseTracks = baseComposition.tracks || [];
  const candTracks = candidate.composition?.tracks || [];
  if (baseTracks.length !== candTracks.length) {
    failures.push({ code: 'track_topology_mismatch' });
  }
  for (let i = 0; i < baseTracks.length; i += 1) {
    const baseTrack = baseTracks[i];
    const candTrack = candTracks[i];
    if (!candTrack || trackTopologyFingerprint(baseTrack) !== trackTopologyFingerprint(candTrack)) {
      failures.push({ code: 'track_topology_mismatch', track_id: baseTrack?.id ?? null });
    }
  }

  const outputRange = candidate.output_range || {};
  const startTick = Number(outputRange.start_tick);
  const endTick = Number(outputRange.end_tick);
  const seamTick = Number.isFinite(startTick) ? startTick : Number(baseComposition.duration_ticks);

  if (operation === 'continue' || operation === 'add_section') {
    if (Number(candidate.composition.bar_count) <= Number(baseComposition.bar_count)) {
      failures.push({ code: 'append_bar_count_not_extended' });
    }
    for (let i = 0; i < baseTracks.length; i += 1) {
      const basePrefix = eventsInPrefix(baseTracks[i].events, Number(baseComposition.duration_ticks));
      const candPrefix = eventsInPrefix(candTracks[i]?.events, Number(baseComposition.duration_ticks));
      if (basePrefix.join(',') !== candPrefix.join(',')) {
        failures.push({ code: 'append_prefix_changed', track_id: baseTracks[i].id });
      }
    }
    // Sections that existed before must remain byte-identical in order/prefix.
    const baseSections = baseComposition.sections || [];
    const candSections = candidate.composition.sections || [];
    for (let i = 0; i < baseSections.length; i += 1) {
      if (JSON.stringify(canonicalizeValue(baseSections[i]))
        !== JSON.stringify(canonicalizeValue(candSections[i]))) {
        failures.push({ code: 'append_section_prefix_changed' });
        break;
      }
    }
  } else if (operation === 'vary_section') {
    if (!Number.isFinite(startTick) || !Number.isFinite(endTick) || endTick <= startTick) {
      failures.push({ code: 'invalid_variation_range' });
    } else {
      for (let i = 0; i < baseTracks.length; i += 1) {
        const baseOutside = eventsOutsideRange(baseTracks[i].events, startTick, endTick);
        const candOutside = eventsOutsideRange(candTracks[i]?.events, startTick, endTick);
        if (baseOutside.join(',') !== candOutside.join(',')) {
          failures.push({ code: 'outside_range_changed', track_id: baseTracks[i].id });
        }
      }
      if (Number(candidate.composition.bar_count) !== Number(baseComposition.bar_count)
        || Number(candidate.composition.duration_ticks) !== Number(baseComposition.duration_ticks)) {
        failures.push({ code: 'variation_geometry_changed' });
      }
    }
  }

  const requiredAssertions = (candidate.preservation || []).filter(
    (item) => item && (item.required === true || item.severity === 'required'),
  );
  for (const assertion of requiredAssertions) {
    if (assertion.passed === false || assertion.ok === false || assertion.status === 'failed') {
      failures.push({ code: assertion.code || 'required_assertion_failed' });
    }
  }

  // Timeline compile must succeed for audition/apply.
  try {
    compileTimeline(candidate.composition);
  } catch (error) {
    failures.push({ code: 'candidate_timeline_invalid', message: error.message });
  }

  return {
    ok: failures.length === 0,
    failures,
    localSourceFingerprint: localSourceFp,
    localCandidateFingerprint: localCandidateFp,
    seamTick,
  };
}
