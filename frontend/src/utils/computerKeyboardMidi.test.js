import assert from 'node:assert/strict';
import test from 'node:test';
import {
  COMPUTER_KEYBOARD_DEFAULT_VELOCITY,
  computerKeyboardCodeToMidi,
  createComputerKeyboardMidi,
  syntheticMidiBytes,
} from './computerKeyboardMidi.js';

test('computerKeyboardCodeToMidi maps two-octave layout', () => {
  assert.equal(computerKeyboardCodeToMidi('KeyZ'), 48);
  assert.equal(computerKeyboardCodeToMidi('KeyQ'), 60);
  assert.equal(computerKeyboardCodeToMidi('Digit2'), 61);
  assert.equal(computerKeyboardCodeToMidi('KeyA'), null);
});

test('syntheticMidiBytes builds note on/off frames', () => {
  assert.deepEqual(syntheticMidiBytes('on', 60, 100), [0x90, 60, 100]);
  assert.deepEqual(syntheticMidiBytes('off', 60), [0x80, 60, 0]);
});

test('computer keyboard emits on/off and ignores text targets', () => {
  const messages = [];
  const kb = createComputerKeyboardMidi({
    enabled: true,
    onMessage: (bytes) => messages.push(bytes),
    shouldIgnoreTarget: (target) => target?.tagName === 'INPUT',
  });

  assert.equal(kb.handleKeyDown({
    code: 'KeyQ',
    repeat: false,
    target: { tagName: 'DIV' },
    preventDefault() {},
  }), true);
  assert.deepEqual(messages[0], [0x90, 60, COMPUTER_KEYBOARD_DEFAULT_VELOCITY]);

  assert.equal(kb.handleKeyDown({
    code: 'KeyQ',
    repeat: false,
    target: { tagName: 'INPUT' },
    preventDefault() {},
  }), false);

  assert.equal(kb.handleKeyUp({ code: 'KeyQ' }), true);
  assert.deepEqual(messages[1], [0x80, 60, 0]);
});

test('disabled keyboard releases held notes', () => {
  const messages = [];
  const kb = createComputerKeyboardMidi({
    enabled: true,
    onMessage: (bytes) => messages.push(bytes),
    shouldIgnoreTarget: () => false,
  });
  kb.handleKeyDown({
    code: 'KeyZ',
    repeat: false,
    target: { tagName: 'DIV' },
    preventDefault() {},
  });
  kb.setEnabled(false);
  assert.ok(messages.some((bytes) => bytes[0] === 0x80 && bytes[1] === 48));
  assert.equal(kb.heldCount(), 0);
});
