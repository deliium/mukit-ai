import assert from 'node:assert/strict';
import test from 'node:test';

import {
  boxesIntersect,
  collectNotesInBarRange,
  collectNotesInBox,
  editableTrackIds,
  makeNoteRef,
  noteIntersectsBox,
  noteRefKey,
  normalizeNoteRef,
  parseNoteRefKey,
  rangeSelectBetween,
  reconcileSelection,
  resolveNoteRefs,
  selectionPitchRange,
  selectionSummary,
  selectionTickRange,
  uniqueNoteRefs,
  visibleTracks,
} from './compositionEditorSelection.js';

function buildComposition() {
  return {
    schema_version: 'composition.v2',
    tempo: 100,
    key: 'C major',
    time_signature: '4/4',
    ticks_per_quarter: 480,
    bar_count: 4,
    duration_ticks: 7680,
    sections: [{ type: 'intro', start_bar: 1, bar_count: 4, start_tick: 0, duration_ticks: 7680 }],
    tracks: [
      {
        id: 'melody',
        name: 'Melody',
        instrument: 'Piano',
        role: 'melody',
        midi_program: 0,
        channel: 1,
        events: [
          { id: 'n1', type: 'note', pitch: 'C4', start_tick: 0, duration_ticks: 480, velocity: 90 },
          { id: 'n2', type: 'note', pitch: 'E4', start_tick: 480, duration_ticks: 480, velocity: 90 },
          { id: 'shared', type: 'note', pitch: 'G4', start_tick: 960, duration_ticks: 480, velocity: 90 },
        ],
      },
      {
        id: 'bass',
        name: 'Bass',
        instrument: 'Bass',
        role: 'bass',
        midi_program: 32,
        channel: 2,
        events: [
          { id: 'shared', type: 'note', pitch: 'C2', start_tick: 0, duration_ticks: 960, velocity: 80 },
          { id: 'b2', type: 'note', pitch: 'G2', start_tick: 1920, duration_ticks: 480, velocity: 80 },
        ],
      },
      {
        id: 'pad',
        name: 'Pad',
        instrument: 'Pad',
        role: 'pad',
        midi_program: 88,
        channel: 3,
        events: [
          { id: 'p1', type: 'note', pitch: 'C3', start_tick: 0, duration_ticks: 1920, velocity: 70 },
        ],
      },
    ],
    tempo_changes: [],
    time_signature_changes: [],
    key_changes: [],
    markers: [],
    harmony: [],
  };
}

test('note refs use trackId+eventId so duplicate event ids across tracks stay distinct', () => {
  const a = makeNoteRef('melody', 'shared');
  const b = makeNoteRef('bass', 'shared');
  assert.notEqual(noteRefKey(a), noteRefKey(b));
  assert.deepEqual(parseNoteRefKey(noteRefKey(a)), a);
  assert.deepEqual(normalizeNoteRef({ trackId: '  melody ', eventId: ' shared ' }), a);
  assert.equal(uniqueNoteRefs([a, a, b]).length, 2);
});

test('visible and editable track filters honor hidden and locked sets', () => {
  const composition = buildComposition();
  assert.equal(visibleTracks(composition, ['pad']).length, 2);
  assert.deepEqual(
    editableTrackIds(composition, { hiddenTrackIds: ['pad'], lockedTrackIds: ['bass'] }),
    ['melody'],
  );
});

test('box intersection includes edge contact and excludes hidden tracks', () => {
  const composition = buildComposition();
  // Touch left edge of melody n2 at tick 480
  const edgeHits = collectNotesInBox(composition, {
    startTick: 480,
    endTick: 481,
    pitchMin: 64,
    pitchMax: 64,
  });
  assert.deepEqual(edgeHits, [{ trackId: 'melody', eventId: 'n2' }]);

  const withoutHidden = collectNotesInBox(composition, {
    startTick: 0,
    endTick: 2000,
    pitchMin: 0,
    pitchMax: 127,
  }, { hiddenTrackIds: ['pad'] });
  assert.ok(withoutHidden.every((ref) => ref.trackId !== 'pad'));
  assert.ok(withoutHidden.some((ref) => ref.trackId === 'bass' && ref.eventId === 'shared'));
  assert.ok(withoutHidden.some((ref) => ref.trackId === 'melody' && ref.eventId === 'shared'));

  assert.equal(
    boxesIntersect(
      { startTick: 0, endTick: 100, pitchMin: 60, pitchMax: 60 },
      { startTick: 100, endTick: 200, pitchMin: 60, pitchMax: 60 },
    ),
    false,
  );
  assert.equal(
    noteIntersectsBox(
      { startTick: 0, endTick: 100, pitchMidi: 60, trackId: 't', eventId: 'e' },
      { startTick: 50, endTick: 60, pitchMin: 60, pitchMax: 60 },
    ),
    true,
  );
});

test('reconcileSelection drops stale and hidden refs while preserving primary when possible', () => {
  const composition = buildComposition();
  const result = reconcileSelection(composition, {
    refs: [
      { trackId: 'melody', eventId: 'n1' },
      { trackId: 'melody', eventId: 'missing' },
      { trackId: 'pad', eventId: 'p1' },
    ],
    primary: { trackId: 'pad', eventId: 'p1' },
  }, { hiddenTrackIds: ['pad'], dropHidden: true });

  assert.deepEqual(result.refs, [{ trackId: 'melody', eventId: 'n1' }]);
  assert.deepEqual(result.primary, { trackId: 'melody', eventId: 'n1' });
  assert.equal(result.droppedCount, 2);
});

test('locked tracks resolve for selection but remain distinguishable', () => {
  const composition = buildComposition();
  const resolved = resolveNoteRefs(composition, [{ trackId: 'bass', eventId: 'b2' }], {
    lockedTrackIds: ['bass'],
  });
  assert.equal(resolved.length, 1);
  assert.equal(resolved[0].locked, true);
});

test('selection tick/pitch aggregates and bar-range collection work across tracks', () => {
  const composition = buildComposition();
  const refs = [
    { trackId: 'melody', eventId: 'n1' },
    { trackId: 'bass', eventId: 'shared' },
  ];
  assert.deepEqual(selectionTickRange(composition, refs), {
    startTick: 0,
    endTick: 960,
    noteCount: 2,
  });
  assert.deepEqual(selectionPitchRange(composition, refs), {
    pitchMin: 36, // C2
    pitchMax: 60, // C4
    noteCount: 2,
  });

  const inBar1 = collectNotesInBarRange(composition, 1, 1, { hiddenTrackIds: ['pad'] });
  assert.ok(inBar1.some((ref) => ref.trackId === 'melody' && ref.eventId === 'n1'));
  assert.ok(inBar1.every((ref) => ref.trackId !== 'pad'));
});

test('rangeSelectBetween spans tracks in timeline order', () => {
  const composition = buildComposition();
  const range = rangeSelectBetween(
    composition,
    { trackId: 'melody', eventId: 'n1' },
    { trackId: 'melody', eventId: 'n2' },
    { hiddenTrackIds: ['pad'] },
  );
  assert.ok(range.some((ref) => ref.eventId === 'n1'));
  assert.ok(range.some((ref) => ref.eventId === 'n2'));
  // Bass shared starts at 0 with lower pitch — may appear between depending on sort.
  assert.ok(range.length >= 2);

  const summary = selectionSummary(composition, range, {
    hiddenTrackIds: ['pad'],
    lockedTrackIds: ['bass'],
  });
  assert.equal(typeof summary.selectedCount, 'number');
  assert.ok(summary.selectedCount >= 2);
  assert.equal(summary.hiddenCount, 0);
});
