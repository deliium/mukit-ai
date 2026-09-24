/**
 * Mix analysis (mix.analysis.v1) UI helpers — separate from symbolic composition.analysis.v1.
 */

export const MIX_ANALYSIS_SCHEMA_VERSION = 'mix.analysis.v1';

export const MIX_ANALYSIS_DIMENSIONS = Object.freeze([
  'peak',
  'loudness',
  'dynamic_range',
  'clipping',
  'stereo_balance',
  'spectral_balance',
  'lf_buildup',
  'masking_proxy',
  'section_loudness',
  'headroom',
]);

export const MIX_ANALYSIS_DEFAULT_DIMENSIONS = Object.freeze([...MIX_ANALYSIS_DIMENSIONS]);

export const MIX_ANALYSIS_ERROR_CODES = Object.freeze({
  STEM_SET_INCOMPLETE: 'mix_analysis_stem_set_incomplete',
  STEM_NOT_READY: 'mix_analysis_stem_not_ready',
  NOT_FOUND: 'mix_analysis_not_found',
  QUOTA_EXCEEDED: 'mix_analysis_quota_exceeded',
  INPUT_TOO_LARGE: 'mix_analysis_input_too_large',
  TIMEOUT: 'mix_analysis_timeout',
  DIMENSION_UNKNOWN: 'mix_analysis_dimension_unknown',
  METRIC_UNAVAILABLE: 'metric_unavailable',
  INTERPRETATION_UNAVAILABLE: 'interpretation_unavailable',
  INTERNAL_ERROR: 'mix_analysis_internal_error',
  REVERB_UNAVAILABLE: 'reverb_estimate_unavailable',
});

export const MIX_ANALYSIS_HONESTY_COPY =
  'Mix analysis measures rendered stem/mix audio. It does not modify stems, the mix, ' +
  'or composition.v2. AI notes are advisory and subjective.';

/**
 * @param {unknown} report
 * @returns {{ measurements: object[], observations: object[], interpretations: object[] }}
 */
export function partitionMixAnalysisLayers(report) {
  const measurements = Array.isArray(report?.measurements) ? report.measurements : [];
  const observations = Array.isArray(report?.observations) ? report.observations : [];
  const interpretations = Array.isArray(report?.interpretations)
    ? report.interpretations
    : [];
  return { measurements, observations, interpretations };
}

/**
 * @param {object|null|undefined} locus
 * @returns {string}
 */
export function formatMixAnalysisLocus(locus) {
  if (!locus || typeof locus !== 'object') {
    return '';
  }
  const parts = [];
  const roles = Array.isArray(locus.stem_roles) ? locus.stem_roles.filter(Boolean) : [];
  if (roles.length) {
    parts.push(roles.join(' + '));
  }
  const tracks = Array.isArray(locus.source_track_ids)
    ? locus.source_track_ids.filter(Boolean)
    : [];
  if (tracks.length) {
    parts.push(`tracks ${tracks.join(', ')}`);
  }
  if (
    Number.isFinite(Number(locus.freq_hz_low))
    && Number.isFinite(Number(locus.freq_hz_high))
  ) {
    parts.push(`${Math.round(Number(locus.freq_hz_low))}–${Math.round(Number(locus.freq_hz_high))} Hz`);
  }
  if (
    Number.isFinite(Number(locus.start_bar))
    && Number.isFinite(Number(locus.end_bar))
  ) {
    parts.push(`bars ${locus.start_bar}–${locus.end_bar}`);
  } else if (
    Number.isFinite(Number(locus.start_seconds))
    && Number.isFinite(Number(locus.end_seconds))
  ) {
    parts.push(
      `${Number(locus.start_seconds).toFixed(1)}–${Number(locus.end_seconds).toFixed(1)} s`,
    );
  }
  return parts.join(' · ');
}

/**
 * @param {string[]} selected
 * @returns {string[]}
 */
export function normalizeMixAnalysisDimensions(selected) {
  const allowed = new Set(MIX_ANALYSIS_DIMENSIONS);
  const source = Array.isArray(selected) && selected.length
    ? selected
    : MIX_ANALYSIS_DEFAULT_DIMENSIONS;
  const out = [];
  for (const item of source) {
    const key = String(item || '').trim().toLowerCase();
    if (allowed.has(key) && !out.includes(key)) {
      out.push(key);
    }
  }
  return out.length ? out : [...MIX_ANALYSIS_DEFAULT_DIMENSIONS];
}

/**
 * Normalize observation loci to 0–1 waveform highlight ranges.
 * @param {object[]} observations
 * @param {number} durationSeconds
 * @returns {{start:number,end:number}[]}
 */
export function observationLocusHighlightRanges(observations, durationSeconds) {
  const dur = Number(durationSeconds);
  if (!Number.isFinite(dur) || dur <= 0) {
    return [];
  }
  const out = [];
  for (const obs of Array.isArray(observations) ? observations : []) {
    const locus = obs?.locus;
    if (!locus) continue;
    let start = Number(locus.start_seconds);
    let end = Number(locus.end_seconds);
    if (!Number.isFinite(start) || !Number.isFinite(end)) {
      continue;
    }
    start = Math.max(0, Math.min(1, start / dur));
    end = Math.max(0, Math.min(1, end / dur));
    if (end < start) {
      const tmp = start;
      start = end;
      end = tmp;
    }
    out.push({ start, end });
  }
  return out;
}
