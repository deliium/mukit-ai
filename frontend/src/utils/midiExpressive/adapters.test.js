import assert from 'node:assert/strict';
import test from 'node:test';
import { adaptIncomingMidi } from './adaptMessage.js';
import { probeMidiCapability } from './capabilityProbe.js';
import {
  MIDI_CAPABILITY_REASON_CODES,
  MIDI_TRANSPORT,
  MIDI1_VELOCITY_U16_MAX,
} from './constants.js';
import { adaptMidi1BytesToExpressive } from './midi1Bytes.js';
import { resolveMpeEventRouting } from './mpeZone.js';
import {
  buildSimulatedMidi2ChannelVoiceUmp,
  decodeUmpShipSubset,
} from './umpTransport.js';
import { UMP_MIDI2_OPCODE } from './constants.js';
import { degradeVelocityU16ToMidi7 } from './velocity.js';

test('capability probe defaults to midi1_bytes and ump_api_absent', () => {
  const cap = probeMidiCapability({
    webMidiEnv: { isSecureContext: true, hasRequestMidiAccess: true },
    hasUmpApi: false,
  });
  assert.equal(cap.transport, MIDI_TRANSPORT.MIDI1_BYTES);
  assert.equal(cap.high_res_velocity, false);
  assert.ok(cap.reason_codes.includes(MIDI_CAPABILITY_REASON_CODES.UMP_API_ABSENT));
  assert.equal(cap.mpe.eligible, true);
  assert.equal(cap.mpe.zone.master_channel, 1);
});

test('capability probe enables ump_experimental when stubbed present', () => {
  const cap = probeMidiCapability({
    webMidiEnv: { isSecureContext: true, hasRequestMidiAccess: true },
    hasUmpApi: true,
  });
  assert.equal(cap.transport, MIDI_TRANSPORT.UMP_EXPERIMENTAL);
  assert.equal(cap.high_res_velocity, true);
});

test('midi1 adapter promotes note-on velocity and parses pitch bend / pressure / CC', () => {
  const on = adaptMidi1BytesToExpressive([0x90, 60, 127]);
  assert.equal(on.kind, 'note_on');
  assert.equal(on.velocity_u16, MIDI1_VELOCITY_U16_MAX);
  assert.equal(degradeVelocityU16ToMidi7(on.velocity_u16), 127);

  const bend = adaptMidi1BytesToExpressive([0xe0, 0x00, 0x40]);
  assert.equal(bend.kind, 'pitch_bend');
  assert.ok(Math.abs(bend.bend) < 1e-9);

  const poly = adaptMidi1BytesToExpressive([0xa0, 60, 64]);
  assert.equal(poly.kind, 'pressure');
  assert.equal(poly.scope, 'poly');

  const ch = adaptMidi1BytesToExpressive([0xd1, 100]);
  assert.equal(ch.kind, 'pressure');
  assert.equal(ch.scope, 'channel');
  assert.equal(ch.channel, 1);

  const cc = adaptMidi1BytesToExpressive([0xb0, 1, 64]);
  assert.equal(cc.kind, 'control_change');
  assert.equal(cc.controller, 1);
});

test('legacyOnly ignores pitch bend and non-sustain CC', () => {
  const bend = adaptMidi1BytesToExpressive([0xe0, 0x00, 0x40], { legacyOnly: true });
  assert.equal(bend.kind, 'ignored');
  const sustain = adaptMidi1BytesToExpressive([0xb0, 64, 127], { legacyOnly: true });
  assert.equal(sustain.kind, 'control_change');
  assert.equal(sustain.sustain, true);
});

test('adaptIncomingMidi honors expressive disabled env', () => {
  const { events, adapter } = adaptIncomingMidi({
    data: [0xe0, 0x00, 0x60],
    expressiveEnv: { VITE_MIDI_EXPRESSIVE_ENABLED: 'false' },
  });
  assert.equal(adapter, 'legacy');
  assert.equal(events[0].kind, 'ignored');
});

test('MPE mapping attaches member bend/pressure to open-note channel', () => {
  const bend = adaptMidi1BytesToExpressive([0xe2, 0x00, 0x50]);
  const routing = resolveMpeEventRouting(bend, {
    mappingEnabled: true,
    zone: { master_channel: 1, member_channel_low: 2, member_channel_high: 16 },
  });
  assert.equal(routing.role, 'member');
  assert.equal(routing.attachToOpenNoteOnChannel, true);

  const masterSustain = adaptMidi1BytesToExpressive([0xb0, 64, 127]);
  const master = resolveMpeEventRouting(masterSustain, { mappingEnabled: true });
  assert.equal(master.role, 'master');
  assert.equal(master.attachToOpenNoteOnChannel, false);
});

test('UMP ship-subset note-on preserves 16-bit velocity', () => {
  const words = buildSimulatedMidi2ChannelVoiceUmp({
    opcode: UMP_MIDI2_OPCODE.NOTE_ON,
    channel: 0,
    data1: 60,
    velocityU16: 32000,
  });
  const events = decodeUmpShipSubset(words);
  assert.equal(events.length, 1);
  assert.equal(events[0].kind, 'note_on');
  assert.equal(events[0].velocity_u16, 32000);
  assert.equal(degradeVelocityU16ToMidi7(32000), 63);
});

test('UMP unsupported message type is ignored without throw', () => {
  const events = decodeUmpShipSubset([0x00000000]);
  assert.equal(events[0].kind, 'ignored');
  assert.equal(events[0].reason, 'ump_mt_unsupported');
});
