import assert from 'node:assert/strict';
import test from 'node:test';

import { setAppLogLevelForTests } from './appLogger.js';
import {
  buildTrackPlaybackStates,
  isTrackAudible,
  midiVolumeToGain,
  resolveEffectiveTrackGains,
  selectInstrumentStrategy,
} from './playbackTracks.js';

test('maps track volume and pan scalars', () => {
  assert.equal(midiVolumeToGain(127), 1);
  assert.equal(midiVolumeToGain(0), 0);
  assert.equal(midiVolumeToGain(64), 64 / 127);
});

test('selects instrument strategies including unsupported fallback', () => {
  assert.equal(selectInstrumentStrategy({ instrument: 'piano', midi_program: 0 }).id, 'piano_keyboard');
  assert.equal(selectInstrumentStrategy({ instrument: 'electric_bass', role: 'bass', midi_program: 33 }).id, 'bass');
  assert.equal(selectInstrumentStrategy({ instrument: 'strings', role: 'pad', midi_program: 48 }).id, 'strings_pad');
  assert.equal(selectInstrumentStrategy({ instrument: 'flute', role: 'melody', midi_program: 73 }).id, 'lead_synth');
  assert.equal(selectInstrumentStrategy({ instrument: 'acoustic_guitar', midi_program: 25 }).id, 'guitar_pluck');
  assert.equal(selectInstrumentStrategy({ instrument: 'drums', is_drum: true }).id, 'drums');

  const fallback = selectInstrumentStrategy({ instrument: 'theremin-of-destiny', midi_program: 120, role: 'fx' });
  assert.equal(fallback.id, 'piano_keyboard');
  assert.equal(fallback.fallback, true);
});

test('keeps piano harmony tracks on piano strategy', () => {
  const strategy = selectInstrumentStrategy({
    instrument: 'piano',
    role: 'harmony',
    midi_program: 0,
  });
  assert.equal(strategy.id, 'piano_keyboard');
});

test('prefers instrument identity over conflicting role', () => {
  const celloBassRole = selectInstrumentStrategy({
    instrument: 'cello',
    role: 'bass',
    midi_program: 42,
  });
  assert.equal(celloBassRole.id, 'strings_pad');
  assert.equal(celloBassRole.fallback, false);

  const pianoMelody = selectInstrumentStrategy({
    instrument: 'piano',
    role: 'melody',
    midi_program: 0,
  });
  assert.equal(pianoMelody.id, 'piano_keyboard');
});

test('uses midi_program over role when instrument is empty', () => {
  const byProgram = selectInstrumentStrategy({
    instrument: '',
    role: 'bass',
    midi_program: 48,
  });
  assert.equal(byProgram.id, 'strings_pad');
  assert.equal(byProgram.fallback, false);
});

test('uses role only as fallback when instrument and program are absent', () => {
  const roleFallback = selectInstrumentStrategy({
    instrument: '',
    role: 'bass',
  });
  assert.equal(roleFallback.id, 'bass');
  assert.equal(roleFallback.fallback, true);
});

test('resolves mute and solo effective audible state', () => {
  const states = buildTrackPlaybackStates([
    { id: 'a', instrument: 'piano', volume: 100 },
    { id: 'b', instrument: 'bass', volume: 80 },
    { id: 'c', instrument: 'strings', volume: 60 },
  ], {
    a: { muted: true },
    b: { solo: true },
    c: { solo: false, muted: false },
  });

  assert.equal(isTrackAudible(states[0], states), false);
  assert.equal(isTrackAudible(states[1], states), true);
  assert.equal(isTrackAudible(states[2], states), false);

  const effective = resolveEffectiveTrackGains(states);
  assert.equal(effective[0].effectiveGain, 0);
  assert.equal(effective[1].effectiveGain, 80 / 127);
  assert.equal(effective[2].effectiveGain, 0);
});

test('mute alone silences a track when nothing is soloed', () => {
  const states = buildTrackPlaybackStates([
    { id: 'a', instrument: 'piano', volume: 127 },
    { id: 'b', instrument: 'bass', volume: 127 },
  ], {
    a: { muted: true },
  });
  const effective = resolveEffectiveTrackGains(states);
  assert.equal(effective[0].audible, false);
  assert.equal(effective[1].audible, true);
  assert.equal(effective[1].effectiveGain, 1);
});

test('mute/solo effective gain stays independent from persisted track volume field', () => {
  const states = buildTrackPlaybackStates([
    { id: 'a', instrument: 'piano', volume: 64 },
    { id: 'b', instrument: 'bass', volume: 100 },
  ], {
    a: { muted: false, volumeMidi: 127 },
    b: { muted: false, volumeMidi: 127 },
  });

  const effective = resolveEffectiveTrackGains(states);
  assert.equal(effective[0].volumeMidi, 127);
  assert.notEqual(effective[0].volumeMidi, 64);
  assert.equal(effective[0].effectiveGain, 1);
});

test('playback diagnostics use controlled appLogger levels', () => {
  const debugLogs = [];
  const previousDebug = console.debug;
  console.debug = (...args) => {
    debugLogs.push(args);
  };
  try {
    setAppLogLevelForTests('silent');
    selectInstrumentStrategy({ instrument: 'piano', midi_program: 0, id: 't1' });
    assert.equal(debugLogs.length, 0);

    setAppLogLevelForTests('debug');
    selectInstrumentStrategy({ instrument: 'piano', midi_program: 0, id: 't2' });
    assert.ok(debugLogs.length >= 1);
    const joined = JSON.stringify(debugLogs);
    assert.equal(joined.includes('C4'), false);
    assert.equal(joined.includes('events'), false);
  } finally {
    console.debug = previousDebug;
    setAppLogLevelForTests(null);
  }
});

test('arrangement catalog GM programs map by instrument identity not role', () => {
  assert.equal(
    selectInstrumentStrategy({ instrument: 'cello', role: 'melody', midi_program: 42 }).id,
    'strings_pad',
  );
  assert.equal(
    selectInstrumentStrategy({ instrument: 'cello', role: 'bass', midi_program: 42 }).id,
    'strings_pad',
  );
  assert.equal(
    selectInstrumentStrategy({ instrument: 'string_ensemble_1', role: 'harmony', midi_program: 48 }).id,
    'strings_pad',
  );
  assert.equal(
    selectInstrumentStrategy({ instrument: 'acoustic_bass', role: 'melody', midi_program: 32 }).id,
    'bass',
  );
  assert.equal(
    selectInstrumentStrategy({ instrument: 'acoustic_grand_piano', role: 'pad', midi_program: 0 }).id,
    'piano_keyboard',
  );
});
