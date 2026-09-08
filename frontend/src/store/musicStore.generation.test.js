import assert from 'node:assert/strict';
import test from 'node:test';

import { migrateV1ToV2 } from '../utils/compositionVersion.js';
import { useMusicStore } from './musicStore.js';

function resetGenerationState(overrides = {}) {
  useMusicStore.setState({
    generationStatus: 'idle',
    uiError: '',
    warnings: [],
    selectedProvider: 'openai',
    selectedModel: 'gpt-test',
    availableLlmModels: [
      { provider: 'openai', model: 'gpt-test', display_name: 'OpenAI test', is_default: true },
    ],
    llmModelsLoaded: true,
    aiEditStatus: 'idle',
    aiEditError: '',
    aiEditWarnings: [],
    aiEditStartBar: 1,
    aiEditEndBar: 2,
    aiEditInstruction: 'make brighter',
    editedMusicJson: migrateV1ToV2({
      schema_version: 'composition.v1',
      tempo: 100,
      key: 'C major',
      time_signature: '4/4',
      ticks_per_quarter: 480,
      bar_count: 2,
      duration_ticks: 3840,
      sections: [{ type: 'intro', start_bar: 1, bar_count: 2, start_tick: 0, duration_ticks: 3840 }],
      tracks: [
        {
          id: 'melody-1',
          name: 'Melody',
          instrument: 'piano',
          role: 'melody',
          midi_program: 0,
          channel: 1,
          events: [{ type: 'note', id: 'n1', pitch: 'C4', start_tick: 0, duration_ticks: 480, velocity: 90 }],
        },
      ],
      harmony: [{ bar: 1, chord: 'C' }],
    }),
    ...overrides,
  });
}

test('startGeneration is ignored while already loading', () => {
  resetGenerationState({ generationStatus: 'loading' });
  const started = useMusicStore.getState().startGeneration();
  assert.equal(started, false);
  assert.equal(useMusicStore.getState().generationStatus, 'loading');
});

test('startGeneration transitions idle to loading', () => {
  resetGenerationState({ generationStatus: 'idle' });
  const started = useMusicStore.getState().startGeneration();
  assert.equal(started, true);
  assert.equal(useMusicStore.getState().generationStatus, 'loading');
  assert.equal(useMusicStore.getState().uiError, '');
});

test('empty models leave llmReady false after setAvailableLlmModels', () => {
  resetGenerationState({ availableLlmModels: [{ provider: 'openai', model: 'x' }], llmModelsLoaded: false });
  useMusicStore.getState().setAvailableLlmModels([]);
  const state = useMusicStore.getState();
  assert.equal(state.llmModelsLoaded, true);
  assert.equal(state.availableLlmModels.length, 0);
});

test('startAiEdit is ignored while already loading', () => {
  resetGenerationState({ aiEditStatus: 'loading' });
  const started = useMusicStore.getState().startAiEdit();
  assert.equal(started, false);
  assert.equal(useMusicStore.getState().aiEditStatus, 'loading');
});
