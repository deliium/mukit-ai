/**
 * MPE zone helpers — channels are 0-based in adapters; zone config is 1-based.
 */

import { MPE_DEFAULT_ZONE } from './constants.js';

/**
 * @param {{
 *   master_channel?: number,
 *   member_channel_low?: number,
 *   member_channel_high?: number,
 * } | null | undefined} zone
 * @returns {{
 *   masterChannel0: number,
 *   memberLow0: number,
 *   memberHigh0: number,
 * }}
 */
export function normalizeMpeZoneToZeroBased(zone) {
  const master1 = Number.isInteger(Number(zone?.master_channel))
    ? Number(zone.master_channel)
    : MPE_DEFAULT_ZONE.master_channel;
  const low1 = Number.isInteger(Number(zone?.member_channel_low))
    ? Number(zone.member_channel_low)
    : MPE_DEFAULT_ZONE.member_channel_low;
  const high1 = Number.isInteger(Number(zone?.member_channel_high))
    ? Number(zone.member_channel_high)
    : MPE_DEFAULT_ZONE.member_channel_high;
  return {
    masterChannel0: Math.min(15, Math.max(0, master1 - 1)),
    memberLow0: Math.min(15, Math.max(0, low1 - 1)),
    memberHigh0: Math.min(15, Math.max(0, high1 - 1)),
  };
}

/**
 * @param {number} channel0
 * @param {ReturnType<typeof normalizeMpeZoneToZeroBased>} zone0
 * @returns {'master' | 'member' | 'outside'}
 */
export function classifyMpeChannel(channel0, zone0) {
  const ch = Number(channel0);
  if (!Number.isInteger(ch) || ch < 0 || ch > 15) {
    return 'outside';
  }
  if (ch === zone0.masterChannel0) {
    return 'master';
  }
  if (ch >= zone0.memberLow0 && ch <= zone0.memberHigh0) {
    return 'member';
  }
  return 'outside';
}

/**
 * Whether an expressive event should attach to a per-note MPE member voice.
 * Master-channel CCs (e.g. sustain) stay global.
 *
 * @param {import('./schemas.js').MidiExpressiveEventV1} event
 * @param {{
 *   mappingEnabled?: boolean,
 *   zone?: { master_channel: number, member_channel_low: number, member_channel_high: number },
 * }} [options]
 * @returns {{
 *   mappingEnabled: boolean,
 *   role: 'master' | 'member' | 'outside' | 'legacy',
 *   attachToOpenNoteOnChannel: boolean,
 * }}
 */
export function resolveMpeEventRouting(event, options = {}) {
  const mappingEnabled = options.mappingEnabled === true;
  if (!mappingEnabled) {
    return {
      mappingEnabled: false,
      role: 'legacy',
      attachToOpenNoteOnChannel: false,
    };
  }
  const zone0 = normalizeMpeZoneToZeroBased(options.zone);
  const channel = 'channel' in event && Number.isInteger(event.channel)
    ? event.channel
    : -1;
  const role = classifyMpeChannel(channel, zone0);
  const kind = event.kind;
  const attachToOpenNoteOnChannel =
    role === 'member'
    && (kind === 'pitch_bend'
      || kind === 'pressure'
      || (kind === 'control_change' && event.controller !== 64));
  return {
    mappingEnabled: true,
    role,
    attachToOpenNoteOnChannel,
  };
}
