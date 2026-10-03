import assert from 'node:assert/strict';
import test from 'node:test';

import { buildArdourScopeSummary } from './scopeSummary.js';

test('buildArdourScopeSummary prefers manifest bars when preview alignment is present', () => {
  const summary = buildArdourScopeSummary({
    companionStatus: {
      connection_state: 'connected',
      locate_samples: 48000,
      sample_rate: 48000,
      selected_strip_name: 'Idea',
      transport_playing: false,
    },
    exchangePreview: {
      alignment: {
        start_bar: 1,
        bar_count: 8,
        tempo_bpm: 120,
        time_signature: '4/4',
      },
      manifest: {
        track_name: 'Idea',
        tempo_bpm: 120,
        time_signature: '4/4',
      },
    },
  });
  assert.equal(summary.source, 'manifest');
  assert.equal(summary.barsLabel, 'bars 1–8');
  assert.equal(summary.tempoBpm, 120);
  assert.equal(summary.timeSignature, '4/4');
  assert.equal(summary.trackName, 'Idea');
  assert.equal(summary.hasPreviewAlignment, true);
  assert.equal(summary.exportSelectionCta, null);
  assert.equal(summary.locateDisplay, '00:00:01');
});

test('buildArdourScopeSummary falls back to OSC strip and Lua export CTA without preview', () => {
  const summary = buildArdourScopeSummary({
    companionStatus: {
      connection_state: 'connected',
      locate_samples: 0,
      sample_rate: 48000,
      selected_ssid: 3,
      selected_strip_name: 'Synth',
      transport_playing: true,
    },
    exchangePreview: null,
  });
  assert.equal(summary.source, 'osc');
  assert.equal(summary.stripName, 'Synth');
  assert.equal(summary.playing, true);
  assert.equal(summary.hasPreviewAlignment, false);
  assert.match(summary.exportSelectionCta, /Lua/i);
});
