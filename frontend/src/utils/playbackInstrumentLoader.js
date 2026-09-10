/**
 * Resolve local sample URL maps for a validated pack profile.
 */

import { createAppLogger } from './appLogger.js';
import {
  mapTrackToAssetProfile,
  resolveLocalAssetUrl,
  validateBrowserPlaybackManifest,
} from './browserPlaybackAssets.js';
import { resolveSynthFallbackPreset } from './playbackInstrumentPresets.js';
import {
  createInstrumentProfileCache,
  createToneSamplerAdapter,
  createToneSynthAdapter,
} from './playbackInstrumentAdapters.js';

const logger = createAppLogger('playbackInstrumentLoader');

/** @type {object|null} */
let activeManifest = null;
const profileCache = createInstrumentProfileCache();

/**
 * Install a validated pack manifest for subsequent track resolution.
 * @param {unknown} rawManifest
 * @returns {{ ok: boolean, manifest?: object, errors?: string[] }}
 */
export function setBrowserPlaybackManifest(rawManifest) {
  const result = validateBrowserPlaybackManifest(rawManifest);
  if (!result.ok) {
    activeManifest = null;
    logger.warn('Rejected playback manifest', {
      errorCount: result.errors.length,
      codes: result.errors.slice(0, 8),
    });
    return result;
  }
  activeManifest = result.manifest;
  logger.info('Playback manifest installed', {
    packId: result.manifest.packId,
    version: result.manifest.version,
    instrumentCount: result.manifest.instruments.length,
  });
  return result;
}

export function clearBrowserPlaybackManifest() {
  activeManifest = null;
  profileCache.clear();
}

export function getBrowserPlaybackManifest() {
  return activeManifest;
}

/**
 * Build pitch→URL map for a sampled profile (same-origin only).
 * @param {object} profile from mapTrackToAssetProfile
 * @param {object} manifest
 * @param {{ origin?: string }} [options]
 */
export function buildSampleUrlMap(profile, manifest, options = {}) {
  const urls = {};
  if (!profile?.sampled || !Array.isArray(profile.sampleRefs)) {
    return urls;
  }
  profile.sampleRefs.forEach((sample) => {
    const resolved = resolveLocalAssetUrl(manifest, sample.path, options);
    if (!resolved.ok || !sample.pitch) {
      logger.warn('Skipped sample ref', {
        reason: resolved.reason || 'missing_pitch',
        packId: manifest?.packId,
      });
      return;
    }
    urls[sample.pitch] = resolved.url;
  });
  return urls;
}

/**
 * Prepare per-track instrument adapters (sampled when pack allows, else synth).
 * Never swaps timbre after the returned map is frozen for a playback session.
 *
 * @param {{ Tone: object, tracks: object[], origin?: string }} options
 * @returns {Promise<Map<string, object>>} trackId → adapter
 */
export async function prepareTrackInstrumentAdapters({
  Tone,
  tracks = [],
  origin = '',
} = {}) {
  const adapters = new Map();
  const list = Array.isArray(tracks) ? tracks : [];

  await Promise.all(list.map(async (track) => {
    const trackId = String(track?.id ?? '');
    if (!trackId) {
      return;
    }
    const synthFallback = resolveSynthFallbackPreset(track);
    let adapter;

    if (activeManifest) {
      const profile = mapTrackToAssetProfile(track, activeManifest, {
        fallbackPresetId: synthFallback.presetId,
      });
      if (profile.sampled) {
        const urls = buildSampleUrlMap(profile, activeManifest, { origin });
        const cacheKey = `${activeManifest.packId}@${activeManifest.version}:${profile.profileId}`;
        adapter = await profileCache.load(cacheKey, () => createToneSamplerAdapter({
          Tone,
          urls,
          baseUrl: '',
          profileId: profile.profileId,
          fallbackTrack: track,
          onFallback: (reason) => {
            logger.warn('Per-track sampler fallback', {
              trackId,
              profileId: profile.profileId,
              reasonCode: reason,
            });
          },
        }));
      }
    }

    if (!adapter) {
      const cacheKey = `synth:${synthFallback.presetId}`;
      adapter = await profileCache.load(cacheKey, () => createToneSynthAdapter({
        Tone,
        track,
        profileId: synthFallback.presetId,
      }));
    }

    adapters.set(trackId, adapter);
    const status = adapter.getStatus();
    logger.info('Track instrument ready', {
      trackId,
      profileId: status.profileId,
      fallback: Boolean(status.fallback),
      reasonCode: status.reasonCode,
    });
  }));

  return adapters;
}

export function getInstrumentProfileCache() {
  return profileCache;
}
