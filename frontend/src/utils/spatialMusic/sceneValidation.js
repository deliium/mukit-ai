/**
 * UI scene-body validators (not an FOA compile twin).
 * Refuse embeds of events / harmony / PCM before PUT.
 */

import {
  AZIMUTH_DEG_MAX,
  AZIMUTH_DEG_MIN,
  D_MAX,
  ELEVATION_DEG_MAX,
  ELEVATION_DEG_MIN,
  GAIN_MAX,
  GAIN_MIN,
  MAX_MOTION_KEYFRAMES,
  MAX_SOURCES,
  SCENE_SCHEMA_VERSION,
  SOURCE_KINDS,
  SPREAD_MAX,
  SPREAD_MIN,
} from './constants.js';

const PCM_KEY_HINTS = new Set([
  'pcm',
  'audio_base64',
  'wav_base64',
  'audio_bytes',
  'raw_audio',
  'base64_audio',
  'pcm_base64',
]);

/**
 * @param {unknown} payload
 * @param {string} [path]
 * @returns {{ ok: false, code: string, path: string } | null}
 */
function findForbiddenEmbed(payload, path = '') {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    return null;
  }
  for (const [key, value] of Object.entries(payload)) {
    const childPath = path ? `${path}.${key}` : key;
    if (
      key === 'events'
      || key === 'notes'
      || key === 'midi_events'
      || key === 'note_performances'
    ) {
      return { ok: false, code: 'scene_embeds_events', path: childPath };
    }
    if (key === 'harmony' && Array.isArray(value)) {
      return { ok: false, code: 'scene_embeds_harmony', path: childPath };
    }
    if (
      PCM_KEY_HINTS.has(key)
      || key.endsWith('_pcm')
      || (key.endsWith('_base64') && key.toLowerCase().includes('audio'))
    ) {
      return { ok: false, code: 'scene_embeds_pcm', path: childPath };
    }
    if (key === 'composition' || key === 'composition_json') {
      return { ok: false, code: 'scene_embeds_forbidden', path: childPath };
    }
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      const nested = findForbiddenEmbed(value, childPath);
      if (nested) return nested;
    }
    if (Array.isArray(value)) {
      for (let i = 0; i < value.length; i += 1) {
        if (value[i] && typeof value[i] === 'object') {
          const nested = findForbiddenEmbed(value[i], `${childPath}[${i}]`);
          if (nested) return nested;
        }
      }
    }
  }
  return null;
}

function inRange(value, min, max) {
  return typeof value === 'number' && Number.isFinite(value) && value >= min && value <= max;
}

/**
 * @param {unknown} scene
 * @returns {{ ok: true } | { ok: false, code: string, path?: string, message?: string }}
 */
export function validateSpatialSceneBody(scene) {
  if (!scene || typeof scene !== 'object' || Array.isArray(scene)) {
    return { ok: false, code: 'spatial_scene_invalid', message: 'scene must be an object' };
  }
  const embed = findForbiddenEmbed(scene);
  if (embed) return embed;

  if (scene.schema_version !== SCENE_SCHEMA_VERSION) {
    return { ok: false, code: 'unsupported_schema_version' };
  }
  if (typeof scene.name !== 'string' || !scene.name.trim()) {
    return { ok: false, code: 'spatial_scene_invalid', path: 'name' };
  }
  if (
    typeof scene.source_composition_fingerprint !== 'string'
    || scene.source_composition_fingerprint.length < 8
  ) {
    return { ok: false, code: 'spatial_scene_invalid', path: 'source_composition_fingerprint' };
  }

  const sources = Array.isArray(scene.sources) ? scene.sources : [];
  if (sources.length > MAX_SOURCES) {
    return { ok: false, code: 'too_many_sources', path: 'sources' };
  }

  let hasStem = false;
  const ids = new Set();
  for (let i = 0; i < sources.length; i += 1) {
    const src = sources[i];
    const base = `sources[${i}]`;
    if (!src || typeof src !== 'object') {
      return { ok: false, code: 'spatial_scene_invalid', path: base };
    }
    if (typeof src.id !== 'string' || !src.id) {
      return { ok: false, code: 'spatial_scene_invalid', path: `${base}.id` };
    }
    if (ids.has(src.id)) {
      return { ok: false, code: 'spatial_scene_invalid', path: `${base}.id`, message: 'duplicate' };
    }
    ids.add(src.id);
    if (!SOURCE_KINDS.includes(src.source_kind)) {
      return { ok: false, code: 'spatial_scene_invalid', path: `${base}.source_kind` };
    }
    if (src.source_kind === 'track' && !src.track_id) {
      return { ok: false, code: 'spatial_scene_invalid', path: `${base}.track_id` };
    }
    if (src.source_kind === 'stem') {
      hasStem = true;
      if (!src.stem_id) {
        return { ok: false, code: 'stem_id_required', path: `${base}.stem_id` };
      }
    }
    if (!inRange(src.azimuth_deg ?? 0, AZIMUTH_DEG_MIN, AZIMUTH_DEG_MAX)
      || !inRange(src.elevation_deg ?? 0, ELEVATION_DEG_MIN, ELEVATION_DEG_MAX)
      || !inRange(src.distance ?? 1, 0, D_MAX)
      || !inRange(src.spread ?? 0, SPREAD_MIN, SPREAD_MAX)
      || !inRange(src.gain ?? 1, GAIN_MIN, GAIN_MAX)) {
      return { ok: false, code: 'spatial_scene_invalid', path: base };
    }
    const motion = Array.isArray(src.motion) ? src.motion : [];
    if (motion.length > MAX_MOTION_KEYFRAMES) {
      return { ok: false, code: 'too_many_motion_keyframes', path: `${base}.motion` };
    }
  }

  if (hasStem) {
    if (!scene.source_stem_set_id || !scene.source_stem_set_fingerprint) {
      return {
        ok: false,
        code: 'stem_set_required',
        path: 'source_stem_set_id',
      };
    }
  }

  return { ok: true };
}
