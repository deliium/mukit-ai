/**
 * Ephemeral browser mixer control helpers (session-only).
 * Never write these into composition.v2, undo stacks, or persistence payloads.
 */

import {
  DEFAULT_SESSION_TRACK_CONTROLS,
  SESSION_PAN_OFFSET_MAX,
  SESSION_PAN_OFFSET_MIN,
  SESSION_REVERB_SEND_MAX,
  SESSION_REVERB_SEND_MIN,
  SESSION_TRIM_DB_MAX,
  SESSION_TRIM_DB_MIN,
  legacyVolumeMidiToTrimDb,
} from './playbackTracks.js';
import {
  PLAYBACK_MIXER_SCOPE_ARRANGEMENT,
  PLAYBACK_MIXER_SCOPE_DEVELOPMENT,
  PLAYBACK_MIXER_SCOPE_PREVIEW,
  PLAYBACK_MIXER_SCOPE_VERSION,
  PLAYBACK_MIXER_SCOPE_WORKING,
} from './playbackSource.js';

export const MIXER_CONTROL_STATE_KEYS = Object.freeze({
  [PLAYBACK_MIXER_SCOPE_WORKING]: 'trackControls',
  [PLAYBACK_MIXER_SCOPE_DEVELOPMENT]: 'developmentCandidateTrackControls',
  [PLAYBACK_MIXER_SCOPE_ARRANGEMENT]: 'arrangementCandidateTrackControls',
  [PLAYBACK_MIXER_SCOPE_VERSION]: 'versionAuditionTrackControls',
  [PLAYBACK_MIXER_SCOPE_PREVIEW]: 'previewTrackControls',
});

/** Activity delta below this is treated as unchanged (avoids Zustand spam). */
export const ACTIVITY_MATERIAL_DELTA = 0.02;

/**
 * @param {number} [volumeMidi]
 * @returns {object}
 */
export function createDefaultTrackControl(volumeMidi = 100) {
  const midi = clampMidi(volumeMidi, 100);
  return {
    muted: false,
    solo: false,
    volumeMidi: midi,
    trimDb: 0,
    panOffset: 0,
    reverbSend: 0,
    presetId: null,
  };
}

/**
 * Normalize a partial control patch into bounded session fields.
 * @param {object} [patch]
 * @param {object} [current]
 * @param {number} [canonicalVolumeMidi]
 */
export function normalizeTrackControlPatch(patch = {}, current = null, canonicalVolumeMidi = 100) {
  const base = current ? { ...createDefaultTrackControl(canonicalVolumeMidi), ...current } : createDefaultTrackControl(canonicalVolumeMidi);
  const next = { ...base };

  if (patch.muted !== undefined) {
    next.muted = Boolean(patch.muted);
  }
  if (patch.solo !== undefined) {
    next.solo = Boolean(patch.solo);
  }
  if (patch.volumeMidi !== undefined && patch.volumeMidi !== null) {
    next.volumeMidi = clampMidi(patch.volumeMidi, base.volumeMidi);
    // Prefer explicit trimDb when both present; otherwise derive from legacy fader.
    if (patch.trimDb === undefined || patch.trimDb === null) {
      next.trimDb = legacyVolumeMidiToTrimDb(next.volumeMidi, canonicalVolumeMidi);
    }
  }
  if (patch.trimDb !== undefined && patch.trimDb !== null) {
    next.trimDb = clampNumber(patch.trimDb, SESSION_TRIM_DB_MIN, SESSION_TRIM_DB_MAX, 0);
  }
  if (patch.panOffset !== undefined && patch.panOffset !== null) {
    next.panOffset = clampNumber(patch.panOffset, SESSION_PAN_OFFSET_MIN, SESSION_PAN_OFFSET_MAX, 0);
  }
  if (patch.reverbSend !== undefined && patch.reverbSend !== null) {
    next.reverbSend = clampNumber(
      patch.reverbSend,
      SESSION_REVERB_SEND_MIN,
      SESSION_REVERB_SEND_MAX,
      0,
    );
  }
  if (patch.presetId !== undefined) {
    next.presetId = patch.presetId == null || patch.presetId === ''
      ? null
      : String(patch.presetId).slice(0, 64);
  }

  return next;
}

/**
 * Build default mixer map for a composition's tracks.
 * @param {object|null} musicJson
 */
export function buildDefaultMixerControls(musicJson) {
  if (!musicJson || !Array.isArray(musicJson.tracks)) {
    return {};
  }
  const controls = {};
  musicJson.tracks.forEach((track) => {
    const trackId = String(track.id);
    const volume = Number(track.volume);
    controls[trackId] = createDefaultTrackControl(Number.isFinite(volume) ? volume : 100);
  });
  return controls;
}

/**
 * Merge existing session controls onto current track IDs without pruning foreign scopes.
 * @param {object} existing
 * @param {object|null} musicJson
 */
export function mergeMixerControls(existing, musicJson) {
  const defaults = buildDefaultMixerControls(musicJson);
  const merged = {};
  Object.keys(defaults).forEach((trackId) => {
    const prior = existing?.[trackId] || {};
    const volumeMidi = prior.volumeMidi ?? defaults[trackId].volumeMidi;
    merged[trackId] = normalizeTrackControlPatch(prior, defaults[trackId], volumeMidi);
    merged[trackId].volumeMidi = volumeMidi;
  });
  return merged;
}

/**
 * Resolve Zustand state key for a mixer scope.
 * @param {string} mixerScope
 * @returns {string}
 */
export function mixerControlsStateKey(mixerScope) {
  return MIXER_CONTROL_STATE_KEYS[mixerScope] || MIXER_CONTROL_STATE_KEYS[PLAYBACK_MIXER_SCOPE_WORKING];
}

/**
 * @param {{ tracks?: Record<string, number>, clipped?: boolean }|null} previous
 * @param {{ tracks?: Record<string, number>, clipped?: boolean }|null} next
 */
export function activityLevelsMateriallyChanged(previous, next) {
  const prevTracks = previous?.tracks || {};
  const nextTracks = next?.tracks || {};
  if (Boolean(previous?.clipped) !== Boolean(next?.clipped)) {
    return true;
  }
  const ids = new Set([...Object.keys(prevTracks), ...Object.keys(nextTracks)]);
  for (const id of ids) {
    const a = Number(prevTracks[id]) || 0;
    const b = Number(nextTracks[id]) || 0;
    if (Math.abs(a - b) >= ACTIVITY_MATERIAL_DELTA) {
      return true;
    }
  }
  return false;
}

/**
 * Bounded metadata for mixer logs — strip composition / event payloads.
 * @param {object} [meta]
 */
export function sanitizeMixerLogMeta(meta = {}) {
  const out = {};
  const allow = new Set([
    'trackId',
    'mixerScope',
    'sourceKey',
    'sourceKind',
    'muted',
    'solo',
    'trimDb',
    'panOffset',
    'reverbSend',
    'volumeMidi',
    'presetId',
    'reason',
    'clipped',
    'trackCount',
    'sessionId',
    'opId',
  ]);
  Object.keys(meta || {}).forEach((key) => {
    if (!allow.has(key)) {
      return;
    }
    const value = meta[key];
    if (typeof value === 'string') {
      out[key] = value.slice(0, 96);
    } else if (typeof value === 'number' || typeof value === 'boolean' || value == null) {
      out[key] = value;
    }
  });
  return out;
}

export { DEFAULT_SESSION_TRACK_CONTROLS };

function clampMidi(value, fallback = 100) {
  const n = Number(value);
  if (!Number.isFinite(n)) {
    return fallback;
  }
  return Math.max(0, Math.min(127, Math.round(n)));
}

function clampNumber(value, min, max, fallback) {
  const n = Number(value);
  if (!Number.isFinite(n)) {
    return fallback;
  }
  return Math.max(min, Math.min(max, n));
}
