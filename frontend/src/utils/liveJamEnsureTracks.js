/**
 * Pure helper: ensure destination tracks exist for AI Jam Commit role map.
 *
 * Creates valid composition.v2 tracks when opted in — never invents note events.
 * Jam ephemeral roles (accompaniment/texture) map onto supported V2 track roles.
 *
 * Logging: createAppLogger('liveJam') — counts / id prefixes only.
 */

import { createAppLogger } from './appLogger.js';
import { JAM_TRACK_ROLES } from './liveJamContracts.js';
import { SUPPORTED_TRACK_ROLES, validateMusicJson } from './musicJsonValidation.js';

const log = createAppLogger('liveJam');

/** @typedef {'melody'|'bass'|'harmony'|'accompaniment'|'texture'} JamTrackRole */

/**
 * Map jam ephemeral track_role → canonical V2 track.role.
 * @type {Readonly<Record<JamTrackRole, string>>}
 */
export const JAM_ROLE_TO_V2_TRACK_ROLE = Object.freeze({
  melody: 'melody',
  bass: 'bass',
  harmony: 'harmony',
  accompaniment: 'harmony',
  texture: 'pad',
});

/** Default GM program / display names when instrument_set omits them. */
export const DEFAULT_JAM_INSTRUMENTS = Object.freeze({
  melody: Object.freeze({ name: 'Jam Melody', instrument: 'piano', midi_program: 0 }),
  bass: Object.freeze({ name: 'Jam Bass', instrument: 'bass', midi_program: 32 }),
  harmony: Object.freeze({ name: 'Jam Harmony', instrument: 'piano', midi_program: 0 }),
  accompaniment: Object.freeze({ name: 'Jam Accompaniment', instrument: 'strings', midi_program: 48 }),
  texture: Object.freeze({ name: 'Jam Texture', instrument: 'pad', midi_program: 89 }),
});

/**
 * Default GM program for a jam role (0–127).
 * @param {string} jamRole
 * @returns {number}
 */
export function defaultMidiProgramForJamRole(jamRole) {
  const entry = DEFAULT_JAM_INSTRUMENTS[/** @type {JamTrackRole} */ (jamRole)];
  return entry?.midi_program ?? 0;
}

/**
 * @param {string} jamRole
 * @returns {string}
 */
export function mapJamRoleToV2TrackRole(jamRole) {
  const mapped = JAM_ROLE_TO_V2_TRACK_ROLE[/** @type {JamTrackRole} */ (jamRole)];
  if (mapped && SUPPORTED_TRACK_ROLES.has(mapped)) {
    return mapped;
  }
  return 'other';
}

/**
 * Create empty valid V2 tracks for missing role destinations.
 *
 * @param {object|null} composition
 * @param {Record<string, string|null|undefined>|null|undefined} roleMap
 *   Keys = jam roles; values = existing track ids (null/missing → create when ensuring).
 * @param {Record<string, { midi_program?: number, track_id?: string, name?: string, instrument?: string }>|null|undefined} instrumentSet
 * @param {{ validate?: boolean }} [options]
 * @returns {{
 *   ok: boolean,
 *   code?: string,
 *   composition: object|null,
 *   roleMap: Record<string, string>,
 *   tracksEnsured: string[],
 *   message?: string,
 * }}
 */
export function ensureJamRoleTracks(composition, roleMap, instrumentSet = {}, options = {}) {
  if (!composition || typeof composition !== 'object') {
    return {
      ok: false,
      code: 'jam_ensure_no_composition',
      composition,
      roleMap: {},
      tracksEnsured: [],
    };
  }

  const mapSrc = roleMap && typeof roleMap === 'object' && !Array.isArray(roleMap)
    ? roleMap
    : {};
  const instruments = instrumentSet && typeof instrumentSet === 'object' && !Array.isArray(instrumentSet)
    ? instrumentSet
    : {};

  /** @type {Record<string, string>} */
  const resolved = {};
  /** @type {string[]} */
  const tracksEnsured = [];
  const existingIds = new Set(
    (Array.isArray(composition.tracks) ? composition.tracks : [])
      .map((t) => (t?.id != null ? String(t.id) : ''))
      .filter(Boolean),
  );
  const usedChannels = new Set(
    (Array.isArray(composition.tracks) ? composition.tracks : [])
      .map((t) => Number(t?.channel))
      .filter((ch) => Number.isInteger(ch) && ch >= 1 && ch <= 16),
  );

  let tracks = Array.isArray(composition.tracks)
    ? composition.tracks.map((t) => ({ ...t }))
    : [];

  for (const role of Object.keys(mapSrc)) {
    if (!JAM_TRACK_ROLES.includes(/** @type {JamTrackRole} */ (role))) {
      continue;
    }
    const existingId = mapSrc[role] != null && String(mapSrc[role]).trim()
      ? String(mapSrc[role]).trim()
      : (instruments[role]?.track_id ? String(instruments[role].track_id).trim() : '');

    if (existingId && existingIds.has(existingId)) {
      resolved[role] = existingId;
      continue;
    }

    const created = createJamTrack({
      role,
      instrumentEntry: instruments[role],
      usedIds: existingIds,
      usedChannels,
    });
    tracks = [...tracks, created];
    existingIds.add(created.id);
    usedChannels.add(created.channel);
    resolved[role] = created.id;
    tracksEnsured.push(created.id);
  }

  const next = {
    ...composition,
    tracks,
    markers: Array.isArray(composition.markers) ? composition.markers : [],
  };

  if (options.validate !== false) {
    const validation = validateMusicJson(next);
    if (!validation.valid) {
      log.warn('ensureJamRoleTracks validation failed', {
        code: 'jam_ensure_validation_failed',
        message: validation.message,
        tracksEnsured: tracksEnsured.length,
      });
      return {
        ok: false,
        code: 'jam_ensure_validation_failed',
        composition,
        roleMap: {},
        tracksEnsured: [],
        message: validation.message,
      };
    }
  }

  log.info('ensureJamRoleTracks', {
    tracksEnsured: tracksEnsured.length,
    trackIdPrefixes: tracksEnsured.map((id) => String(id).slice(0, 16)),
    rolesResolved: Object.keys(resolved).length,
  });

  return {
    ok: true,
    composition: next,
    roleMap: resolved,
    tracksEnsured,
  };
}

/**
 * @param {{
 *   role: string,
 *   instrumentEntry?: { midi_program?: number, name?: string, instrument?: string },
 *   usedIds: Set<string>,
 *   usedChannels: Set<number>,
 * }} args
 */
function createJamTrack({ role, instrumentEntry, usedIds, usedChannels }) {
  const defaults = DEFAULT_JAM_INSTRUMENTS[role] || DEFAULT_JAM_INSTRUMENTS.melody;
  const programRaw = Number(instrumentEntry?.midi_program);
  const midiProgram = Number.isFinite(programRaw) && programRaw >= 0 && programRaw <= 127
    ? Math.round(programRaw)
    : defaults.midi_program;
  const name = typeof instrumentEntry?.name === 'string' && instrumentEntry.name.trim()
    ? instrumentEntry.name.trim().slice(0, 64)
    : defaults.name;
  const instrument = typeof instrumentEntry?.instrument === 'string' && instrumentEntry.instrument.trim()
    ? instrumentEntry.instrument.trim().slice(0, 64)
    : defaults.instrument;
  const v2Role = mapJamRoleToV2TrackRole(role);
  const id = uniqueTrackId(`jam-${role}`, usedIds);
  const channel = nextFreeChannel(usedChannels);

  return {
    id,
    name,
    instrument,
    role: v2Role,
    midi_program: midiProgram,
    channel,
    expression: 127,
    sustain_pedals: [],
    events: [],
  };
}

/**
 * @param {string} prefix
 * @param {Set<string>} usedIds
 */
function uniqueTrackId(prefix, usedIds) {
  const base = String(prefix || 'jam').slice(0, 24);
  let candidate = base;
  let n = 1;
  while (usedIds.has(candidate)) {
    candidate = `${base}-${n}`;
    n += 1;
    if (n > 10_000) {
      candidate = `${base}-${Date.now().toString(36)}`;
      break;
    }
  }
  return candidate;
}

/**
 * @param {Set<number>} usedChannels
 */
function nextFreeChannel(usedChannels) {
  for (let ch = 1; ch <= 16; ch += 1) {
    if (!usedChannels.has(ch)) return ch;
  }
  // All channels taken — reuse channel 1 (still valid V2).
  return 1;
}
