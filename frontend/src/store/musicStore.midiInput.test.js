import assert from 'node:assert/strict';
import test from 'node:test';
import { migrateV1ToV2 } from '../utils/compositionVersion.js';
import { MIDI_PHASES, useMusicStore } from './musicStore.js';

function sampleComposition() {
  return migrateV1ToV2({
    schema_version: 'composition.v1',
    tempo: 100,
    key: 'C major',
    time_signature: '4/4',
    ticks_per_quarter: 480,
    bar_count: 4,
    duration_ticks: 7680,
    sections: [
      { id: 'verse', type: 'verse', start_bar: 1, bar_count: 4, start_tick: 0, duration_ticks: 7680 },
    ],
    tracks: [
      {
        id: 'melody-1',
        name: 'Melody',
        instrument: 'piano',
        role: 'melody',
        midi_program: 0,
        channel: 1,
        is_drum: false,
        volume: 100,
        events: [],
      },
    ],
    harmony: [],
  });
}

function resetMidiStore(composition = sampleComposition()) {
  useMusicStore.getState().resetMidiInputSession();
  useMusicStore.setState({
    generatedMusicJson: composition,
    editedMusicJson: composition,
    compositionRevision: 'midi-test-rev',
    pianoRollTrackId: 'melody-1',
    editCursorTick: 0,
    compositionEditUndoStack: [],
    compositionEditRedoStack: [],
  });
}

function createFakeAccess(ports = []) {
  const inputs = new Map(ports.map((port) => [port.id, port]));
  const listeners = new Set();
  return {
    inputs,
    addEventListener(type, handler) {
      if (type === 'statechange') listeners.add(handler);
    },
    removeEventListener(type, handler) {
      if (type === 'statechange') listeners.delete(handler);
    },
    emitStateChange(port) {
      for (const handler of listeners) {
        handler({ port });
      }
    },
  };
}

test('store init does not request MIDI access', () => {
  resetMidiStore();
  const state = useMusicStore.getState();
  assert.equal(state.midiPhase, MIDI_PHASES.IDLE);
  assert.equal(state.midiAccessStatus, 'idle');
  assert.equal(state.midiArmed, false);
  assert.deepEqual(state.midiActiveNotes, []);
});

test('enableMidiAccess transitions to ready and selects preferred device', async () => {
  resetMidiStore();
  const port = {
    id: 'keys-1',
    name: 'Keys',
    manufacturer: 'Test',
    state: 'connected',
    connection: 'closed',
    type: 'input',
    addEventListener() {},
    removeEventListener() {},
    open() {
      return Promise.resolve();
    },
  };
  const access = createFakeAccess([port]);
  const result = await useMusicStore.getState().enableMidiAccess({
    probeEnv: { isSecureContext: true, hasRequestMidiAccess: true },
    storage: {
      getItem() { return null; },
      setItem() {},
    },
    async requestMIDIAccess() {
      return access;
    },
  });
  assert.equal(result.ok, true);
  const state = useMusicStore.getState();
  assert.equal(state.midiPhase, MIDI_PHASES.READY);
  assert.equal(state.midiSelectedInputId, 'keys-1');
  assert.equal(state.midiDestinationTrackId, 'melody-1');
  useMusicStore.getState().resetMidiInputSession();
});

test('arm and disarm MIDI recording updates phase machine', async () => {
  resetMidiStore();
  await useMusicStore.getState().enableMidiAccess({
    probeEnv: { isSecureContext: true, hasRequestMidiAccess: true },
    storage: { getItem() { return null; }, setItem() {} },
    async requestMIDIAccess() {
      return createFakeAccess([{
        id: 'keys-1',
        name: 'Keys',
        manufacturer: 'Test',
        state: 'connected',
        connection: 'closed',
        type: 'input',
        addEventListener() {},
        removeEventListener() {},
        open() { return Promise.resolve(); },
      }]);
    },
  });
  assert.equal(useMusicStore.getState().armMidiRecording(), true);
  assert.equal(useMusicStore.getState().midiPhase, MIDI_PHASES.ARMED);
  assert.equal(useMusicStore.getState().midiArmed, true);
  assert.equal(useMusicStore.getState().disarmMidiRecording(), true);
  assert.equal(useMusicStore.getState().midiPhase, MIDI_PHASES.READY);
  assert.equal(useMusicStore.getState().midiArmed, false);
  useMusicStore.getState().resetMidiInputSession();
});

test('arm rejects when no destination track exists', () => {
  resetMidiStore(null);
  useMusicStore.setState({
    editedMusicJson: null,
    pianoRollTrackId: null,
    midiPhase: MIDI_PHASES.READY,
    midiAccessStatus: 'ready',
  });
  assert.equal(useMusicStore.getState().armMidiRecording(), false);
  assert.equal(useMusicStore.getState().midiErrorCode, 'midi_no_destination');
});

test('panic clears active notes without changing phase', () => {
  resetMidiStore();
  useMusicStore.setState({
    midiPhase: MIDI_PHASES.READY,
    midiActiveNotes: ['C4', 'E4'],
  });
  useMusicStore.getState().panicMidiNotes();
  assert.deepEqual(useMusicStore.getState().midiActiveNotes, []);
  assert.equal(useMusicStore.getState().midiPhase, MIDI_PHASES.READY);
});

test('record inject stop commits notes and undo removes take', async () => {
  resetMidiStore();
  useMusicStore.setState({ midiCountInBars: 0, midiQuantizeAfterRecord: false });
  await useMusicStore.getState().enableMidiAccess({
    probeEnv: { isSecureContext: true, hasRequestMidiAccess: true },
    storage: { getItem() { return null; }, setItem() {} },
    async requestMIDIAccess() {
      return createFakeAccess([{
        id: 'keys-1',
        name: 'Keys',
        manufacturer: 'Test',
        state: 'connected',
        connection: 'closed',
        type: 'input',
        addEventListener() {},
        removeEventListener() {},
        open() { return Promise.resolve(); },
      }]);
    },
  });

  assert.equal(useMusicStore.getState().startMidiRecording({ skipCountIn: true }), true);
  assert.equal(useMusicStore.getState().midiPhase, MIDI_PHASES.RECORDING);
  useMusicStore.getState().injectMidiMessage([0x90, 60, 100]);
  useMusicStore.getState().injectMidiMessage([0x80, 60, 0]);
  assert.equal(useMusicStore.getState().stopMidiRecording({ commit: true }), true);

  const after = useMusicStore.getState();
  assert.equal(after.midiPhase, MIDI_PHASES.READY);
  assert.ok(after.editedMusicJson.tracks[0].events.length >= 1);
  assert.ok(after.compositionEditUndoStack.length >= 1);

  after.undoCompositionEdit();
  assert.equal(useMusicStore.getState().editedMusicJson.tracks[0].events.length, 0);
  useMusicStore.getState().resetMidiInputSession();
});

test('quantize after record runs second transaction when enabled', async () => {
  resetMidiStore();
  useMusicStore.setState({
    midiCountInBars: 0,
    midiQuantizeAfterRecord: true,
    pianoRollSnap: '1/4',
  });
  await useMusicStore.getState().enableMidiAccess({
    probeEnv: { isSecureContext: true, hasRequestMidiAccess: true },
    storage: { getItem() { return null; }, setItem() {} },
    async requestMIDIAccess() {
      return createFakeAccess([{
        id: 'keys-1',
        name: 'Keys',
        manufacturer: 'Test',
        state: 'connected',
        connection: 'closed',
        type: 'input',
        addEventListener() {},
        removeEventListener() {},
        open() { return Promise.resolve(); },
      }]);
    },
  });
  useMusicStore.getState().startMidiRecording({ skipCountIn: true });
  // Off-grid note-on then off via inject (same tick → duration 1; quantize still runs)
  useMusicStore.getState().injectMidiMessage([0x90, 60, 100]);
  useMusicStore.getState().injectMidiMessage([0x80, 60, 0]);
  assert.equal(useMusicStore.getState().stopMidiRecording({ commit: true }), true);
  assert.ok(useMusicStore.getState().compositionEditUndoStack.length >= 1);
  useMusicStore.getState().resetMidiInputSession();
});

test('discardMidiTake clears buffer without mutating composition', async () => {
  resetMidiStore();
  useMusicStore.setState({ midiCountInBars: 0 });
  await useMusicStore.getState().enableMidiAccess({
    probeEnv: { isSecureContext: true, hasRequestMidiAccess: true },
    storage: { getItem() { return null; }, setItem() {} },
    async requestMIDIAccess() {
      return createFakeAccess([{
        id: 'keys-1',
        name: 'Keys',
        manufacturer: 'Test',
        state: 'connected',
        connection: 'closed',
        type: 'input',
        addEventListener() {},
        removeEventListener() {},
        open() { return Promise.resolve(); },
      }]);
    },
  });
  useMusicStore.getState().startMidiRecording({ skipCountIn: true });
  useMusicStore.getState().injectMidiMessage([0x90, 64, 90]);
  useMusicStore.getState().discardMidiTake();
  assert.equal(useMusicStore.getState().editedMusicJson.tracks[0].events.length, 0);
  assert.equal(useMusicStore.getState().midiPhase, MIDI_PHASES.READY);
  useMusicStore.getState().resetMidiInputSession();
});
