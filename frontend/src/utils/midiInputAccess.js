/**
 * Web MIDI access registry — enable on user gesture only.
 * Dependency-injectable for headless CI (no real navigator.requestMIDIAccess).
 */

import { createAppLogger } from './appLogger.js';
import {
  midiAccessFailureReason,
  MIDI_SUPPORT_REASONS,
  probeWebMidiSupport,
} from './midiInputSupport.js';

const log = createAppLogger('midiInput');

export const MIDI_PREF_STORAGE_KEY = 'midiInput:v1';

/**
 * @typedef {{
 *   id: string,
 *   name: string,
 *   manufacturer: string,
 *   state: string,
 *   connection: string,
 * }} MidiInputDescriptor
 *
 * @typedef {{
 *   type: 'inputs' | 'disconnect' | 'reconnect' | 'error',
 *   inputs?: MidiInputDescriptor[],
 *   deviceId?: string,
 *   deviceName?: string,
 *   code?: string,
 *   message?: string,
 * }} MidiAccessEvent
 *
 * @typedef {(event: MidiAccessEvent) => void} MidiAccessListener
 */

/**
 * @param {unknown} port
 * @returns {MidiInputDescriptor | null}
 */
export function normalizeMidiInput(port) {
  if (!port || typeof port !== 'object') {
    return null;
  }
  const id = typeof port.id === 'string' ? port.id : '';
  if (!id) {
    return null;
  }
  return {
    id,
    name: typeof port.name === 'string' && port.name ? port.name : id,
    manufacturer: typeof port.manufacturer === 'string' ? port.manufacturer : '',
    state: typeof port.state === 'string' ? port.state : 'connected',
    connection: typeof port.connection === 'string' ? port.connection : 'closed',
  };
}

/**
 * @param {MIDIAccess | { inputs?: Map<string, unknown> | Iterable<[string, unknown]> }} access
 * @returns {MidiInputDescriptor[]}
 */
export function listMidiInputs(access) {
  if (!access || !access.inputs) {
    return [];
  }
  const inputs = [];
  const iterable = typeof access.inputs.values === 'function'
    ? access.inputs.values()
    : access.inputs;
  for (const port of iterable) {
    const normalized = normalizeMidiInput(port);
    if (normalized && normalized.state !== 'disconnected') {
      inputs.push(normalized);
    }
  }
  inputs.sort((a, b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id));
  return inputs;
}

/**
 * @returns {{ selectedInputId: string | null }}
 */
export function readMidiInputPreference(storage = globalThis.localStorage) {
  try {
    if (!storage || typeof storage.getItem !== 'function') {
      return { selectedInputId: null };
    }
    const raw = storage.getItem(MIDI_PREF_STORAGE_KEY);
    if (!raw) {
      return { selectedInputId: null };
    }
    const parsed = JSON.parse(raw);
    const selectedInputId =
      parsed && typeof parsed.selectedInputId === 'string' && parsed.selectedInputId
        ? parsed.selectedInputId
        : null;
    return { selectedInputId };
  } catch (error) {
    log.warn('Failed to read MIDI preference', {
      code: 'midi_pref_read_failed',
      message: error instanceof Error ? error.message : 'unknown',
    });
    return { selectedInputId: null };
  }
}

/**
 * @param {{ selectedInputId?: string | null }} preference
 */
export function writeMidiInputPreference(preference, storage = globalThis.localStorage) {
  try {
    if (!storage || typeof storage.setItem !== 'function') {
      return false;
    }
    const selectedInputId =
      preference && typeof preference.selectedInputId === 'string' && preference.selectedInputId
        ? preference.selectedInputId
        : null;
    storage.setItem(MIDI_PREF_STORAGE_KEY, JSON.stringify({ selectedInputId }));
    log.debug('Wrote MIDI preference', { hasSelection: Boolean(selectedInputId) });
    return true;
  } catch (error) {
    log.warn('Failed to write MIDI preference', {
      code: 'midi_pref_write_failed',
      message: error instanceof Error ? error.message : 'unknown',
    });
    return false;
  }
}

/**
 * Create a disposable MIDI access session.
 * @param {{
 *   requestMIDIAccess?: (options?: { sysex?: boolean }) => Promise<MIDIAccess>,
 *   probeEnv?: Parameters<typeof probeWebMidiSupport>[0],
 *   storage?: Storage,
 * }} [deps]
 */
export function createMidiAccessSession(deps = {}) {
  /** @type {MIDIAccess | null} */
  let access = null;
  /** @type {Set<MidiAccessListener>} */
  const listeners = new Set();
  /** @type {((event: Event) => void) | null} */
  let stateChangeHandler = null;
  let disposed = false;

  function emit(event) {
    for (const listener of listeners) {
      try {
        listener(event);
      } catch (error) {
        log.error('MIDI access listener failed', {
          code: 'midi_listener_error',
          message: error instanceof Error ? error.message : 'unknown',
        });
      }
    }
  }

  function refreshInputs(reason) {
    if (!access) {
      return [];
    }
    const inputs = listMidiInputs(access);
    log.debug('MIDI inputs refreshed', { reason, inputCount: inputs.length });
    emit({ type: 'inputs', inputs });
    return inputs;
  }

  function onStateChange(event) {
    const port = event && event.port ? event.port : null;
    const descriptor = normalizeMidiInput(port);
    if (!descriptor || (port && port.type && port.type !== 'input')) {
      refreshInputs('statechange');
      return;
    }
    if (descriptor.state === 'disconnected') {
      log.warn('MIDI input disconnected', {
        code: 'midi_device_disconnected',
        deviceId: descriptor.id.slice(0, 12),
        deviceName: descriptor.name,
      });
      emit({
        type: 'disconnect',
        deviceId: descriptor.id,
        deviceName: descriptor.name,
        code: 'midi_device_disconnected',
        inputs: listMidiInputs(access),
      });
    } else if (descriptor.state === 'connected') {
      log.warn('MIDI input reconnected', {
        code: 'midi_device_reconnected',
        deviceId: descriptor.id.slice(0, 12),
        deviceName: descriptor.name,
      });
      emit({
        type: 'reconnect',
        deviceId: descriptor.id,
        deviceName: descriptor.name,
        code: 'midi_device_reconnected',
        inputs: listMidiInputs(access),
      });
    } else {
      refreshInputs('statechange');
    }
  }

  /**
   * @returns {Promise<{
   *   ok: boolean,
   *   reason: string,
   *   inputs: MidiInputDescriptor[],
   *   preferredInputId: string | null,
   * }>}
   */
  async function enable() {
    if (disposed) {
      return {
        ok: false,
        reason: MIDI_SUPPORT_REASONS.UNSUPPORTED,
        inputs: [],
        preferredInputId: null,
      };
    }

    const probe = probeWebMidiSupport(deps.probeEnv);
    if (!probe.supported) {
      log.info('MIDI enable skipped — unsupported', { reason: probe.reason });
      return {
        ok: false,
        reason: probe.reason,
        inputs: [],
        preferredInputId: null,
      };
    }

    const request =
      deps.requestMIDIAccess
      || (typeof globalThis !== 'undefined'
        && globalThis.navigator
        && typeof globalThis.navigator.requestMIDIAccess === 'function'
        ? globalThis.navigator.requestMIDIAccess.bind(globalThis.navigator)
        : null);

    if (!request) {
      log.info('MIDI enable failed — no requestMIDIAccess', {
        reason: MIDI_SUPPORT_REASONS.UNSUPPORTED,
      });
      return {
        ok: false,
        reason: MIDI_SUPPORT_REASONS.UNSUPPORTED,
        inputs: [],
        preferredInputId: null,
      };
    }

    try {
      access = await request({ sysex: false });
      if (disposed) {
        dispose();
        return {
          ok: false,
          reason: MIDI_SUPPORT_REASONS.UNSUPPORTED,
          inputs: [],
          preferredInputId: null,
        };
      }

      stateChangeHandler = onStateChange;
      if (typeof access.addEventListener === 'function') {
        access.addEventListener('statechange', stateChangeHandler);
      } else {
        access.onstatechange = stateChangeHandler;
      }

      const inputs = listMidiInputs(access);
      const preferred = readMidiInputPreference(deps.storage);
      const preferredInputId =
        preferred.selectedInputId
        && inputs.some((input) => input.id === preferred.selectedInputId)
          ? preferred.selectedInputId
          : null;

      log.info('MIDI access enabled', {
        code: 'midi_access_enabled',
        inputCount: inputs.length,
        hasPreferred: Boolean(preferredInputId),
      });
      emit({ type: 'inputs', inputs });
      return {
        ok: true,
        reason: MIDI_SUPPORT_REASONS.AVAILABLE,
        inputs,
        preferredInputId,
      };
    } catch (error) {
      const reason = midiAccessFailureReason(error);
      log.error('MIDI access enable failed', {
        code: 'midi_access_failed',
        reason,
        message: error instanceof Error ? error.message : 'unknown',
      });
      emit({
        type: 'error',
        code: 'midi_access_failed',
        message: error instanceof Error ? error.message : 'unknown',
      });
      return {
        ok: false,
        reason,
        inputs: [],
        preferredInputId: null,
      };
    }
  }

  /**
   * @param {string} inputId
   * @param {(event: MIDIMessageEvent) => void} handler
   * @returns {(() => void) | null} unsubscribe
   */
  function subscribeInput(inputId, handler) {
    if (!access || !inputId || typeof handler !== 'function') {
      return null;
    }
    const port = typeof access.inputs?.get === 'function'
      ? access.inputs.get(inputId)
      : null;
    if (!port) {
      log.warn('MIDI subscribe failed — input missing', {
        code: 'midi_input_missing',
        deviceId: String(inputId).slice(0, 12),
      });
      return null;
    }
    const onMessage = (event) => {
      try {
        handler(event);
      } catch (error) {
        log.error('MIDI message handler failed', {
          code: 'midi_message_handler_error',
          message: error instanceof Error ? error.message : 'unknown',
        });
      }
    };
    if (typeof port.addEventListener === 'function') {
      port.addEventListener('midimessage', onMessage);
    } else {
      port.onmidimessage = onMessage;
    }
    if (typeof port.open === 'function') {
      try {
        const opened = port.open();
        if (opened && typeof opened.catch === 'function') {
          opened.catch((error) => {
            log.warn('MIDI port open rejected', {
              code: 'midi_port_open_failed',
              message: error instanceof Error ? error.message : 'unknown',
            });
          });
        }
      } catch (error) {
        log.warn('MIDI port open threw', {
          code: 'midi_port_open_failed',
          message: error instanceof Error ? error.message : 'unknown',
        });
      }
    }
    log.debug('Subscribed MIDI input', { deviceId: String(inputId).slice(0, 12) });
    return () => {
      if (typeof port.removeEventListener === 'function') {
        port.removeEventListener('midimessage', onMessage);
      } else if (port.onmidimessage === onMessage) {
        port.onmidimessage = null;
      }
    };
  }

  function selectAndRemember(inputId) {
    writeMidiInputPreference({ selectedInputId: inputId || null }, deps.storage);
  }

  function getInputs() {
    return access ? listMidiInputs(access) : [];
  }

  function subscribe(listener) {
    if (typeof listener !== 'function') {
      return () => {};
    }
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  }

  function dispose() {
    disposed = true;
    if (access && stateChangeHandler) {
      if (typeof access.removeEventListener === 'function') {
        access.removeEventListener('statechange', stateChangeHandler);
      } else if (access.onstatechange === stateChangeHandler) {
        access.onstatechange = null;
      }
    }
    stateChangeHandler = null;
    access = null;
    listeners.clear();
    log.debug('MIDI access session disposed');
  }

  return {
    enable,
    subscribe,
    subscribeInput,
    selectAndRemember,
    getInputs,
    dispose,
  };
}

/**
 * One-shot helper used by callers that do not need a long-lived session.
 * Prefer createMidiAccessSession for UI/store wiring.
 */
export async function enableMidiAccess(deps = {}) {
  const session = createMidiAccessSession(deps);
  const result = await session.enable();
  return { ...result, session };
}
