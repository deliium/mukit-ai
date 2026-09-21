import assert from 'node:assert/strict';
import test from 'node:test';
import {
  createMidiAccessSession,
  listMidiInputs,
  MIDI_PREF_STORAGE_KEY,
  normalizeMidiInput,
  readMidiInputPreference,
  writeMidiInputPreference,
} from './midiInputAccess.js';

function createFakeStorage(initial = {}) {
  const map = new Map(Object.entries(initial));
  return {
    getItem(key) {
      return map.has(key) ? map.get(key) : null;
    },
    setItem(key, value) {
      map.set(key, String(value));
    },
    removeItem(key) {
      map.delete(key);
    },
  };
}

function createFakeAccess(ports = []) {
  const inputs = new Map(ports.map((port) => [port.id, port]));
  /** @type {Set<Function>} */
  const listeners = new Set();
  return {
    inputs,
    addEventListener(type, handler) {
      if (type === 'statechange') {
        listeners.add(handler);
      }
    },
    removeEventListener(type, handler) {
      if (type === 'statechange') {
        listeners.delete(handler);
      }
    },
    /** @param {object} port */
    emitStateChange(port) {
      for (const handler of listeners) {
        handler({ port });
      }
    },
  };
}

test('normalizeMidiInput and listMidiInputs skip disconnected ports', () => {
  assert.equal(normalizeMidiInput(null), null);
  const listed = listMidiInputs(createFakeAccess([
    { id: 'a', name: 'Pad', manufacturer: 'X', state: 'connected', connection: 'open', type: 'input' },
    { id: 'b', name: 'Gone', manufacturer: 'Y', state: 'disconnected', connection: 'closed', type: 'input' },
  ]));
  assert.equal(listed.length, 1);
  assert.equal(listed[0].id, 'a');
});

test('MIDI preference round-trips selected input id', () => {
  const storage = createFakeStorage();
  assert.deepEqual(readMidiInputPreference(storage), { selectedInputId: null });
  assert.equal(writeMidiInputPreference({ selectedInputId: 'dev-1' }, storage), true);
  assert.deepEqual(readMidiInputPreference(storage), { selectedInputId: 'dev-1' });
  assert.ok(storage.getItem(MIDI_PREF_STORAGE_KEY));
});

test('enableMidiAccess lists inputs and remembers preferred selection', async () => {
  const storage = createFakeStorage({
    [MIDI_PREF_STORAGE_KEY]: JSON.stringify({ selectedInputId: 'kbd-1' }),
  });
  const access = createFakeAccess([
    {
      id: 'kbd-1',
      name: 'Keys',
      manufacturer: 'Test',
      state: 'connected',
      connection: 'closed',
      type: 'input',
    },
  ]);
  const session = createMidiAccessSession({
    probeEnv: { isSecureContext: true, hasRequestMidiAccess: true },
    storage,
    async requestMIDIAccess() {
      return access;
    },
  });

  const events = [];
  session.subscribe((event) => events.push(event.type));
  const result = await session.enable();
  assert.equal(result.ok, true);
  assert.equal(result.inputs.length, 1);
  assert.equal(result.preferredInputId, 'kbd-1');
  assert.ok(events.includes('inputs'));
  session.dispose();
});

test('device disconnect emits midi_device_disconnected without crashing', async () => {
  const port = {
    id: 'usb-99',
    name: 'Controller',
    manufacturer: 'Test',
    state: 'connected',
    connection: 'open',
    type: 'input',
  };
  const access = createFakeAccess([port]);
  const session = createMidiAccessSession({
    probeEnv: { isSecureContext: true, hasRequestMidiAccess: true },
    storage: createFakeStorage(),
    async requestMIDIAccess() {
      return access;
    },
  });
  await session.enable();

  const disconnectEvents = [];
  session.subscribe((event) => {
    if (event.type === 'disconnect') {
      disconnectEvents.push(event);
    }
  });

  port.state = 'disconnected';
  access.inputs.delete(port.id);
  access.emitStateChange(port);

  assert.equal(disconnectEvents.length, 1);
  assert.equal(disconnectEvents[0].code, 'midi_device_disconnected');
  assert.equal(disconnectEvents[0].deviceId, 'usb-99');
  session.dispose();
});

test('enable fails cleanly when probe reports unsupported', async () => {
  const session = createMidiAccessSession({
    probeEnv: { isSecureContext: true, hasRequestMidiAccess: false },
    async requestMIDIAccess() {
      throw new Error('should not be called');
    },
  });
  const result = await session.enable();
  assert.equal(result.ok, false);
  assert.equal(result.reason, 'unsupported');
  session.dispose();
});

test('permission denial maps to permission_denied', async () => {
  const session = createMidiAccessSession({
    probeEnv: { isSecureContext: true, hasRequestMidiAccess: true },
    async requestMIDIAccess() {
      const error = new Error('denied');
      error.name = 'NotAllowedError';
      throw error;
    },
  });
  const result = await session.enable();
  assert.equal(result.ok, false);
  assert.equal(result.reason, 'permission_denied');
  session.dispose();
});
