import assert from 'node:assert/strict';
import test from 'node:test';

import {
  MOTIF_RECONCILE_WARNING_DEFINITION_REMOVED,
  MOTIF_RECONCILE_WARNING_OCCURRENCE_PRUNED,
  nextMotifLabel,
  reconcileMotifsForRemovedEventIds,
  validateMotifDefinitions,
} from './compositionMotifs.js';

function compositionWithNotes() {
  return {
    schema_version: 'composition.v2',
    tracks: [
      {
        id: 'melody-1',
        role: 'melody',
        is_drum: false,
        events: [
          { id: 'n1', start_tick: 0, duration_ticks: 480, pitch: 'C4' },
          { id: 'n2', start_tick: 480, duration_ticks: 480, pitch: 'D4' },
          { id: 'n3', start_tick: 960, duration_ticks: 480, pitch: 'E4' },
          { id: 'n4', start_tick: 1440, duration_ticks: 480, pitch: 'F4' },
        ],
      },
    ],
    motifs: [
      {
        id: 'motif-a',
        label: 'Motif A',
        occurrences: [
          {
            id: 'occ-orig',
            track_id: 'melody-1',
            event_ids: ['n1', 'n2', 'n3'],
            relationship: 'original',
          },
          {
            id: 'occ-repeat',
            track_id: 'melody-1',
            event_ids: ['n2', 'n3', 'n4'],
            relationship: 'repeat',
          },
        ],
      },
    ],
  };
}

test('validateMotifDefinitions accepts empty and valid motifs', () => {
  assert.equal(validateMotifDefinitions({ motifs: undefined }).valid, true);
  assert.equal(validateMotifDefinitions({ motifs: [] }).valid, true);
  assert.equal(validateMotifDefinitions(compositionWithNotes()).valid, true);
});

test('nextMotifLabel increments alphabetically', () => {
  assert.equal(nextMotifLabel([]), 'Motif A');
  assert.equal(nextMotifLabel([{ label: 'Motif A' }]), 'Motif B');
});

test('reconcileMotifsForRemovedEventIds prunes occurrences and definitions', () => {
  const pruned = reconcileMotifsForRemovedEventIds(compositionWithNotes().motifs, ['n4']);
  assert.equal(pruned.motifs.length, 1);
  assert.deepEqual(
    pruned.motifs[0].occurrences.map((item) => item.id),
    ['occ-orig'],
  );
  assert.equal(pruned.warnings[0].code, MOTIF_RECONCILE_WARNING_OCCURRENCE_PRUNED);

  const removed = reconcileMotifsForRemovedEventIds(compositionWithNotes().motifs, ['n1']);
  assert.equal(removed.motifs.length, 0);
  assert.ok(
    removed.warnings.some((item) => item.code === MOTIF_RECONCILE_WARNING_DEFINITION_REMOVED),
  );
});
