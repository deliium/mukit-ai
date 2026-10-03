/**
 * Spatial audition helpers: compile-body assembly + stem player orchestration.
 * FOA coeffs always come from API compile — this is not an FOA twin.
 * Motion never mutates note ticks or Tone.Transport.bpm.
 */

import { createAppLogger } from '../appLogger.js';
import {
  buildSpatialMixMapFromScene,
  createSpatialPannerNode,
  distanceGainToTrimDb,
  stereoGainsToPanOffset,
} from './spatialApply.js';
import {
  prepareStemSpatialPlayer,
  teardownAllStemSpatialPlayers,
} from './stemSpatialPlayer.js';

const logger = createAppLogger('spatialMusic.audition');

export function sceneHasTrackSources(scene) {
  return (scene?.sources || []).some((s) => s?.source_kind === 'track');
}

export function sceneHasStemSources(scene) {
  return (scene?.sources || []).some((s) => s?.source_kind === 'stem');
}

/**
 * Build stem meta list for SpatialCompileRequest from a neural stem-set document.
 * @param {object|null} stemSet
 * @returns {{ stem_id: string, sha256_prefix: string, role?: string }[]}
 */
export function buildStemMetaFromStemSet(stemSet) {
  const stems = Array.isArray(stemSet?.stems) ? stemSet.stems : [];
  const out = [];
  for (const stem of stems) {
    const stemId = stem?.id || stem?.stem_id;
    const prefix = stem?.sha256_prefix;
    if (!stemId || typeof prefix !== 'string' || prefix.length < 8) continue;
    out.push({
      stem_id: String(stemId),
      sha256_prefix: String(prefix),
      role: stem?.role || stem?.stem_role || undefined,
    });
  }
  return out;
}

/**
 * Assemble POST .../compile body (draft-safe; no project write).
 * @param {object} opts
 * @param {object} opts.scene
 * @param {object|null} [opts.composition]
 * @param {object|null} [opts.stemSet]
 * @param {number} [opts.atTick]
 */
export function buildSpatialCompileBody({
  scene,
  composition = null,
  stemSet = null,
  atTick = 0,
}) {
  const body = {
    scene,
    at_tick: Math.max(0, Number(atTick) || 0),
  };
  if (sceneHasTrackSources(scene) && composition) {
    body.composition = composition;
  }
  if (sceneHasStemSources(scene)) {
    const stemSetId = stemSet?.id || scene?.source_stem_set_id || null;
    const stems = buildStemMetaFromStemSet(stemSet);
    if (stemSetId) body.stem_set_id = String(stemSetId);
    if (stems.length) {
      body.stems = stems;
    } else if (scene?.source_stem_set_fingerprint) {
      body.stem_set_fingerprint = scene.source_stem_set_fingerprint;
    }
  }
  return body;
}

/**
 * Preview source lookup by stem_id for skip / stereo fallback.
 * @param {object|null} preview
 */
export function previewStemById(preview) {
  const map = new Map();
  for (const src of preview?.sources || []) {
    if (src?.source_kind === 'stem' && src.stem_id) {
      map.set(String(src.stem_id), src);
    }
  }
  return map;
}

/**
 * Create a spatial destination chain for one stem and connect to master.
 * @returns {{ input: object, nodes: object[], reason: string } | null}
 */
export function createStemSpatialDestination(Tone, mix, stereo, masterDestination) {
  if (!Tone || !masterDestination) return null;
  const created = createSpatialPannerNode(Tone, mix || {
    azimuth_deg: 0,
    elevation_deg: 0,
    distance: 1,
    spread: 0,
  });
  if (created.node) {
    created.node.connect(masterDestination);
    return { input: created.node, nodes: [created.node], reason: created.reason };
  }
  // Compiled-stereo fallback when Panner3D unavailable.
  const left = stereo?.left_gain ?? 0.7071;
  const right = stereo?.right_gain ?? 0.7071;
  const panOffset = stereoGainsToPanOffset(left, right);
  const trimDb = distanceGainToTrimDb(stereo?.distance_gain ?? 1);
  const linearGain = 10 ** (trimDb / 20);
  const nodes = [];
  let input;
  if (typeof Tone.Panner === 'function') {
    const gain = new Tone.Gain(linearGain);
    const panner = new Tone.Panner(panOffset);
    gain.connect(panner);
    panner.connect(masterDestination);
    nodes.push(gain, panner);
    input = gain;
  } else {
    const gain = new Tone.Gain(linearGain);
    gain.connect(masterDestination);
    nodes.push(gain);
    input = gain;
  }
  return { input, nodes, reason: 'compiled_stereo' };
}

/**
 * Fetch/decode/schedule stem sources onto shared Transport; skip missing softly.
 * @param {object} opts
 * @param {typeof import('tone')} opts.Tone
 * @param {object} opts.scene
 * @param {object|null} opts.preview
 * @param {object} opts.masterDestination
 * @param {(stemId: string) => Promise<ArrayBuffer>} opts.fetchStemArrayBuffer
 */
export async function prepareSceneStemSpatialPlayers({
  Tone,
  scene,
  preview = null,
  masterDestination,
  fetchStemArrayBuffer,
}) {
  teardownAllStemSpatialPlayers();
  if (!Tone || !scene || !masterDestination || typeof fetchStemArrayBuffer !== 'function') {
    return { prepared: 0, skipped: 0, failed: 0, reasons: ['missing_args'] };
  }

  const mixByStem = new Map();
  for (const src of scene.sources || []) {
    if (src?.source_kind !== 'stem' || !src.stem_id) continue;
    mixByStem.set(String(src.stem_id), {
      azimuth_deg: Number(src.azimuth_deg) || 0,
      elevation_deg: Number(src.elevation_deg) || 0,
      distance: Number(src.distance) || 1,
      spread: Number(src.spread) || 0,
      muted: Boolean(src.muted),
    });
  }

  const previewByStem = previewStemById(preview);
  let prepared = 0;
  let skipped = 0;
  let failed = 0;
  const reasons = [];

  for (const [stemId, mix] of mixByStem) {
    if (mix.muted) {
      skipped += 1;
      reasons.push(`muted:${stemId}`);
      continue;
    }
    const previewSrc = previewByStem.get(stemId);
    if (previewSrc?.skipped) {
      skipped += 1;
      reasons.push(`preview_skipped:${stemId}`);
      logger.info('Skipping stem spatial player (preview skipped)', { stemId });
      continue;
    }
    const chain = createStemSpatialDestination(
      Tone,
      mix,
      previewSrc?.stereo
        ? { ...previewSrc.stereo, distance_gain: previewSrc.distance_gain }
        : null,
      masterDestination,
    );
    if (!chain) {
      failed += 1;
      reasons.push(`no_destination:${stemId}`);
      continue;
    }
    const result = await prepareStemSpatialPlayer({
      Tone,
      stemId,
      destination: chain.input,
      extraNodes: chain.nodes,
      fetchArrayBuffer: async () => fetchStemArrayBuffer(stemId),
      url: `stem:${stemId}`,
    });
    if (result.ok) {
      prepared += 1;
      reasons.push(`${chain.reason}:${stemId}`);
    } else {
      failed += 1;
      reasons.push(`${result.reason}:${stemId}`);
      // Dispose orphan chain when player failed.
      for (const node of chain.nodes) {
        try {
          node.disconnect?.();
          node.dispose?.();
        } catch {
          // ignore
        }
      }
    }
  }

  logger.info('Prepared scene stem spatial players', {
    prepared,
    skipped,
    failed,
    trackMixCount: buildSpatialMixMapFromScene(scene).size,
  });
  return { prepared, skipped, failed, reasons };
}
