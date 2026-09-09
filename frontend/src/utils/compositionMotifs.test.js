import assert from 'node:assert/strict';
import test from 'node:test';

import {
  MOTIF_AUTHORING_WARNING_BAR_SPAN,
  MOTIF_AUTHORING_WARNING_COUNT,
  MOTIF_AUTHORING_WARNING_INCOMPLETE_TIE,
  MOTIF_RECONCILE_WARNING_DEFINITION_REMOVED,
  MOTIF_RECONCILE_WARNING_OCCURRENCE_PRUNED,
  applyMotifReconciliation,
  collectSelectedEventIdsInOrder,
  countDestinationOverlapEvents,
  nextMotifLabel,
  resolveMotifOccurrenceBarSpan,
  projectMotifUsagesForDisplay,
  reconcileMotifsForRemovedEventIds,
  resolveOccurrenceLocationLabels,
  validateMotifAuthoringSelection,
  validateMotifDefinitions,
} from './compositionMotifs.js';

function compositionWithNotes() {
  return {
    schema_version: 'composition.v2',
    tempo: 100,
    key: 'C major',
    time_signature: '4/4',
    ticks_per_quarter: 480,
    bar_count: 4,
    duration_ticks: 7680,
    sections: [
      {
        id: 'verse',
        type: 'verse',
        start_bar: 1,
        bar_count: 2,
        start_tick: 0,
        duration_ticks: 3840,
      },
      {
        id: 'chorus',
        type: 'chorus',
        start_bar: 3,
        bar_count: 2,
        start_tick: 3840,
        duration_ticks: 3840,
      },
    ],
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
          { id: 'n5', start_tick: 3840, duration_ticks: 480, pitch: 'G4' },
          { id: 'n6', start_tick: 4320, duration_ticks: 480, pitch: 'A4' },
          { id: 'n7', start_tick: 4800, duration_ticks: 480, pitch: 'B4' },
        ],
      },
      {
        id: 'drums-1',
        role: 'drums',
        is_drum: true,
        events: [
          { id: 'd1', start_tick: 0, duration_ticks: 480, pitch: 'C2' },
          { id: 'd2', start_tick: 480, duration_ticks: 480, pitch: 'C2' },
          { id: 'd3', start_tick: 960, duration_ticks: 480, pitch: 'C2' },
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

test('collectSelectedEventIdsInOrder returns canonical chronological ids', () => {
  const composition = compositionWithNotes();
  assert.deepEqual(
    collectSelectedEventIdsInOrder(composition, 'melody-1', ['n3', 'n1', 'n2']),
    ['n1', 'n2', 'n3'],
  );
});

test('validateMotifAuthoringSelection enforces count, track, and bar span', () => {
  const composition = compositionWithNotes();
  const valid = validateMotifAuthoringSelection(composition, {
    trackId: 'melody-1',
    eventIds: ['n1', 'n2', 'n3'],
  });
  assert.equal(valid.valid, true);
  assert.deepEqual(valid.eventIds, ['n1', 'n2', 'n3']);
  assert.equal(valid.startBar, 1);
  assert.equal(valid.endBar, 1);
  assert.equal(valid.sectionId, 'verse');

  const tooFew = validateMotifAuthoringSelection(composition, {
    trackId: 'melody-1',
    eventIds: ['n1', 'n2'],
  });
  assert.equal(tooFew.valid, false);
  assert.equal(tooFew.code, MOTIF_AUTHORING_WARNING_COUNT);

  const percussion = validateMotifAuthoringSelection(composition, {
    trackId: 'drums-1',
    eventIds: ['d1', 'd2', 'd3'],
  });
  assert.equal(percussion.valid, false);

  const wideSpan = validateMotifAuthoringSelection(composition, {
    trackId: 'melody-1',
    eventIds: ['n1', 'n2', 'n5'],
  });
  assert.equal(wideSpan.valid, false);
  assert.equal(wideSpan.code, MOTIF_AUTHORING_WARNING_BAR_SPAN);
});

test('validateMotifAuthoringSelection requires complete tie chains', () => {
  const composition = compositionWithNotes();
  composition.tracks[0].events.push({
    id: 't2',
    start_tick: 1920,
    duration_ticks: 480,
    pitch: 'A4',
    tie: { group_id: 'tie-1', type: 'stop' },
  });
  composition.tracks[0].events[3].tie = { group_id: 'tie-1', type: 'start' };
  const incomplete = validateMotifAuthoringSelection(composition, {
    trackId: 'melody-1',
    eventIds: ['n1', 'n2', 'n4'],
  });
  assert.equal(incomplete.valid, false);
  assert.equal(incomplete.code, MOTIF_AUTHORING_WARNING_INCOMPLETE_TIE);

  const complete = validateMotifAuthoringSelection(composition, {
    trackId: 'melody-1',
    eventIds: ['n1', 'n2', 'n4', 't2'],
  });
  assert.equal(complete.valid, true);
  assert.equal(complete.eventIds.length, 4);
});

test('resolveOccurrenceLocationLabels maps section and bars', () => {
  const composition = compositionWithNotes();
  const labels = resolveOccurrenceLocationLabels(composition, {
    track_id: 'melody-1',
    event_ids: ['n5', 'n6', 'n7'],
  });
  assert.equal(labels.sectionId, 'chorus');
  assert.equal(labels.startBar, 3);
  assert.equal(labels.endBar, 3);
});

test('countDestinationOverlapEvents counts notes intersecting destination span', () => {
  const composition = compositionWithNotes();
  assert.equal(countDestinationOverlapEvents(composition, 'melody-1', 3840, 5280), 3);
  assert.equal(countDestinationOverlapEvents(composition, 'melody-1', 0, 3840), 4);
  assert.equal(countDestinationOverlapEvents(composition, 'missing-track', 0, 3840), 0);
});

test('resolveMotifOccurrenceBarSpan returns bar span for canonical occurrence', () => {
  const composition = compositionWithNotes();
  const span = resolveMotifOccurrenceBarSpan(composition, {
    id: 'occ-1',
    track_id: 'melody-1',
    event_ids: ['n1', 'n2', 'n3'],
    relationship: 'original',
  });
  assert.equal(span.valid, true);
  assert.equal(span.startBar, 1);
  assert.equal(span.endBar, 1);
  assert.equal(span.barSpan, 1);
});

test('applyMotifReconciliation updates composition motifs', () => {
  const composition = compositionWithNotes();
  const reconciled = applyMotifReconciliation(composition, ['n4']);
  assert.equal(reconciled.composition.motifs.length, 1);
  assert.equal(reconciled.warnings.length, 1);
});

test('projectMotifUsagesForDisplay merges canonical and detected usages', () => {
  const composition = compositionWithNotes();
  const detected = [
    {
      key: 'detected:family-1:occ-d1',
      familyId: 'family-1',
      label: 'Detected exact',
      occurrenceId: 'occ-d1',
      trackId: 'melody-1',
      eventIds: ['n5', 'n6', 'n7'],
      relationship: 'exact',
      identityScore: 1,
      stale: false,
      truncated: false,
      startTick: 3840,
      endTick: 5280,
      startBar: 3,
      endBar: 3,
      sectionId: 'chorus',
      sectionType: 'chorus',
      sectionIndex: 1,
      sectionLabel: 'chorus',
    },
  ];
  const usages = projectMotifUsagesForDisplay(composition, { detectedUsages: detected });
  assert.equal(usages.filter((item) => item.source === 'canonical').length, 2);
  assert.equal(usages.filter((item) => item.source === 'detected').length, 1);
  assert.equal(usages.find((item) => item.source === 'detected').sectionId, 'chorus');
});
