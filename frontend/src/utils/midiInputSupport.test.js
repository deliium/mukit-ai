import assert from 'node:assert/strict';
import test from 'node:test';
import {
  isWebMidiSupported,
  midiAccessFailureReason,
  MIDI_SUPPORT_REASONS,
  probeWebMidiSupport,
} from './midiInputSupport.js';

test('probeWebMidiSupport reports available when secure + API present', () => {
  const result = probeWebMidiSupport({
    isSecureContext: true,
    hasRequestMidiAccess: true,
  });
  assert.deepEqual(result, {
    supported: true,
    reason: MIDI_SUPPORT_REASONS.AVAILABLE,
  });
  assert.equal(isWebMidiSupported({
    isSecureContext: true,
    hasRequestMidiAccess: true,
  }), true);
});

test('probeWebMidiSupport reports insecure_context before unsupported', () => {
  const result = probeWebMidiSupport({
    isSecureContext: false,
    hasRequestMidiAccess: true,
  });
  assert.equal(result.reason, MIDI_SUPPORT_REASONS.INSECURE_CONTEXT);
  assert.equal(result.supported, false);
});

test('probeWebMidiSupport reports unsupported when API missing', () => {
  const result = probeWebMidiSupport({
    isSecureContext: true,
    hasRequestMidiAccess: false,
  });
  assert.equal(result.reason, MIDI_SUPPORT_REASONS.UNSUPPORTED);
  assert.equal(result.supported, false);
});

test('probeWebMidiSupport never throws on missing globals', () => {
  assert.doesNotThrow(() => probeWebMidiSupport());
});

test('midiAccessFailureReason maps permission and support errors', () => {
  assert.equal(
    midiAccessFailureReason({ name: 'NotAllowedError' }),
    MIDI_SUPPORT_REASONS.PERMISSION_DENIED,
  );
  assert.equal(
    midiAccessFailureReason({ name: 'SecurityError' }),
    MIDI_SUPPORT_REASONS.PERMISSION_DENIED,
  );
  assert.equal(
    midiAccessFailureReason({ name: 'NotSupportedError' }),
    MIDI_SUPPORT_REASONS.UNSUPPORTED,
  );
  assert.equal(
    midiAccessFailureReason(new Error('boom')),
    MIDI_SUPPORT_REASONS.UNSUPPORTED,
  );
});
