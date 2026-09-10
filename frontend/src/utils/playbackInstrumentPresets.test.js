import assert from 'node:assert/strict';
import test from 'node:test';

import {
  SYNTH_PRESET_CATALOG,
  createInstrumentAdapterStub,
  getSynthPreset,
  resolveSynthFallbackPreset,
  validateInstrumentAdapter,
} from './playbackInstrumentPresets.js';

test('synth preset catalog covers core families', () => {
  assert.ok(getSynthPreset('piano_keyboard'));
  assert.ok(getSynthPreset('bass_synth'));
  assert.ok(getSynthPreset('strings_pad'));
  assert.ok(getSynthPreset('guitar_pluck'));
  assert.ok(getSynthPreset('brass'));
  assert.ok(getSynthPreset('woodwind_lead'));
  assert.ok(getSynthPreset('mallet'));
  assert.ok(getSynthPreset('drums_basic'));
  assert.equal(Object.keys(SYNTH_PRESET_CATALOG).length, 8);
});

test('resolveSynthFallbackPreset follows track strategy', () => {
  const piano = resolveSynthFallbackPreset({ instrument: 'piano', midi_program: 0 });
  assert.equal(piano.presetId, 'piano_keyboard');
  assert.equal(piano.strategy.id, 'piano_keyboard');

  const drums = resolveSynthFallbackPreset({ instrument: 'drums', is_drum: true, channel: 10 });
  assert.equal(drums.presetId, 'drums_basic');
});

test('instrument adapter stub satisfies contract with note-id attacks', async () => {
  const adapter = createInstrumentAdapterStub({ profileId: 'piano_keyboard' });
  assert.deepEqual(validateInstrumentAdapter(adapter), { ok: true, missing: [] });

  const before = adapter.getStatus();
  assert.equal(before.ready, false);

  const prepared = await adapter.prepare();
  assert.equal(prepared.ready, true);

  adapter.connect({ id: 'bus' });
  adapter.attack('n1', 'C4', 0.8);
  adapter.release('n1');
  adapter.releaseAll();
  adapter.dispose();
  assert.equal(adapter.getStatus().ready, false);
  assert.equal(adapter.getStatus().reasonCode, 'disposed');
});

test('validateInstrumentAdapter reports missing methods', () => {
  const result = validateInstrumentAdapter({ prepare() {} });
  assert.equal(result.ok, false);
  assert.ok(result.missing.includes('attack'));
});
