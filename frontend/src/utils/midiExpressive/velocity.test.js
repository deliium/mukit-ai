import assert from 'node:assert/strict';
import test from 'node:test';
import {
  MIDI1_VELOCITY_U16_MAX,
  VELOCITY_U16_MAX,
} from './constants.js';
import {
  degradeVelocityU16ToMidi7,
  promoteMidi1VelocityToU16,
  promoteUmpVelocityToU16,
  velocityU16MatchesDegraded,
} from './velocity.js';

test('promotes MIDI 1.0 with << 9 and caps at 65024', () => {
  assert.equal(promoteMidi1VelocityToU16(0), 0);
  assert.equal(promoteMidi1VelocityToU16(1), 512);
  assert.equal(promoteMidi1VelocityToU16(64), 64 << 9);
  assert.equal(promoteMidi1VelocityToU16(127), MIDI1_VELOCITY_U16_MAX);
  assert.equal(promoteMidi1VelocityToU16(200), MIDI1_VELOCITY_U16_MAX);
});

test('degrades with /512 into 1..127 for attacks', () => {
  assert.equal(degradeVelocityU16ToMidi7(0), 0);
  assert.equal(degradeVelocityU16ToMidi7(512), 1);
  assert.equal(degradeVelocityU16ToMidi7(64 << 9), 64);
  assert.equal(degradeVelocityU16ToMidi7(MIDI1_VELOCITY_U16_MAX), 127);
  assert.equal(degradeVelocityU16ToMidi7(VELOCITY_U16_MAX), 127);
});

test('round-trips MIDI 1.0 promote → degrade for all midi7', () => {
  for (let v = 1; v <= 127; v += 1) {
    const u16 = promoteMidi1VelocityToU16(v);
    assert.equal(degradeVelocityU16ToMidi7(u16), v);
    assert.equal(velocityU16MatchesDegraded(u16, v), true);
  }
});

test('UMP pass-through does not apply << 9', () => {
  assert.equal(promoteUmpVelocityToU16(1000), 1000);
  assert.equal(promoteUmpVelocityToU16(VELOCITY_U16_MAX), VELOCITY_U16_MAX);
  assert.equal(degradeVelocityU16ToMidi7(promoteUmpVelocityToU16(32000)), 63);
});
