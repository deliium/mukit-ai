/**
 * Pure track runtime helpers for Composition V1 playback routing.
 * Derives mute/solo/volume state and Tone-compatible instrument strategies.
 */

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

  console.debug('[playbackTracks] Track playback state created', {
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
  const anySolo = trackStates.some((track) => track.solo);
  return trackStates.map((track) => {
    const audible = isTrackAudible(track, trackStates);
    const effectiveGain = audible ? track.gain : 0;
    console.debug('[playbackTracks] Effective track gain resolved', {
      trackId: track.trackId,
      audible,
      anySolo,
      muted: track.muted,
      solo: track.solo,
      volumeMidi: track.volumeMidi,
      effectiveGain,
      strategy: track.strategy?.id,
    });
    return {
      ...track,
      audible,
      effectiveGain,
    };
  });
}

export function selectInstrumentStrategy(track) {
  const instrument = String(track?.instrument || '').toLowerCase();
  const role = String(track?.role || '').toLowerCase();
  const hasProgram = track?.midi_program !== undefined && track?.midi_program !== null && track?.midi_program !== '';
  const program = hasProgram ? clampMidi(track.midi_program, 0, 127, 0) : null;
  const isDrum = Boolean(track?.is_drum)
    || instrument.includes('drum')
    || role.includes('drum')
    || instrument.includes('perc')
    || Number(track?.channel) === 10;

  if (isDrum) {
    return strategyResult('drums', {
      synth: 'MembraneSynth',
      options: { octave: 2 },
      fallback: !isKnownDrum(instrument, role, track),
      reason: 'drums/percussion fallback',
    }, track);
  }

  if (isBass(instrument, role, program)) {
    return strategyResult('bass', {
      synth: 'MonoSynth',
      options: {
        oscillator: { type: 'square' },
        envelope: { attack: 0.01, decay: 0.2, sustain: 0.4, release: 0.3 },
        filterEnvelope: { attack: 0.01, decay: 0.1, sustain: 0.3, release: 0.2, baseFrequency: 120, octaves: 2.5 },
      },
      fallback: false,
      reason: 'bass mapping',
    }, track);
  }

  // Piano/keyboard before role-based pad/harmony so piano harmony tracks stay pianistic.
  if (isPianoOrKeyboard(instrument, role, program)) {
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
      fallback: false,
      reason: 'piano/keyboard mapping',
    }, track);
  }

  if (isStringsOrPad(instrument, role, program)) {
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
      fallback: false,
      reason: 'strings/pad mapping',
    }, track);
  }

  if (isGuitarLike(instrument, role, program)) {
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
      fallback: !isKnownGuitar(instrument, role, program),
      reason: 'guitar-like plucked/synth fallback',
    }, track);
  }

  if (isLeadOrSynth(instrument, role, program)) {
    return strategyResult('lead_synth', {
      synth: 'MonoSynth',
      options: {
        oscillator: { type: 'sawtooth' },
        envelope: { attack: 0.02, decay: 0.15, sustain: 0.5, release: 0.25 },
        filterEnvelope: { attack: 0.02, decay: 0.2, sustain: 0.3, release: 0.2, baseFrequency: 400, octaves: 3 },
      },
      fallback: false,
      reason: 'lead/synth mapping',
    }, track);
  }

  const fallback = strategyResult('piano_keyboard', {
    synth: 'PolySynth',
    voice: 'Synth',
    options: {
      maxPolyphony: 8,
      voice: {
        oscillator: { type: 'triangle' },
        envelope: { attack: 0.01, decay: 0.2, sustain: 0.3, release: 0.4 },
      },
    },
    fallback: true,
    reason: 'unsupported instrument/program fallback to piano-like synth',
  }, track);

  console.warn('[playbackTracks] Unsupported instrument/program; using fallback strategy', {
    trackId: track?.id,
    instrument: track?.instrument,
    role: track?.role,
    midiProgram: track?.midi_program,
    fallbackStrategy: fallback.id,
  });
  return fallback;
}

function strategyResult(id, details, track) {
  const result = {
    id,
    ...details,
  };
  console.debug('[playbackTracks] Selected instrument strategy', {
    trackId: track?.id,
    instrument: track?.instrument,
    role: track?.role,
    midiProgram: track?.midi_program,
    strategy: result.id,
    fallback: Boolean(result.fallback),
    reason: result.reason,
  });
  return result;
}

function isPianoOrKeyboard(instrument, role, program) {
  if (/(piano|keyboard|epiano|electric.?piano|clav|harpsi)/.test(instrument) || role.includes('piano')) {
    return true;
  }
  if (instrument) {
    return false;
  }
  return program !== null && program >= 0 && program <= 7;
}

function isBass(instrument, role, program) {
  if (instrument.includes('bass') || role.includes('bass')) {
    return true;
  }
  if (instrument) {
    return false;
  }
  return program !== null && program >= 32 && program <= 39;
}

function isStringsOrPad(instrument, role, program) {
  if (/(string|pad|choir|ensemble|violin|viola|cello|orchestr)/.test(instrument) || /(pad|string)/.test(role)) {
    return true;
  }
  if (instrument) {
    return false;
  }
  return program !== null && ((program >= 40 && program <= 55) || (program >= 88 && program <= 95));
}

function isGuitarLike(instrument, role, program) {
  if (/(guitar|pluck|banjo|mandolin)/.test(instrument) || role.includes('guitar')) {
    return true;
  }
  if (instrument) {
    return false;
  }
  return program !== null && program >= 24 && program <= 31;
}

function isKnownGuitar(instrument, role, program) {
  return /(guitar|pluck|banjo|mandolin)/.test(instrument) || role.includes('guitar') || (program !== null && program >= 24 && program <= 31);
}

function isLeadOrSynth(instrument, role, program) {
  if (/(lead|synth|flute|oboe|clarinet|sax|trumpet|brass|woodwind)/.test(instrument) || /(lead|melody)/.test(role)) {
    return true;
  }
  if (instrument) {
    return false;
  }
  return program !== null && ((program >= 56 && program <= 87) || (program >= 96 && program <= 103));
}

function isKnownDrum(instrument, role, track) {
  return Boolean(
    track?.is_drum
    || instrument.includes('drum')
    || instrument.includes('perc')
    || role.includes('drum')
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
