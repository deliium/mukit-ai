/**
 * Apply SpatialMix / compiled stereo to Web Audio / Tone nodes.
 * FOA coeffs come from the API only — this module is not an FOA encoder twin.
 * Motion never mutates note ticks or Tone.Transport.bpm.
 */

import { createAppLogger } from '../appLogger.js';

const logger = createAppLogger('spatialMusic');

/** @type {Map<string, { azimuth_deg: number, elevation_deg: number, distance: number, spread: number }> | null} */
let activeSpatialMixByTrackId = null;

/**
 * @param {Map<string, object> | Record<string, object> | null} mixByTrackId
 */
export function setActiveSpatialMixByTrackId(mixByTrackId) {
  if (!mixByTrackId) {
    activeSpatialMixByTrackId = null;
    logger.debug('Cleared active spatial mix map');
    return;
  }
  activeSpatialMixByTrackId = mixByTrackId instanceof Map
    ? mixByTrackId
    : new Map(Object.entries(mixByTrackId));
  logger.debug('Set active spatial mix map', {
    trackCount: activeSpatialMixByTrackId.size,
  });
}

export function getActiveSpatialMix(trackId) {
  if (!activeSpatialMixByTrackId || trackId == null) return null;
  return activeSpatialMixByTrackId.get(String(trackId)) || null;
}

/**
 * Right-handed: +Z front, +Y up, +X right; +azimuth = left (CCW).
 * @param {number} azimuthDeg
 * @param {number} elevationDeg
 * @param {number} distance
 */
export function spatialMixToCartesian(azimuthDeg, elevationDeg, distance) {
  const az = (Number(azimuthDeg) || 0) * (Math.PI / 180);
  const el = (Number(elevationDeg) || 0) * (Math.PI / 180);
  const d = Math.max(0.01, Number(distance) || 1);
  const cosEl = Math.cos(el);
  return {
    x: -Math.sin(az) * cosEl * d,
    y: Math.sin(el) * d,
    z: Math.cos(az) * cosEl * d,
  };
}

/**
 * Reconstruct Tone.Panner pan (−1 left … +1 right) from equal-power L/R gains.
 * @param {number} leftGain
 * @param {number} rightGain
 */
export function stereoGainsToPanOffset(leftGain, rightGain) {
  const left = Math.max(0, Number(leftGain) || 0);
  const right = Math.max(0, Number(rightGain) || 0);
  if (left <= 0 && right <= 0) return 0;
  const pan01 = Math.atan2(right, left) / (Math.PI / 2);
  const panStereo = pan01 * 2 - 1;
  return Math.max(-1, Math.min(1, panStereo));
}

/**
 * @param {number} distanceGain
 * @returns {number} trimDb
 */
export function distanceGainToTrimDb(distanceGain) {
  const g = Math.max(0.05, Math.min(1, Number(distanceGain) || 1));
  const db = 20 * Math.log10(g);
  return Math.max(-24, Math.min(12, db));
}

/**
 * Build preview-scope mixer overrides from API-compiled stereo (fallback path).
 * @param {object} preview spatial.preview.v1
 * @returns {{ overrides: Record<string, object>, fallbackReason: string|null, trackCount: number }}
 */
export function buildSpatialMixerOverridesFromPreview(preview) {
  const overrides = {};
  let trackCount = 0;
  const sources = Array.isArray(preview?.sources) ? preview.sources : [];
  for (const src of sources) {
    if (!src || src.source_kind !== 'track' || !src.track_id || src.skipped) continue;
    trackCount += 1;
    const left = src.stereo?.left_gain ?? 0;
    const right = src.stereo?.right_gain ?? 0;
    overrides[String(src.track_id)] = {
      panOffset: stereoGainsToPanOffset(left, right),
      trimDb: distanceGainToTrimDb(src.distance_gain ?? 1),
      muted: Boolean(src.muted),
    };
  }
  logger.info('Built spatial mixer overrides from preview', {
    trackCount,
    fallbackReason: 'compiled_stereo',
  });
  return {
    overrides,
    fallbackReason: 'compiled_stereo',
    trackCount,
  };
}

/**
 * Build track SpatialMix map from scene sources (for Panner3D path).
 * @param {object} scene
 */
export function buildSpatialMixMapFromScene(scene) {
  const map = new Map();
  for (const src of scene?.sources || []) {
    if (!src || src.source_kind !== 'track' || !src.track_id) continue;
    map.set(String(src.track_id), {
      azimuth_deg: Number(src.azimuth_deg) || 0,
      elevation_deg: Number(src.elevation_deg) || 0,
      distance: Number(src.distance) || 1,
      spread: Number(src.spread) || 0,
      muted: Boolean(src.muted),
    });
  }
  return map;
}

/**
 * Prefer Tone.Panner3D when available; otherwise report fallback reason.
 * @param {typeof import('tone') | null} Tone
 * @param {object} mix
 * @returns {{ node: object|null, reason: string }}
 */
export function createSpatialPannerNode(Tone, mix) {
  if (!Tone || typeof Tone.Panner3D !== 'function') {
    return { node: null, reason: 'panner3d_unavailable' };
  }
  try {
    const pos = spatialMixToCartesian(mix.azimuth_deg, mix.elevation_deg, mix.distance);
    const panner = new Tone.Panner3D({
      panningModel: 'HRTF',
      distanceModel: 'inverse',
      refDistance: 1,
      maxDistance: 100,
      rolloffFactor: 1,
      coneInnerAngle: 360,
      coneOuterAngle: 360,
      coneOuterGain: 0,
    });
    if (typeof panner.setPosition === 'function') {
      panner.setPosition(pos.x, pos.y, pos.z);
    } else {
      panner.positionX.value = pos.x;
      panner.positionY.value = pos.y;
      panner.positionZ.value = pos.z;
    }
    // Spread → wider cone (mild); not mix-plan stereo widen.
    const spread = Math.max(0, Math.min(1, Number(mix.spread) || 0));
    if (spread > 0 && panner.coneInnerAngle != null) {
      panner.coneInnerAngle = 360;
      panner.coneOuterAngle = 360;
    }
    return { node: panner, reason: 'panner3d' };
  } catch (error) {
    logger.warn('Panner3D create failed', { message: error?.message || String(error) });
    return { node: null, reason: 'panner3d_error' };
  }
}

/**
 * Update an existing Panner3D from SpatialMix (position only — not musical time).
 * @param {object} panner
 * @param {object} mix
 */
export function updateSpatialPannerNode(panner, mix) {
  if (!panner || !mix) return;
  const pos = spatialMixToCartesian(mix.azimuth_deg, mix.elevation_deg, mix.distance);
  if (typeof panner.setPosition === 'function') {
    panner.setPosition(pos.x, pos.y, pos.z);
  } else if (panner.positionX) {
    panner.positionX.value = pos.x;
    panner.positionY.value = pos.y;
    panner.positionZ.value = pos.z;
  }
}
