import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  isNeuralAudioDownloadReady,
  neuralAudioFidelityDisclaimer,
} from './neuralAudioRenderUi.js';

describe('neuralAudioRenderUi', () => {
  it('exposes generative not-note-perfect disclaimer', () => {
    const text = neuralAudioFidelityDisclaimer('generative');
    assert.match(text, /not note-perfect/i);
  });

  it('exposes neural instrument approximate disclaimer', () => {
    const text = neuralAudioFidelityDisclaimer('neural_instrument');
    assert.match(text, /approximate/i);
  });

  it('gates download on complete status', () => {
    assert.equal(isNeuralAudioDownloadReady({ id: 'r1', status: 'running' }), false);
    assert.equal(isNeuralAudioDownloadReady({ id: 'r1', status: 'complete' }), true);
  });
});
