/**
 * Pure track runtime helpers for Composition V2 playback routing.
 * Derives mute/solo/session override state and Tone-compatible instrument strategies.
 *
 * Strategy priority: instrument name → midi_program → role (fallback only).
 *
 * Session mixer controls are neutral browser overrides (trimDb / panOffset / mute /
 * solo / reverbSend). Canonical track.volume, track.pan, and expression remain
 * authoritative on the composition route — session trim defaults to 0 dB so
 * canonical volume is not squared by a second volume fader.
 */

import { createAppLogger } from './appLogger.js';

const logger = createAppLogger('playbackTracks');

const DEFAULT_TRACK_VOLUME = 100;

/** Bounded session trim range in dB (neutral = 0). */
export const SESSION_TRIM_DB_MIN = -24;
export const SESSION_TRIM_DB_MAX = 24;

/** Bounded reverb send (0 = dry, 1 = full send). */
export const SESSION_REVERB_SEND_MIN = 0;
export const SESSION_REVERB_SEND_MAX = 1;

/** Pan offset in stereo units (-1..1), added to canonical pan. */
export const SESSION_PAN_OFFSET_MIN = -1;
export const SESSION_PAN_OFFSET_MAX = 1;

export const DEFAULT_SESSION_TRACK_CONTROLS = Object.freeze({
  trimDb: 0,
  panOffset: 0,
  muted: false,
  solo: false,
  reverbSend: 0,
});

export function midiVolumeToGain(volumeMidi = DEFAULT_TRACK_VOLUME) {
  const clamped = clampMidi(volumeMidi, 0, 127, DEFAULT_TRACK_VOLUME);
  return clamped / 127;
}

export function midiPanToStereo(pan = 0) {
  const clamped = clampMidi(pan, -64, 63, 0);
  return clamped / 64;
}

export function trimDbToGain(trimDb = 0) {
  const db = clampNumber(trimDb, SESSION_TRIM_DB_MIN, SESSION_TRIM_DB_MAX, 0);
  return 10 ** (db / 20);
}

/**
 * Normalize ephemeral session mixer overrides. Does not mutate composition fields.
 * Legacy `volumeMidi` overrides are converted to trim relative to canonical volume
 * so a default fader at track.volume yields trimDb 0 (no double gain).
 *
 * @param {object} [overrides]
 * @param {{ volumeMidi?: number }} [canonical]
 * @returns {{
 *   trimDb: number,
 *   panOffset: number,
 *   muted: boolean,
 *   solo: boolean,
 *   reverbSend: number,
 * }}
 */
export function normalizeSessionTrackControls(overrides = {}, canonical = {}) {
  const muted = Boolean(overrides?.muted);
  const solo = Boolean(overrides?.solo);
  const panOffset = clampNumber(
    overrides?.panOffset,
    SESSION_PAN_OFFSET_MIN,
    SESSION_PAN_OFFSET_MAX,
    0,
  );
  const reverbSend = clampNumber(
    overrides?.reverbSend,
    SESSION_REVERB_SEND_MIN,
    SESSION_REVERB_SEND_MAX,
    0,
  );

  let trimDb;
  if (overrides?.trimDb !== undefined && overrides?.trimDb !== null) {
    trimDb = clampNumber(overrides.trimDb, SESSION_TRIM_DB_MIN, SESSION_TRIM_DB_MAX, 0);
  } else if (overrides?.volumeMidi !== undefined && overrides?.volumeMidi !== null) {
    trimDb = legacyVolumeMidiToTrimDb(overrides.volumeMidi, canonical.volumeMidi);
  } else {
    trimDb = 0;
  }

  return {
    trimDb,
    panOffset,
    muted,
    solo,
    reverbSend,
  };
}

/**
 * @param {number|string} volumeMidi
 * @param {number} [canonicalVolumeMidi]
 * @returns {number}
 */
export function legacyVolumeMidiToTrimDb(volumeMidi, canonicalVolumeMidi = DEFAULT_TRACK_VOLUME) {
  const canonical = clampMidi(canonicalVolumeMidi, 0, 127, DEFAULT_TRACK_VOLUME);
  const ui = clampMidi(volumeMidi, 0, 127, canonical);
  const canonicalGain = Math.max(midiVolumeToGain(canonical), 1e-6);
  const uiGain = midiVolumeToGain(ui);
  const ratio = Math.max(uiGain / canonicalGain, 1e-6);
  return clampNumber(20 * Math.log10(ratio), SESSION_TRIM_DB_MIN, SESSION_TRIM_DB_MAX, 0);
}

export function createTrackPlaybackState(track, overrides = {}) {
  const trackId = String(track?.id ?? '');
  const canonicalVolumeMidi = clampMidi(track?.volume, 0, 127, DEFAULT_TRACK_VOLUME);
  const canonicalPan = clampMidi(track?.pan, -64, 63, 0);
  const session = normalizeSessionTrackControls(overrides, { volumeMidi: canonicalVolumeMidi });
  const strategy = selectInstrumentStrategy(track);
  const canonicalGain = midiVolumeToGain(canonicalVolumeMidi);
  const sessionGain = trimDbToGain(session.trimDb);
  const panStereo = clampNumber(
    midiPanToStereo(canonicalPan) + session.panOffset,
    SESSION_PAN_OFFSET_MIN,
    SESSION_PAN_OFFSET_MAX,
    0,
  );

  const state = {
    trackId,
    trackName: typeof track?.name === 'string' && track.name.trim() ? track.name.trim() : trackId,
    instrument: typeof track?.instrument === 'string' ? track.instrument : '',
    role: typeof track?.role === 'string' ? track.role : '',
    midiProgram: clampMidi(track?.midi_program, 0, 127, 0),
    channel: clampMidi(track?.channel, 1, 16, 1),
    isDrum: Boolean(track?.is_drum),
    // Canonical authority (composition fields)
    volumeMidi: canonicalVolumeMidi,
    gain: canonicalGain,
    pan: canonicalPan,
    panStereo,
    // Session overrides (ephemeral)
    session,
    trimDb: session.trimDb,
    panOffset: session.panOffset,
    reverbSend: session.reverbSend,
    sessionGain,
    muted: session.muted,
    solo: session.solo,
    strategy,
  };

  logger.debug('Track playback state created', {
    trackId: state.trackId,
    strategy: strategy.id,
    volumeMidi: state.volumeMidi,
    trimDb: state.trimDb,
    muted: state.muted,
    solo: state.solo,
    fallback: strategy.fallback,
  });

  return state;
}

export function buildTrackPlaybackStates(tracks, overridesByTrackId = {}) {
  if (!Array.isArray(tracks)) {
    return [];
  }
  return tracks.map((track) => {
    const trackId = String(track?.id ?? '');
    return createTrackPlaybackState(track, overridesByTrackId[trackId] || {});
  });
}

export function isTrackAudible(trackState, allTrackStates) {
  if (!trackState) {
    return false;
  }
  const anySolo = Array.isArray(allTrackStates) && allTrackStates.some((track) => track.solo);
  if (anySolo) {
    return Boolean(trackState.solo) && !trackState.muted;
  }
  return !trackState.muted;
}

/**
 * Effective UI/session gain only (mute/solo/trim). Canonical volume stays on the
 * composition volume node so the two are not multiplied twice.
 */
export function resolveEffectiveTrackGains(trackStates) {
  return trackStates.map((track) => {
    const audible = isTrackAudible(track, trackStates);
    const sessionGain = Number.isFinite(Number(track.sessionGain))
      ? Number(track.sessionGain)
      : trimDbToGain(track.trimDb ?? 0);
    const effectiveGain = audible ? sessionGain : 0;
    return {
      ...track,
      audible,
      sessionGain,
      effectiveGain,
      /** Full route gain if a single node applies both (tests / diagnostics). */
      combinedGain: audible ? track.gain * sessionGain : 0,
    };
  });
}

/**
 * Prefer canonical instrument / midi_program identity; role is fallback only.
 */
export function selectInstrumentStrategy(track) {
  const instrument = String(track?.instrument || '').toLowerCase();
  const role = String(track?.role || '').toLowerCase();
  const hasProgram = track?.midi_program !== undefined
    && track?.midi_program !== null
    && track?.midi_program !== '';
  const program = hasProgram ? clampMidi(track.midi_program, 0, 127, 0) : null;
  const drumByIdentity = Boolean(track?.is_drum)
    || instrument.includes('drum')
    || instrument.includes('perc')
    || Number(track?.channel) === 10;

  if (drumByIdentity) {
    return strategyResult('drums', {
      synth: 'MembraneSynth',
      options: { octave: 2 },
      fallback: !isKnownDrumIdentity(instrument, track),
      reason: 'drums/percussion identity',
      presetId: 'drums_basic',
    }, track);
  }

  // 1) Instrument name identity
  if (instrument) {
    if (isBassInstrument(instrument)) {
      return bassStrategy(track, false, 'bass instrument mapping');
    }
    if (isPianoInstrument(instrument)) {
      return pianoStrategy(track, false, 'piano/keyboard instrument mapping');
    }
    if (isStringsInstrument(instrument)) {
      return stringsStrategy(track, false, 'strings/pad instrument mapping');
    }
    if (isGuitarInstrument(instrument)) {
      return guitarStrategy(track, !isKnownGuitarInstrument(instrument), 'guitar instrument mapping');
    }
    if (isBrassInstrument(instrument)) {
      return brassStrategy(track, false, 'brass instrument mapping');
    }
    if (isMalletInstrument(instrument)) {
      return malletStrategy(track, false, 'mallet instrument mapping');
    }
    if (isLeadInstrument(instrument)) {
      return leadStrategy(track, false, 'lead/synth instrument mapping');
    }
  }

  // 2) GM program identity
  if (program !== null) {
    if (program >= 32 && program <= 39) {
      return bassStrategy(track, false, 'bass program mapping');
    }
    if (program >= 0 && program <= 7) {
      return pianoStrategy(track, false, 'piano/keyboard program mapping');
    }
    if ((program >= 40 && program <= 55) || (program >= 88 && program <= 95)) {
      return stringsStrategy(track, false, 'strings/pad program mapping');
    }
    if (program >= 24 && program <= 31) {
      return guitarStrategy(track, false, 'guitar program mapping');
    }
    if (program >= 56 && program <= 63) {
      return brassStrategy(track, false, 'brass program mapping');
    }
    if (program >= 8 && program <= 15) {
      return malletStrategy(track, false, 'mallet program mapping');
    }
    if ((program >= 64 && program <= 87) || (program >= 96 && program <= 103)) {
      return leadStrategy(track, false, 'lead/synth program mapping');
    }
  }

  // 3) Role only as fallback when instrument/program did not decide
  if (role.includes('drum') || role.includes('perc')) {
    return strategyResult('drums', {
      synth: 'MembraneSynth',
      options: { octave: 2 },
      fallback: true,
      reason: 'drums role fallback',
      presetId: 'drums_basic',
    }, track);
  }
  if (role.includes('bass')) {
    return bassStrategy(track, true, 'bass role fallback');
  }
  if (role.includes('piano') || role.includes('keyboard')) {
    return pianoStrategy(track, true, 'piano role fallback');
  }
  if (/(pad|string)/.test(role)) {
    return stringsStrategy(track, true, 'strings/pad role fallback');
  }
  if (role.includes('guitar')) {
    return guitarStrategy(track, true, 'guitar role fallback');
  }
  if (/(brass|horn)/.test(role)) {
    return brassStrategy(track, true, 'brass role fallback');
  }
  if (/(lead|melody)/.test(role)) {
    return leadStrategy(track, true, 'lead/melody role fallback');
  }

  const fallback = pianoStrategy(
    track,
    true,
    'unsupported instrument/program fallback to piano-like synth',
  );
  logger.warn('Unsupported instrument/program; using fallback strategy', {
    trackId: track?.id,
    strategy: fallback.id,
    fallback: true,
    hasInstrument: Boolean(instrument),
    hasProgram: program !== null,
    hasRole: Boolean(role),
  });
  return fallback;
}

function bassStrategy(track, fallback, reason) {
  return strategyResult('bass', {
    synth: 'MonoSynth',
    options: {
      oscillator: { type: 'square' },
      envelope: { attack: 0.01, decay: 0.2, sustain: 0.4, release: 0.3 },
      filterEnvelope: {
        attack: 0.01,
        decay: 0.1,
        sustain: 0.3,
        release: 0.2,
        baseFrequency: 120,
        octaves: 2.5,
      },
    },
    fallback,
    reason,
    presetId: 'bass_synth',
  }, track);
}

function pianoStrategy(track, fallback, reason) {
  return strategyResult('piano_keyboard', {
    synth: 'PolySynth',
    voice: 'Synth',
    options: {
      maxPolyphony: 12,
      voice: {
        oscillator: { type: 'triangle' },
        envelope: { attack: 0.005, decay: 0.3, sustain: 0.2, release: 0.6 },
      },
    },
    fallback,
    reason,
    presetId: 'piano_keyboard',
  }, track);
}

function stringsStrategy(track, fallback, reason) {
  return strategyResult('strings_pad', {
    synth: 'PolySynth',
    voice: 'Synth',
    options: {
      maxPolyphony: 8,
      voice: {
        oscillator: { type: 'triangle' },
        envelope: { attack: 0.08, decay: 0.2, sustain: 0.7, release: 0.8 },
      },
    },
    fallback,
    reason,
    presetId: 'strings_pad',
  }, track);
}

function guitarStrategy(track, fallback, reason) {
  return strategyResult('guitar_pluck', {
    synth: 'PolySynth',
    voice: 'Synth',
    options: {
      maxPolyphony: 6,
      voice: {
        oscillator: { type: 'sawtooth' },
        envelope: { attack: 0.005, decay: 0.25, sustain: 0.15, release: 0.35 },
      },
    },
    fallback,
    reason,
    presetId: 'guitar_pluck',
  }, track);
}

function leadStrategy(track, fallback, reason) {
  return strategyResult('lead_synth', {
    synth: 'MonoSynth',
    options: {
      oscillator: { type: 'sawtooth' },
      envelope: { attack: 0.02, decay: 0.15, sustain: 0.5, release: 0.25 },
      filterEnvelope: {
        attack: 0.02,
        decay: 0.2,
        sustain: 0.3,
        release: 0.2,
        baseFrequency: 400,
        octaves: 3,
      },
    },
    fallback,
    reason,
    presetId: 'woodwind_lead',
  }, track);
}

function brassStrategy(track, fallback, reason) {
  return strategyResult('brass', {
    synth: 'MonoSynth',
    options: {
      oscillator: { type: 'sawtooth' },
      envelope: { attack: 0.04, decay: 0.18, sustain: 0.55, release: 0.3 },
      filterEnvelope: {
        attack: 0.03,
        decay: 0.15,
        sustain: 0.4,
        release: 0.25,
        baseFrequency: 350,
        octaves: 2.5,
      },
    },
    fallback,
    reason,
    presetId: 'brass',
  }, track);
}

function malletStrategy(track, fallback, reason) {
  return strategyResult('mallet', {
    synth: 'PolySynth',
    voice: 'Synth',
    options: {
      maxPolyphony: 8,
      voice: {
        oscillator: { type: 'sine' },
        envelope: { attack: 0.002, decay: 0.35, sustain: 0.05, release: 0.45 },
      },
    },
    fallback,
    reason,
    presetId: 'mallet',
  }, track);
}

function strategyResult(id, details, track) {
  const result = {
    id,
    ...details,
  };
  logger.debug('Selected instrument strategy', {
    trackId: track?.id,
    strategy: result.id,
    presetId: result.presetId,
    fallback: Boolean(result.fallback),
    reason: result.reason,
    hasInstrument: Boolean(track?.instrument),
    hasProgram: track?.midi_program !== undefined && track?.midi_program !== null,
    hasRole: Boolean(track?.role),
  });
  return result;
}

function isPianoInstrument(instrument) {
  return /(piano|keyboard|epiano|electric.?piano|clav|harpsi)/.test(instrument);
}

function isBassInstrument(instrument) {
  return instrument.includes('bass') && !instrument.includes('bassoon');
}

function isStringsInstrument(instrument) {
  return /(string|pad|choir|ensemble|violin|viola|cello|orchestr)/.test(instrument);
}

function isGuitarInstrument(instrument) {
  return /(guitar|pluck|banjo|mandolin)/.test(instrument);
}

function isKnownGuitarInstrument(instrument) {
  return /(guitar|pluck|banjo|mandolin)/.test(instrument);
}

function isBrassInstrument(instrument) {
  return /(brass|trumpet|trombone|tuba|horn|cornet)/.test(instrument);
}

function isMalletInstrument(instrument) {
  return /(mallet|marimba|vibraphone|xylophone|glock|celesta|bell)/.test(instrument);
}

function isLeadInstrument(instrument) {
  return /(lead|synth|flute|oboe|clarinet|sax|woodwind)/.test(instrument);
}

function isKnownDrumIdentity(instrument, track) {
  return Boolean(
    track?.is_drum
    || instrument.includes('drum')
    || instrument.includes('perc')
    || Number(track?.channel) === 10,
  );
}

function clampMidi(value, min, max, fallback) {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return fallback;
  }
  return Math.max(min, Math.min(max, number));
}

function clampNumber(value, min, max, fallback) {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return fallback;
  }
  return Math.max(min, Math.min(max, number));
}
