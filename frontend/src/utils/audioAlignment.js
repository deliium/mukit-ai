/**
 * Parametric audio↔symbolic alignment helpers (FE mirror of BE audio_alignment).
 *
 * source_seconds = downbeat_offset_seconds
 *                + tickToSeconds(timeline, tick − origin_tick)
 *
 * Logging: createAppLogger('audioAlignment') — method/confidence/samples only.
 */

import { createAppLogger } from './appLogger.js';
import {
  AUDIO_ALIGNMENT_ISSUE_CODES,
  AUDIO_ALIGNMENT_SCHEMA_VERSION,
} from './audioAlignmentContracts.js';
import {
  barRangeTicks,
  compileTimeline,
  secondsToTick,
  tickToSeconds,
} from './compositionTimeline.js';

const log = createAppLogger('audioAlignment');

export const TEMPO_DIVERGENCE_EPSILON_BPM = 0.5;
export const LOW_OVERALL_CONFIDENCE = 0.45;
export const SPARSE_BEAT_GRID_CONFIDENCE = 0.4;
export const DEFAULT_OFFSET_UNCERTAINTY_MS = 25;

/**
 * @param {object} timeline compiled timeline
 * @param {number} tick
 * @param {{ downbeatOffsetSeconds?: number, originTick?: number }} [opts]
 * @returns {number|null}
 */
export function tickToSourceSeconds(timeline, tick, opts = {}) {
  if (!timeline || tick == null || Number.isNaN(tick)) {
    return null;
  }
  const downbeatOffsetSeconds = Number(opts.downbeatOffsetSeconds ?? 0);
  const originTick = Number(opts.originTick ?? 0);
  const relative = Math.max(0, Math.trunc(tick) - Math.trunc(originTick));
  const clamped = Math.min(relative, timeline.durationTicks);
  const musical = tickToSeconds(timeline, clamped);
  if (musical == null) {
    return null;
  }
  const seconds = downbeatOffsetSeconds + musical;
  log.debug('tickToSourceSeconds', { tick, seconds: Number(seconds.toFixed(6)) });
  return seconds;
}

/**
 * @param {object} timeline
 * @param {number} sourceSeconds
 * @param {{ downbeatOffsetSeconds?: number, originTick?: number }} [opts]
 * @returns {number|null}
 */
export function sourceSecondsToTick(timeline, sourceSeconds, opts = {}) {
  if (!timeline || sourceSeconds == null || Number.isNaN(sourceSeconds)) {
    return null;
  }
  const downbeatOffsetSeconds = Number(opts.downbeatOffsetSeconds ?? 0);
  const originTick = Number(opts.originTick ?? 0);
  const musical = Math.max(0, Number(sourceSeconds) - downbeatOffsetSeconds);
  const relative = secondsToTick(timeline, musical);
  if (relative == null) {
    return null;
  }
  const tick = Math.trunc(originTick) + Math.round(relative);
  const clamped = Math.max(0, Math.min(tick, timeline.durationTicks + Math.trunc(originTick)));
  log.debug('sourceSecondsToTick', {
    seconds: Number(Number(sourceSeconds).toFixed(6)),
    tick: clamped,
  });
  return clamped;
}

/**
 * @param {number} startBar
 * @param {number} endBar
 * @param {object} composition composition.v2
 * @param {object} alignment audio.alignment.v1-like
 * @returns {{ startBar: number, endBar: number, startTick: number, endTick: number, startSeconds: number, endSeconds: number }|null}
 */
export function barRangeToAudioWindow(startBar, endBar, composition, alignment) {
  const timeline = compileTimeline(composition);
  if (!timeline || !alignment?.map) {
    log.warn('barRangeToAudioWindow missing timeline or alignment.map');
    return null;
  }
  const range = barRangeTicks(timeline, startBar, endBar);
  if (!range) {
    return null;
  }
  const map = alignment.map;
  const downbeatOffsetSeconds = Number(
    map.downbeat_offset_seconds ?? map.downbeatOffsetSeconds ?? 0,
  );
  const originTick = Number(map.origin_tick ?? map.originTick ?? 0);
  const startSeconds = tickToSourceSeconds(timeline, range.startTick, {
    downbeatOffsetSeconds,
    originTick,
  });
  const endSeconds = tickToSourceSeconds(timeline, range.endTick, {
    downbeatOffsetSeconds,
    originTick,
  });
  if (startSeconds == null || endSeconds == null) {
    return null;
  }
  log.info('barRangeToAudioWindow', {
    startBar,
    endBar,
    startSeconds: Number(startSeconds.toFixed(4)),
    endSeconds: Number(endSeconds.toFixed(4)),
  });
  return {
    startBar,
    endBar,
    startTick: range.startTick,
    endTick: range.endTick,
    startSeconds,
    endSeconds,
  };
}

/**
 * @param {object} params
 * @param {object} params.composition
 * @param {object} params.scaffolding recovery scaffolding-like
 * @param {string} params.sourceAudioAssetId
 * @param {string|null} [params.resultAssetId]
 * @param {string|null} [params.jobId]
 * @param {string|null} [params.projectId]
 * @param {string} [params.compositionFingerprint]
 * @param {Array<{stem: string, track_id: string}>} [params.stemBindings]
 * @returns {import('./audioAlignmentContracts.js').AudioAlignmentV1}
 */
export function buildAlignmentFromScaffolding({
  composition,
  scaffolding,
  sourceAudioAssetId,
  resultAssetId = null,
  jobId = null,
  projectId = null,
  compositionFingerprint = 'snap_pending_xxxxxxxx',
  stemBindings = [],
}) {
  if (!sourceAudioAssetId) {
    throw new Error(AUDIO_ALIGNMENT_ISSUE_CODES.ALIGNMENT_MISSING_SOURCE);
  }
  const timeline = compileTimeline(composition);
  const beatGrid = scaffolding?.beat_grid || scaffolding?.beatGrid || {};
  const scaffoldingTempo = Number(
    scaffolding?.tempo_bpm ?? scaffolding?.tempoBpm ?? timeline?.rootTempo ?? 120,
  );
  const v2Tempo = Number(timeline?.rootTempo ?? scaffoldingTempo);
  const tempoConfidence = Number(
    scaffolding?.tempo_confidence ?? scaffolding?.tempoConfidence ?? 0.5,
  );
  const beatGridConfidence = Number(beatGrid.confidence ?? 0.5);
  const downbeatOffset = Number(
    beatGrid.downbeat_offset_seconds ?? beatGrid.downbeatOffsetSeconds ?? 0,
  );
  const ticksPerQuarter = Number(
    beatGrid.ticks_per_quarter
      ?? beatGrid.ticksPerQuarter
      ?? timeline?.ticksPerQuarter
      ?? 480,
  );
  const meter = String(
    scaffolding?.meter ?? timeline?.rootTimeSignature ?? '4/4',
  );
  const tempoDiverged =
    Math.abs(v2Tempo - scaffoldingTempo) > TEMPO_DIVERGENCE_EPSILON_BPM;

  let method = 'timeline_parametric';
  const tempoSource = scaffolding?.tempo_source ?? scaffolding?.tempoSource;
  if (tempoSource === 'defaulted' && beatGridConfidence < 0.3) {
    method = 'defaulted';
  } else if (
    (tempoSource === 'estimated' || tempoSource === 'provided') &&
    !tempoDiverged
  ) {
    method = 'scaffolding';
  }

  const issues = [];
  let overall = Math.min(tempoConfidence, beatGridConfidence);
  let offsetUncertaintyMs = DEFAULT_OFFSET_UNCERTAINTY_MS;
  if (tempoDiverged) {
    issues.push(AUDIO_ALIGNMENT_ISSUE_CODES.ALIGNMENT_TEMPO_DIVERGED);
    overall = Math.min(overall, 0.35);
    offsetUncertaintyMs = Math.max(offsetUncertaintyMs, 80);
    log.warn('Alignment tempo diverged', { overall });
  }
  if (beatGridConfidence < SPARSE_BEAT_GRID_CONFIDENCE) {
    issues.push(AUDIO_ALIGNMENT_ISSUE_CODES.ALIGNMENT_SPARSE_BEAT_GRID);
    offsetUncertaintyMs = Math.max(offsetUncertaintyMs, 60);
  }
  if (overall < LOW_OVERALL_CONFIDENCE) {
    issues.push(AUDIO_ALIGNMENT_ISSUE_CODES.ALIGNMENT_LOW_CONFIDENCE);
  }

  const doc = {
    schema_version: AUDIO_ALIGNMENT_SCHEMA_VERSION,
    source_audio_asset_id: sourceAudioAssetId,
    result_asset_id: resultAssetId,
    job_id: jobId,
    project_id: projectId,
    composition_fingerprint: compositionFingerprint,
    map: {
      tempo_bpm: v2Tempo,
      ticks_per_quarter: ticksPerQuarter,
      downbeat_offset_seconds: downbeatOffset,
      origin_tick: 0,
      meter,
      scaffolding_tempo_bpm: scaffoldingTempo,
    },
    stem_bindings: stemBindings.map((b) => ({
      stem: b.stem,
      track_id: b.track_id ?? b.trackId,
      provisional_stem_label: b.provisional_stem_label ?? b.provisionalStemLabel ?? null,
    })),
    quality: {
      overall_confidence: Math.max(0, Math.min(1, overall)),
      tempo_confidence: Math.max(0, Math.min(1, tempoConfidence)),
      beat_grid_confidence: Math.max(0, Math.min(1, beatGridConfidence)),
      offset_uncertainty_ms: offsetUncertaintyMs,
      method,
      issues: [...new Set(issues)],
      stem_qualities: [],
    },
    created_at: new Date().toISOString(),
    playable: false,
  };

  log.info('Built audio.alignment.v1', {
    method: doc.quality.method,
    overall_confidence: Number(doc.quality.overall_confidence.toFixed(4)),
    bar_count: timeline?.barCount ?? null,
    source_asset_prefix: String(sourceAudioAssetId).slice(0, 8),
  });
  return doc;
}

/**
 * Read map params from either snake_case or camelCase alignment documents.
 * @param {object|null|undefined} alignment
 */
export function alignmentMapOpts(alignment) {
  const map = alignment?.map;
  if (!map) {
    return { downbeatOffsetSeconds: 0, originTick: 0 };
  }
  return {
    downbeatOffsetSeconds: Number(
      map.downbeat_offset_seconds ?? map.downbeatOffsetSeconds ?? 0,
    ),
    originTick: Number(map.origin_tick ?? map.originTick ?? 0),
  };
}
