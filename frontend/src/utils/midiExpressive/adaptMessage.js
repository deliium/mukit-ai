/**
 * Unified entry: bytes or UMP words → midi.expressive.event.v1[].
 * Honors VITE_MIDI_EXPRESSIVE_ENABLED (legacy note/CC64 only when falsy).
 */

import { createAppLogger } from '../appLogger.js';
import {
  isMidiExpressiveEnabled,
  MIDI_TRANSPORT,
} from './constants.js';
import { adaptMidi1BytesToExpressive } from './midi1Bytes.js';
import { resolveMpeEventRouting } from './mpeZone.js';
import { decodeUmpShipSubset } from './umpTransport.js';

const log = createAppLogger('midiExpressive');

/**
 * @param {{
 *   data?: Iterable<number> | ArrayLike<number> | null,
 *   umpWords?: ArrayLike<number> | null,
 *   transport?: import('./constants.js').MidiTransportId | string,
 *   expressiveEnv?: Record<string, unknown>,
 *   mpeMappingEnabled?: boolean,
 *   mpeZone?: { master_channel: number, member_channel_low: number, member_channel_high: number },
 *   atMs?: number,
 * }} [options]
 * @returns {{
 *   events: import('./schemas.js').MidiExpressiveEventV1[],
 *   adapter: 'midi1_bytes' | 'ump_experimental' | 'legacy',
 *   mpe: ReturnType<typeof resolveMpeEventRouting> | null,
 * }}
 */
export function adaptIncomingMidi(options = {}) {
  const expressiveOn = isMidiExpressiveEnabled(options.expressiveEnv);
  const atMs = options.atMs;
  const transport = options.transport || MIDI_TRANSPORT.MIDI1_BYTES;

  if (!expressiveOn) {
    log.debug('Adapter entry: expressive disabled → legacy', {
      transport: 'legacy',
    });
    const event = adaptMidi1BytesToExpressive(options.data, {
      legacyOnly: true,
      atMs,
    });
    return {
      events: [event],
      adapter: 'legacy',
      mpe: null,
    };
  }

  if (
    transport === MIDI_TRANSPORT.UMP_EXPERIMENTAL
    && options.umpWords
    && options.umpWords.length > 0
  ) {
    const events = decodeUmpShipSubset(options.umpWords, { atMs });
    log.debug('Adapter entry: ump_experimental', {
      eventCount: events.length,
    });
    return {
      events,
      adapter: 'ump_experimental',
      mpe: events[0]
        ? resolveMpeEventRouting(events[0], {
            mappingEnabled: options.mpeMappingEnabled,
            zone: options.mpeZone,
          })
        : null,
    };
  }

  const event = adaptMidi1BytesToExpressive(options.data, {
    legacyOnly: false,
    atMs,
  });
  const mpe = resolveMpeEventRouting(event, {
    mappingEnabled: options.mpeMappingEnabled,
    zone: options.mpeZone,
  });
  log.debug('Adapter entry: midi1_bytes', {
    kind: event.kind,
    mpeRole: mpe.role,
  });
  return {
    events: [event],
    adapter: 'midi1_bytes',
    mpe,
  };
}
