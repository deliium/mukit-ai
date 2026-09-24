/**
 * Pure helper: ensure destination tracks exist for audio-recovery Apply.
 *
 * Creates valid composition.v2 tracks when opted in — never invents note events.
 * Product stems map onto supported V2 track.role. Do NOT import jam modules.
 *
 * Logging: createAppLogger('audioRecovery') — counts / id prefixes only.
 */

import { createAppLogger } from './appLogger.js';
import { SUPPORTED_TRACK_ROLES, validateMusicJson } from './musicJsonValidation.js';

const log = createAppLogger('audioRecovery');

/** @typedef {'vocals'|'melody'|'bass'|'drums'|'harmonic'|'other'} RecoveryStemRole */

export const RECOVERY_STEM_ROLES = Object.freeze([
  'vocals',
  'melody',
  'bass',
  'drums',
  'harmonic',
  'other',
]);

/**
 * Product stem → canonical V2 track.role.
 * @type {Readonly<Record<RecoveryStemRole, string>>}
 */
export const RECOVERY_STEM_TO_V2_TRACK_ROLE = Object.freeze({
  vocals: 'lead',
  melody: 'melody',
  bass: 'bass',
  drums: 'drums',
  harmonic: 'harmony',
  other: 'other',
});

/** Default GM program / display names when instrument_set omits them. */
export const DEFAULT_RECOVERY_INSTRUMENTS = Object.freeze({
  vocals: Object.freeze({ name: 'Recovery Vocals', instrument: 'voice', midi_program: 52 }),
  melody: Object.freeze({ name: 'Recovery Melody', instrument: 'piano', midi_program: 0 }),
  bass: Object.freeze({ name: 'Recovery Bass', instrument: 'bass', midi_program: 32 }),
  drums: Object.freeze({ name: 'Recovery Drums', instrument: 'drums', midi_program: 0 }),
  harmonic: Object.freeze({ name: 'Recovery Harmony', instrument: 'piano', midi_program: 0 }),
  other: Object.freeze({ name: 'Recovery Other', instrument: 'synth', midi_program: 80 }),
});

/**
 * @param {string} stem
 * @returns {string}
 */
export function mapRecoveryStemToV2TrackRole(stem) {
  const mapped = RECOVERY_STEM_TO_V2_TRACK_ROLE[/** @type {RecoveryStemRole} */ (stem)];
  if (mapped && SUPPORTED_TRACK_ROLES.has(mapped)) {
    return mapped;
  }
  return 'other';
}

/**
 * Create empty valid V2 tracks for missing stem destinations.
 *
 * @param {object|null} composition
 * @param {Record<string, string|null|undefined>|null|undefined} stemRoleMap
 *   Keys = product stems; values = existing track ids (null/missing → create when ensuring).
 * @param {Record<string, { midi_program?: number, track_id?: string, name?: string, instrument?: string }>|null|undefined} instrumentSet
 * @param {{ validate?: boolean, ensure_missing_tracks?: boolean }} [options]
 * @returns {{
 *   ok: boolean,
 *   code?: string,
 *   composition: object|null,
 *   stemRoleMap: Record<string, string>,
 *   tracksEnsured: string[],
 *   message?: string,
 * }}
 */
export function ensureRecoveryRoleTracks(
  composition,
  stemRoleMap,
  instrumentSet = {},
  options = {},
) {
  if (!composition || typeof composition !== 'object') {
    return {
      ok: false,
      code: 'audio_recovery_ensure_no_composition',
      composition,
      stemRoleMap: {},
      tracksEnsured: [],
    };
  }

  const ensureMissing = options.ensure_missing_tracks !== false;
  const mapSrc = stemRoleMap && typeof stemRoleMap === 'object' && !Array.isArray(stemRoleMap)
    ? stemRoleMap
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

  for (const stem of Object.keys(mapSrc)) {
    if (!RECOVERY_STEM_ROLES.includes(/** @type {RecoveryStemRole} */ (stem))) {
      continue;
    }
    const existingId = mapSrc[stem] != null && String(mapSrc[stem]).trim()
      ? String(mapSrc[stem]).trim()
      : (instruments[stem]?.track_id ? String(instruments[stem].track_id).trim() : '');

    if (existingId && existingIds.has(existingId)) {
      resolved[stem] = existingId;
      continue;
    }

    if (!ensureMissing) {
      continue;
    }

    const created = createRecoveryTrack({
      stem,
      instrumentEntry: instruments[stem],
      usedIds: existingIds,
      usedChannels,
    });
    tracks = [...tracks, created];
    existingIds.add(created.id);
    usedChannels.add(created.channel);
    resolved[stem] = created.id;
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
      log.warn('ensureRecoveryRoleTracks validation failed', {
        code: 'audio_recovery_ensure_validation_failed',
        message: validation.message,
        tracksEnsured: tracksEnsured.length,
      });
      return {
        ok: false,
        code: 'audio_recovery_ensure_validation_failed',
        composition,
        stemRoleMap: {},
        tracksEnsured: [],
        message: validation.message,
      };
    }
  }

  log.info('ensureRecoveryRoleTracks', {
    tracksEnsured: tracksEnsured.length,
    trackIdPrefixes: tracksEnsured.map((id) => String(id).slice(0, 16)),
    stemsResolved: Object.keys(resolved).length,
  });

  return {
    ok: true,
    composition: next,
    stemRoleMap: resolved,
    tracksEnsured,
  };
}

/**
 * @param {{
 *   stem: string,
 *   instrumentEntry?: { midi_program?: number, name?: string, instrument?: string },
 *   usedIds: Set<string>,
 *   usedChannels: Set<number>,
 * }} args
 */
function createRecoveryTrack({ stem, instrumentEntry, usedIds, usedChannels }) {
  const defaults = DEFAULT_RECOVERY_INSTRUMENTS[stem] || DEFAULT_RECOVERY_INSTRUMENTS.melody;
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
  const v2Role = mapRecoveryStemToV2TrackRole(stem);
  const id = uniqueTrackId(`rec-${stem}`, usedIds);
  const channel = nextFreeChannel(usedChannels, { prefer: stem === 'drums' ? 10 : null });

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
  const base = String(prefix || 'rec').slice(0, 24);
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
 * @param {{ prefer?: number|null }} [opts]
 */
function nextFreeChannel(usedChannels, opts = {}) {
  const prefer = opts.prefer;
  if (prefer != null && !usedChannels.has(prefer)) {
    return prefer;
  }
  for (let ch = 1; ch <= 16; ch += 1) {
    if (!usedChannels.has(ch)) return ch;
  }
  return 1;
}
