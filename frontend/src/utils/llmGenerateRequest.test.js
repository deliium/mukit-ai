import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { buildLlmRequest } from './llmGenerateRequest.js';

const basePrompt = {
  genre: 'classical',
  mood: 'calm',
  key: 'C major',
  time_signature: '4/4',
  tempo_min: '100',
  tempo_max: '120',
  instruments: 'piano, bass',
  sections: '',
  complexity: 'moderate',
  duration_bars: '8',
  instructions: '',
};

describe('buildLlmRequest pipeline options', () => {
  it('defaults to llm_only without seed', () => {
    const request = buildLlmRequest(basePrompt, 'fake', 'fake-v1');
    assert.equal(request.options.pipeline, 'llm_only');
    assert.equal(request.options.seed, undefined);
  });

  it('sets hybrid pipeline and numeric seed', () => {
    const request = buildLlmRequest(basePrompt, 'fake', 'fake-v1', {
      pipeline: 'hybrid_plan_symbolic',
      seed: '42',
    });
    assert.equal(request.options.pipeline, 'hybrid_plan_symbolic');
    assert.equal(request.options.seed, 42);
  });

  it('omits invalid hybrid seed', () => {
    const request = buildLlmRequest(basePrompt, 'fake', 'fake-v1', {
      pipeline: 'hybrid_plan_symbolic',
      seed: '',
    });
    assert.equal(request.options.pipeline, 'hybrid_plan_symbolic');
    assert.equal(request.options.seed, undefined);
  });

  it('defaults profile_strength to off without profile_id', () => {
    const request = buildLlmRequest(basePrompt, 'fake', 'fake-v1');
    assert.equal(request.profile_strength, 'off');
    assert.equal(request.profile_id, undefined);
  });

  it('includes profile_id only when strength is not off', () => {
    const off = buildLlmRequest(basePrompt, 'fake', 'fake-v1', {
      profileId: 'prof_x',
      profileStrength: 'off',
    });
    assert.equal(off.profile_strength, 'off');
    assert.equal(off.profile_id, undefined);

    const on = buildLlmRequest(basePrompt, 'fake', 'fake-v1', {
      profileId: 'prof_x',
      profileStrength: 'normal',
    });
    assert.equal(on.profile_strength, 'normal');
    assert.equal(on.profile_id, 'prof_x');
    assert.equal(on.prompt.key, 'C major');
    assert.deepEqual(on.prompt.instruments, ['piano', 'bass']);
  });
});
