import assert from 'node:assert/strict';
import test from 'node:test';
import {
  clampMidiVelocity,
  isSustainControlChange,
  MIDI_CC_SUSTAIN,
  MIDI_MESSAGE_KINDS,
  parseMidiMessage,
} from './midiInputMessages.js';

test('parseMidiMessage parses note on with velocity and channel', () => {
  const msg = parseMidiMessage([0x91, 60, 100]);
  assert.deepEqual(msg, {
    kind: MIDI_MESSAGE_KINDS.NOTE_ON,
    channel: 1,
    note: 60,
    velocity: 100,
  });
});

test('parseMidiMessage treats velocity-0 note-on as note-off', () => {
  const msg = parseMidiMessage([0x90, 64, 0]);
  assert.deepEqual(msg, {
    kind: MIDI_MESSAGE_KINDS.NOTE_OFF,
    channel: 0,
    note: 64,
    velocity: 0,
  });
});

test('parseMidiMessage parses note off', () => {
  const msg = parseMidiMessage(Uint8Array.from([0x80, 72, 40]));
  assert.deepEqual(msg, {
    kind: MIDI_MESSAGE_KINDS.NOTE_OFF,
    channel: 0,
    note: 72,
    velocity: 40,
  });
});

test('parseMidiMessage parses CC64 sustain on/off', () => {
  const on = parseMidiMessage([0xb0, MIDI_CC_SUSTAIN, 127]);
  assert.equal(on.kind, MIDI_MESSAGE_KINDS.CONTROL_CHANGE);
  assert.equal(on.controller, MIDI_CC_SUSTAIN);
  assert.equal(on.sustain, true);
  assert.equal(isSustainControlChange(on), true);

  const off = parseMidiMessage([0xb0, MIDI_CC_SUSTAIN, 0]);
  assert.equal(off.sustain, false);
  assert.equal(isSustainControlChange(off), true);
});

test('parseMidiMessage ignores pitch bend and incomplete frames', () => {
  assert.equal(parseMidiMessage([0xe0, 0, 64]).kind, MIDI_MESSAGE_KINDS.IGNORED);
  assert.equal(parseMidiMessage([0x90, 60]).kind, MIDI_MESSAGE_KINDS.IGNORED);
  assert.equal(parseMidiMessage(null).kind, MIDI_MESSAGE_KINDS.IGNORED);
  assert.equal(parseMidiMessage([]).kind, MIDI_MESSAGE_KINDS.IGNORED);
});

test('clampMidiVelocity keeps composition.v2 range', () => {
  assert.equal(clampMidiVelocity(0), 1);
  assert.equal(clampMidiVelocity(-3), 1);
  assert.equal(clampMidiVelocity(200), 127);
  assert.equal(clampMidiVelocity(64.4), 64);
});
