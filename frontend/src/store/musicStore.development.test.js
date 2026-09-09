import assert from 'node:assert/strict';
import test from 'node:test';

import axios from 'axios';
import { useMusicStore } from './musicStore.js';

function installAxiosStub(handler) {
  const previous = axios.defaults.adapter;
  axios.defaults.adapter = handler;
  return () => {
    axios.defaults.adapter = previous;
  };
}

function sixteenBarComposition() {
  const events = [];
  for (let bar = 0; bar < 16; bar += 1) {
    events.push({
      id: `n-${bar}`,
      pitch: 'C4',
      start_tick: bar * 1920,
      duration_ticks: 480,
      velocity: 80,
    });
  }
  return {
    schema_version: 'composition.v2',
    tempo: 100,
    key: 'C major',
    time_signature: '4/4',
    ticks_per_quarter: 480,
    bar_count: 16,
    duration_ticks: 16 * 1920,
    sections: [{
      id: 'a',
      type: 'verse',
      start_bar: 1,
      bar_count: 16,
      start_tick: 0,
      duration_ticks: 16 * 1920,
    }],
    tracks: [{
      id: 'melody-1',
      name: 'Melody',
      instrument: 'piano',
      role: 'melody',
      midi_program: 0,
      channel: 1,
      is_drum: false,
      volume: 100,
      pan: 0,
      expression: 127,
      events,
      dynamic_marks: [],
      sustain_pedals: [],
      automation: [],
    }],
    harmony: [],
    tempo_changes: [],
    time_signature_changes: [],
    key_changes: [],
    markers: [],
    motifs: [],
  };
}

function candidateFrom(base, { candidateId = 'dev-cand-aaaa', fingerprint = 'b'.repeat(64) } = {}) {
  const composition = structuredClone(base);
  composition.bar_count = 24;
  composition.duration_ticks = 24 * 1920;
  composition.sections.push({
    id: 'cont',
    type: 'verse',
    start_bar: 17,
    bar_count: 8,
    start_tick: 16 * 1920,
    duration_ticks: 8 * 1920,
  });
  for (let bar = 16; bar < 24; bar += 1) {
    composition.tracks[0].events.push({
      id: `n-${bar}`,
      pitch: 'E4',
      start_tick: bar * 1920,
      duration_ticks: 480,
      velocity: 80,
    });
  }
  return {
    candidate_id: candidateId,
    candidate_fingerprint: fingerprint,
    edit_source_fingerprint: 'a'.repeat(64),
    algorithm_version: 'composition.development.v1',
    operation: 'continue',
    development_intent: 'continue',
    variation_strength: 'balanced',
    composition,
    source_range: { start_bar: 1, end_bar: 16, start_tick: 0, end_tick: 16 * 1920 },
    output_range: { start_bar: 17, end_bar: 24, start_tick: 16 * 1920, end_tick: 24 * 1920 },
    section_changes: [],
    harmony_changes: [],
    motif_changes: [],
    track_changes: [],
    preservation: [{ code: 'immutable_prefix', required: true, passed: true }],
    identity_diagnostics: [],
    provider: 'fake',
    model: 'fake-deterministic',
    warning_codes: [],
  };
}

test('development preview is ephemeral and apply mutates with one history entry', async (t) => {
  const base = sixteenBarComposition();
  const restore = installAxiosStub(async (config) => {
    assert.equal(config.url, '/composition/development/preview');
    return {
      data: {
        edit_source_fingerprint: 'a'.repeat(64),
        algorithm_version: 'composition.development.v1',
        operation: 'continue',
        development_intent: 'continue',
        variation_strength: 'balanced',
        requested_candidate_count: 2,
        candidates: [
          candidateFrom(base, { candidateId: 'dev-cand-one1', fingerprint: 'b'.repeat(64) }),
          candidateFrom(base, { candidateId: 'dev-cand-two2', fingerprint: 'c'.repeat(64) }),
        ],
        warning_codes: [],
        provider: 'fake',
        model: 'fake-deterministic',
      },
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    };
  });
  t.after(restore);

  useMusicStore.setState({
    editedMusicJson: structuredClone(base),
    compositionRevision: 'rev-base',
    noteEditUndoStack: [],
    noteEditRedoStack: [],
    developmentOperation: 'continue',
    developmentIntent: 'continue',
    developmentStrength: 'balanced',
    developmentOutputBars: 8,
    developmentCandidateCount: 2,
    developmentSourceStartBar: 1,
    developmentSourceEndBar: 16,
    developmentStatus: 'idle',
    developmentCandidates: [],
    developmentSelectedCandidateId: null,
    developmentAuditionActive: false,
    selectedProvider: 'fake',
    selectedModel: 'fake-deterministic',
  });

  const before = structuredClone(useMusicStore.getState().editedMusicJson);
  const ok = await useMusicStore.getState().startDevelopmentPreview();
  assert.equal(ok, true);
  assert.deepEqual(useMusicStore.getState().editedMusicJson, before);
  assert.equal(useMusicStore.getState().developmentCandidates.length, 2);
  assert.equal(useMusicStore.getState().developmentSelectedCandidateId, 'dev-cand-one1');

  useMusicStore.getState().selectDevelopmentCandidate('dev-cand-two2');
  assert.equal(useMusicStore.getState().developmentSelectedCandidateId, 'dev-cand-two2');
  assert.deepEqual(useMusicStore.getState().editedMusicJson, before);

  // Fingerprints in stub won't match local recompute — expect apply to fail verification.
  const applied = await useMusicStore.getState().applySelectedDevelopmentCandidate();
  assert.equal(applied, false);
  assert.deepEqual(useMusicStore.getState().editedMusicJson, before);
});

test('development selection and discard do not dirty history', () => {
  useMusicStore.setState({
    editedMusicJson: sixteenBarComposition(),
    compositionRevision: 'rev-1',
    noteEditUndoStack: [{ editedMusicJson: sixteenBarComposition() }],
    developmentCandidates: [candidateFrom(sixteenBarComposition())],
    developmentSelectedCandidateId: 'dev-cand-aaaa',
    developmentStatus: 'ready',
    developmentAuditionActive: true,
  });
  const undoLen = useMusicStore.getState().noteEditUndoStack.length;
  useMusicStore.getState().selectDevelopmentCandidate('dev-cand-aaaa');
  useMusicStore.getState().discardDevelopmentCandidates();
  assert.equal(useMusicStore.getState().noteEditUndoStack.length, undoLen);
  assert.equal(useMusicStore.getState().developmentCandidates.length, 0);
  assert.equal(useMusicStore.getState().developmentAuditionActive, false);
});
