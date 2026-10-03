import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { stageEnsembleSurvivorAsGenerationCandidate } from './compositionCandidateLifecycle.js';

const tinyComposition = {
  schema_version: 'composition.v2',
  tempo: 120,
  key: 'C major',
  time_signature: '4/4',
  ticks_per_quarter: 480,
  duration_ticks: 3840,
  bar_count: 2,
  sections: [
    {
      id: 's1',
      type: 'verse',
      label: 'Verse',
      start_bar: 1,
      bar_count: 2,
      start_tick: 0,
      duration_ticks: 3840,
    },
  ],
  tracks: [
    {
      id: 'melody-1',
      name: 'Melody',
      instrument: 'piano',
      role: 'melody',
      midi_program: 0,
      channel: 1,
      is_drum: false,
      volume: 100,
      pan: 0,
      events: [
        {
          type: 'note',
          id: 'n1',
          pitch: 'C4',
          start_tick: 0,
          duration_ticks: 480,
          velocity: 80,
        },
      ],
    },
    {
      id: 'bass-1',
      name: 'Bass',
      instrument: 'bass',
      role: 'bass',
      midi_program: 32,
      channel: 2,
      is_drum: false,
      volume: 100,
      pan: 0,
      events: [
        {
          type: 'note',
          id: 'n2',
          pitch: 'C2',
          start_tick: 0,
          duration_ticks: 480,
          velocity: 80,
        },
      ],
    },
    {
      id: 'harmony-1',
      name: 'Harmony',
      instrument: 'piano',
      role: 'harmony',
      midi_program: 0,
      channel: 3,
      is_drum: false,
      volume: 90,
      pan: 0,
      events: [
        {
          type: 'note',
          id: 'n3',
          pitch: 'E3',
          start_tick: 0,
          duration_ticks: 480,
          velocity: 70,
        },
      ],
    },
  ],
  harmony: [],
  markers: [],
  key_changes: [],
  motifs: [],
};

describe('stageEnsembleSurvivorAsGenerationCandidate', () => {
  it('stamps ensemble provenance and does not imply Apply', async () => {
    const survivor = {
      candidate_id: 'ens_abcdef0123456789',
      composition: tinyComposition,
      provenance: {
        model_id: 'fake:symbolic-sparse',
        seed: 2,
        strategy: 'parallel_once',
        attempt_ordinal: 1,
        engine: 'symbolic_composer',
      },
      validation_ok: true,
    };
    const report = {
      suggested_candidate_id: 'ens_ffffffffffffff00',
      ranking_applied: true,
      policy: {
        model_ids: [
          'fake:symbolic-tiny',
          'fake:symbolic-sparse',
          'fake:symbolic-dense',
        ],
        strategy: 'parallel_once',
      },
    };
    const envelope = await stageEnsembleSurvivorAsGenerationCandidate({
      survivor,
      report,
      workingComposition: tinyComposition,
      promptSnapshot: { genre: 'ambient' },
    });
    assert.equal(envelope.operation_type, 'generate-apply');
    assert.equal(envelope.status, 'ready');
    assert.equal(envelope.model_id, 'fake:symbolic-sparse');
    assert.equal(envelope.pipeline_id, 'ensemble_arbitration');
    assert.equal(envelope.generation_parameters.pipeline_id, 'ensemble_arbitration');
    assert.equal(envelope.generation_parameters.musical_quality_claim, false);
    assert.equal(envelope.generation_parameters.suggested_candidate_id, 'ens_ffffffffffffff00');
    assert.notEqual(envelope.candidate_id, report.suggested_candidate_id);
    assert.ok(Array.isArray(envelope.generation_parameters.sibling_model_ids));
    // Staging helper never mutates working score — Apply is a separate step.
    assert.equal(envelope.composition.key, 'C major');
  });
});
