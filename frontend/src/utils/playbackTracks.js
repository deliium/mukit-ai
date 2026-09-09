/**
 * Pure track runtime helpers for Composition V2 playback routing.
 * Derives mute/solo/volume state and Tone-compatible instrument strategies.
 *
 * Strategy priority: instrument name → midi_program → role (fallback only).
 */

import { createAppLogger } from './appLogger.js';

const logger = createAppLogger('playbackTracks');

const DEFAULT_TRACK_VOLUME = 100;

export function midiVolumeToGain(volumeMidi = DEFAULT_TRACK_VOLUME) {
  const clamped = clampMidi(volumeMidi, 0, 127, DEFAULT_TRACK_VOLUME);
  return clamped / 127;
}

export function midiPanToStereo(pan = 0) {
  const clamped = clampMidi(pan, -64, 63, 0);
  return clamped / 64;
}

export function createTrackPlaybackState(track, overrides = {}) {
  const trackId = String(track?.id ?? '');
  const volumeMidi = overrides.volumeMidi !== undefined
    ? clampMidi(overrides.volumeMidi, 0, 127, DEFAULT_TRACK_VOLUME)
    : clampMidi(track?.volume, 0, 127, DEFAULT_TRACK_VOLUME);
  const muted = Boolean(overrides.muted);
  const solo = Boolean(overrides.solo);
  const strategy = selectInstrumentStrategy(track);

  const state = {
    trackId,
    trackName: typeof track?.name === 'string' && track.name.trim() ? track.name.trim() : trackId,
    instrument: typeof track?.instrument === 'string' ? track.instrument : '',
    role: typeof track?.role === 'string' ? track.role : '',
    midiProgram: clampMidi(track?.midi_program, 0, 127, 0),
    channel: clampMidi(track?.channel, 1, 16, 1),
    isDrum: Boolean(track?.is_drum),
    volumeMidi,
    gain: midiVolumeToGain(volumeMidi),
    pan: clampMidi(track?.pan, -64, 63, 0),
    panStereo: midiPanToStereo(track?.pan),
    muted,
    solo,
    strategy,
  };

  logger.debug('Track playback state created', {
    trackId: state.trackId,
    strategy: strategy.id,
    volumeMidi: state.volumeMidi,
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

export function resolveEffectiveTrackGains(trackStates) {
  return trackStates.map((track) => {
    const audible = isTrackAudible(track, trackStates);
    const effectiveGain = audible ? track.gain : 0;
    return {
      ...track,
      audible,
      effectiveGain,
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
    if ((program >= 56 && program <= 87) || (program >= 96 && program <= 103)) {
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

function isLeadInstrument(instrument) {
  return /(lead|synth|flute|oboe|clarinet|sax|trumpet|brass|woodwind)/.test(instrument);
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
