import assert from 'node:assert/strict';
import test from 'node:test';

import { applyRealizeCandidate, buildRealizeSeedPatch } from './applyRealize.js';

const ARRANGEMENT_ENVELOPE = {
  intent: 'add_accompaniment',
  surface: 'arrangement',
  operation: 'add_accompaniment',
  preview: {
    operation: 'add_accompaniment',
    edit_source_fingerprint: 'a'.repeat(32),
    catalog_fingerprint: 'b'.repeat(32),
    candidates: [
      {
        candidate_id: 'cand_arr_accomp_01',
        operation: 'add_accompaniment',
        edit_source_fingerprint: 'a'.repeat(32),
        catalog_fingerprint: 'b'.repeat(32),
        candidate_fingerprint: 'c'.repeat(32),
        before_inventory: [
          {
            track_id: 't-melody',
            instrument: 'Acoustic Grand Piano',
            role: 'melody',
            midi_program: 0,
            event_count: 4,
            part_id: 'before-melody',
          },
        ],
        after_inventory: [
          {
            track_id: 't-melody',
            instrument: 'Acoustic Grand Piano',
            role: 'melody',
            midi_program: 0,
            event_count: 4,
            part_id: 'after-melody',
          },
          {
            track_id: 't-harmony',
            instrument: 'Acoustic Grand Piano',
            role: 'harmony',
            midi_program: 0,
            event_count: 2,
            part_id: 'after-piano-harmony',
          },
        ],
        composition: { schema_version: 'composition.v2', tracks: [] },
      },
    ],
  },
};

const HARMONY_ENVELOPE = {
  intent: 'reharmonize_selection',
  surface: 'harmony',
  operation: 'reharmonize',
  preview: {
    content_policy: 'preserve_harmony_adapt_melody',
    start_tick: 0,
    end_tick: 15360,
    recommended_target_track_ids: ['t-melody'],
    candidates: [
      {
        candidate_id: 'harmony-abcdef0123456789',
        composition: { schema_version: 'composition.v2', tracks: [], harmony: [{ chord: 'C' }] },
        base_fingerprint: 'd'.repeat(32),
        proposal_fingerprint: 'e'.repeat(32),
        harmony_changes: [],
        track_changes: [],
      },
    ],
  },
};

test('buildRealizeSeedPatch seeds arrangement operation and instrumentation', () => {
  const seeded = buildRealizeSeedPatch(ARRANGEMENT_ENVELOPE, 'cand_arr_accomp_01', {
    compositionRevision: 'rev-1',
  });
  assert.equal(seeded.surface, 'arrangement');
  assert.equal(seeded.patch.arrangementStatus, 'ready');
  assert.equal(seeded.patch.arrangementOperation, 'add_accompaniment');
  assert.deepEqual(seeded.patch.arrangementSourceTrackIds, ['t-melody']);
  assert.equal(seeded.patch.arrangementInstrumentationBefore.length, 1);
  assert.equal(seeded.patch.arrangementInstrumentationAfter.length, 2);
  assert.equal(seeded.patch.arrangementInstrumentationAfter[1].role, 'harmony');
  assert.equal(seeded.patch.arrangementInstrumentationAfter[1].instrument_id, 'acoustic_grand_piano');
  assert.equal(seeded.patch.arrangementSelectedCandidateId, 'cand_arr_accomp_01');
  assert.equal(seeded.patch.arrangementBaseRevision, 'rev-1');
  assert.equal(seeded.patch.arrangementEditSourceFingerprint, 'a'.repeat(32));
});

test('buildRealizeSeedPatch seeds harmony surface for reharmonize', () => {
  const seeded = buildRealizeSeedPatch(HARMONY_ENVELOPE, 'harmony-abcdef0123456789', {
    compositionRevision: 'rev-h',
  });
  assert.equal(seeded.surface, 'harmony');
  assert.equal(seeded.patch.reharmonizeStatus, 'ready');
  assert.equal(seeded.patch.reharmonizeContentPolicy, 'preserve_harmony_adapt_melody');
  assert.equal(seeded.patch.reharmonizeOperation, 'reharmonize');
  assert.equal(seeded.patch.reharmonizeEngine, 'deterministic');
  assert.equal(seeded.patch.reharmonizeBaseRevision, 'rev-h');
  assert.deepEqual(seeded.patch.reharmonizeTargetTrackIds, ['t-melody']);
  assert.equal(seeded.patch.reharmonizeCandidate.schema_version, 'composition.v2');
});

test('applyRealizeCandidate seeds then calls arrangement Apply', async () => {
  const patches = [];
  let applied = false;
  const ok = await applyRealizeCandidate(ARRANGEMENT_ENVELOPE, 'cand_arr_accomp_01', {
    getStore: () => ({ compositionRevision: 'rev-1' }),
    setState: (patch) => patches.push(patch),
    applyArrangement: async () => {
      applied = true;
      return true;
    },
  });
  assert.equal(ok, true);
  assert.equal(applied, true);
  assert.equal(patches[0].arrangementOperation, 'add_accompaniment');
  assert.ok(patches[0].arrangementInstrumentationAfter.length >= 1);
});

test('applyRealizeCandidate seeds then calls reharmonize Apply', async () => {
  const patches = [];
  let applied = false;
  const ok = await applyRealizeCandidate(HARMONY_ENVELOPE, null, {
    getStore: () => ({ compositionRevision: 'rev-h' }),
    setState: (patch) => patches.push(patch),
    applyReharmonize: async () => {
      applied = true;
      return true;
    },
  });
  assert.equal(ok, true);
  assert.equal(applied, true);
  assert.equal(patches[0].reharmonizeStatus, 'ready');
  assert.equal(patches[0].reharmonizeContentPolicy, 'preserve_harmony_adapt_melody');
});

test('applyRealizeCandidate returns false when candidate missing', async () => {
  const ok = await applyRealizeCandidate(ARRANGEMENT_ENVELOPE, 'missing', {
    getStore: () => ({ compositionRevision: 'rev-1' }),
    setState: () => {},
    applyArrangement: async () => true,
  });
  assert.equal(ok, false);
});
