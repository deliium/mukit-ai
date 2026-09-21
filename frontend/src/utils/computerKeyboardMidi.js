/**
 * Computer-keyboard → synthetic MIDI note on/off for tests and demos.
 * Two-octave layout (white + black keys) starting at C3 (MIDI 48).
 *
 *   Lower octave (C3–B3):  Z X C V B N M  + S D   G H   (blacks)
 *   Upper octave (C4–B4):  Q W E R T Y U  + 2 3   5 6 7
 *
 * Disabled while focus is in inputs / contenteditable (reuses editor shortcut guard).
 */

import { createAppLogger } from './appLogger.js';
import { shouldIgnoreShortcutTarget } from './editorShortcuts.js';

const log = createAppLogger('midiInput');

/** Default velocity for QWERTY test notes. */
export const COMPUTER_KEYBOARD_DEFAULT_VELOCITY = 96;

/**
 * Map KeyboardEvent.code → MIDI note number (fixed layout).
 * Using `code` keeps layout stable across locales.
 */
export const COMPUTER_KEYBOARD_NOTE_MAP = Object.freeze({
  // Lower octave whites C3–B3
  KeyZ: 48,
  KeyX: 50,
  KeyC: 52,
  KeyV: 53,
  KeyB: 55,
  KeyN: 57,
  KeyM: 59,
  // Lower octave blacks
  KeyS: 49,
  KeyD: 51,
  KeyG: 54,
  KeyH: 56,
  KeyJ: 58,
  // Upper octave whites C4–B4
  KeyQ: 60,
  KeyW: 62,
  KeyE: 64,
  KeyR: 65,
  KeyT: 67,
  KeyY: 69,
  KeyU: 71,
  // Upper octave blacks
  Digit2: 61,
  Digit3: 63,
  Digit5: 66,
  Digit6: 68,
  Digit7: 70,
});

/**
 * @param {string} code
 * @returns {number | null}
 */
export function computerKeyboardCodeToMidi(code) {
  if (!code || typeof code !== 'string') {
    return null;
  }
  const midi = COMPUTER_KEYBOARD_NOTE_MAP[code];
  return Number.isInteger(midi) ? midi : null;
}

/**
 * Build a MIDI note-on/off byte triple (channel 0).
 * @param {'on'|'off'} kind
 * @param {number} midi
 * @param {number} [velocity]
 * @returns {number[]}
 */
export function syntheticMidiBytes(kind, midi, velocity = COMPUTER_KEYBOARD_DEFAULT_VELOCITY) {
  const note = Math.max(0, Math.min(127, Math.round(Number(midi) || 0)));
  if (kind === 'off') {
    return [0x80, note, 0];
  }
  const vel = Math.max(1, Math.min(127, Math.round(Number(velocity) || COMPUTER_KEYBOARD_DEFAULT_VELOCITY)));
  return [0x90, note, vel];
}

/**
 * @param {{
 *   onMessage: (bytes: number[], meta?: { source: string, code: string }) => void,
 *   enabled?: boolean,
 *   velocity?: number,
 *   shouldIgnoreTarget?: (target: EventTarget | null) => boolean,
 * }} options
 */
export function createComputerKeyboardMidi(options) {
  const onMessage = options.onMessage;
  const ignoreTarget = typeof options.shouldIgnoreTarget === 'function'
    ? options.shouldIgnoreTarget
    : (target) => shouldIgnoreShortcutTarget(target);
  let enabled = Boolean(options.enabled);
  let velocity = options.velocity ?? COMPUTER_KEYBOARD_DEFAULT_VELOCITY;
  /** @type {Set<string>} */
  const heldCodes = new Set();

  function setEnabled(next) {
    enabled = Boolean(next);
    if (!enabled) {
      releaseAll();
    }
    log.info('Computer keyboard MIDI toggled', { enabled });
  }

  function handleKeyDown(event) {
    if (!enabled || !event || event.repeat) {
      return false;
    }
    if (event.ctrlKey || event.metaKey || event.altKey) {
      return false;
    }
    if (ignoreTarget(event.target)) {
      return false;
    }
    const midi = computerKeyboardCodeToMidi(event.code);
    if (midi == null) {
      return false;
    }
    if (heldCodes.has(event.code)) {
      return false;
    }
    heldCodes.add(event.code);
    onMessage(syntheticMidiBytes('on', midi, velocity), {
      source: 'computer_keyboard',
      code: event.code,
    });
    if (typeof event.preventDefault === 'function') {
      event.preventDefault();
    }
    return true;
  }

  function handleKeyUp(event) {
    if (!event) {
      return false;
    }
    const midi = computerKeyboardCodeToMidi(event.code);
    if (midi == null) {
      return false;
    }
    if (!heldCodes.has(event.code)) {
      return false;
    }
    heldCodes.delete(event.code);
    onMessage(syntheticMidiBytes('off', midi), {
      source: 'computer_keyboard',
      code: event.code,
    });
    return true;
  }

  function releaseAll() {
    for (const code of [...heldCodes]) {
      const midi = computerKeyboardCodeToMidi(code);
      heldCodes.delete(code);
      if (midi != null) {
        onMessage(syntheticMidiBytes('off', midi), {
          source: 'computer_keyboard',
          code,
        });
      }
    }
  }

  function attach(target = globalThis) {
    if (!target || typeof target.addEventListener !== 'function') {
      return () => {};
    }
    const down = (event) => handleKeyDown(event);
    const up = (event) => handleKeyUp(event);
    const blur = () => releaseAll();
    target.addEventListener('keydown', down);
    target.addEventListener('keyup', up);
    target.addEventListener('blur', blur);
    log.debug('Computer keyboard MIDI attached');
    return () => {
      target.removeEventListener('keydown', down);
      target.removeEventListener('keyup', up);
      target.removeEventListener('blur', blur);
      releaseAll();
      log.debug('Computer keyboard MIDI detached');
    };
  }

  return {
    setEnabled,
    isEnabled: () => enabled,
    handleKeyDown,
    handleKeyUp,
    releaseAll,
    attach,
    heldCount: () => heldCodes.size,
  };
}
