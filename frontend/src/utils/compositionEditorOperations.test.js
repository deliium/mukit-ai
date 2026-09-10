import assert from 'node:assert/strict';
import test from 'node:test';

import {
  CLIPBOARD_VERSION,
  REJECT,
  copyNotes,
  cutNotes,
  deleteNotes,
  deltaNoteVelocities,
  duplicateNotes,
  humanizeNotes,
  legatoNotes,
  nudgeNoteLengths,
  pasteNotes,
  quantizeNoteEnds,
  quantizeNotes,
  scaleNoteVelocities,
  setNoteArticulations,
  setNoteLengths,
  setNoteVelocities,
  transposeNotes,
} from './compositionEditorOperations.js';
import { makeNoteRef } from './compositionEditorSelection.js';
import { pitchToMidi } from './pianoRollEvents.js';

function buildComposition(overrides = {}) {
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
          {
            id: 'n1',
            type: 'note',
            pitch: 'C4',
            start_tick: 0,
            duration_ticks: 480,
            velocity: 90,
            articulations: [],
            tie: null,
          },
          {
            id: 'n2',
            type: 'note',
            pitch: 'E4',
            start_tick: 480,
            duration_ticks: 480,
            velocity: 90,
            articulations: [],
            tie: null,
          },
          {
            id: 'n3',
            type: 'note',
            pitch: 'G4',
            start_tick: 1000,
            duration_ticks: 400,
            velocity: 80,
            articulations: [],
            tie: null,
          },
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
          {
            id: 'b1',
            type: 'note',
            pitch: 'C2',
            start_tick: 0,
            duration_ticks: 960,
            velocity: 70,
            articulations: [],
            tie: null,
          },
          {
            id: 'b2',
            type: 'note',
            pitch: 'G2',
            start_tick: 1920,
            duration_ticks: 480,
            velocity: 70,
            articulations: [],
            tie: null,
          },
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
          {
            id: 'p1',
            type: 'note',
            pitch: 'C3',
            start_tick: 240,
            duration_ticks: 480,
            velocity: 60,
            articulations: [],
            tie: null,
          },
        ],
      },
    ],
    tempo_changes: [],
    time_signature_changes: [],
    key_changes: [],
    markers: [],
    harmony: [],
    ...overrides,
  };
}

function seededRandom(seed) {
  let state = seed >>> 0;
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 0xffffffff;
  };
}

test('copyNotes builds relative clipboard without ids or motif metadata', () => {
  const composition = buildComposition({
    motifs: [{ id: 'm1', label: 'A', occurrences: [] }],
  });
  const refs = [makeNoteRef('melody', 'n2'), makeNoteRef('bass', 'b1')];
  const result = copyNotes(composition, refs);
  assert.equal(result.ok, true);
  assert.equal(result.clipboard.version, CLIPBOARD_VERSION);
  assert.equal(result.clipboard.anchorTick, 0);
  assert.deepEqual(result.clipboard.trackIds, ['melody', 'bass']);
  assert.equal(result.clipboard.notes.length, 2);
  assert.equal(result.clipboard.notes[0].relativeStartTick, 480);
  assert.equal(result.clipboard.notes[0].id, undefined);
  assert.equal(result.clipboard.notes.every((note) => !('motifs' in note)), true);
});

test('deleteNotes rejects empty, missing, and locked targets atomically', () => {
  const composition = buildComposition();
  assert.equal(deleteNotes(composition, []).code, REJECT.empty_selection);
  assert.equal(
    deleteNotes(composition, [makeNoteRef('melody', 'missing')]).code,
    REJECT.missing_targets,
  );
  assert.equal(
    deleteNotes(composition, [makeNoteRef('melody', 'n1')], { lockedTrackIds: ['melody'] }).code,
    REJECT.locked_targets,
  );
  assert.equal(composition.tracks[0].events.length, 3);
});

test('deleteNotes removes notes, clears broken ties, and reconciles motifs', () => {
  const composition = buildComposition({
    tracks: [
      {
        id: 'melody',
        name: 'Melody',
        instrument: 'Piano',
        role: 'melody',
        midi_program: 0,
        channel: 1,
        events: [
          {
            id: 't1',
            type: 'note',
            pitch: 'C4',
            start_tick: 0,
            duration_ticks: 480,
            velocity: 90,
            articulations: [],
            tie: { group_id: 'g1', type: 'start' },
          },
          {
            id: 't2',
            type: 'note',
            pitch: 'C4',
            start_tick: 480,
            duration_ticks: 480,
            velocity: 90,
            articulations: [],
            tie: { group_id: 'g1', type: 'stop' },
          },
          {
            id: 'keep',
            type: 'note',
            pitch: 'E4',
            start_tick: 960,
            duration_ticks: 480,
            velocity: 90,
            articulations: [],
            tie: null,
          },
        ],
      },
    ],
    motifs: [
      {
        id: 'motif-1',
        label: 'M1',
        occurrences: [
          {
            id: 'occ-1',
            track_id: 'melody',
            event_ids: ['t1', 't2'],
            relationship: 'original',
          },
        ],
      },
    ],
  });

  const result = deleteNotes(composition, [makeNoteRef('melody', 't1')]);
  assert.equal(result.ok, true);
  const events = result.composition.tracks[0].events;
  assert.equal(events.length, 2);
  assert.equal(events.find((event) => event.id === 't2').tie, null);
  assert.equal(events.find((event) => event.id === 'keep'), composition.tracks[0].events[2]);
  assert.deepEqual(result.composition.motifs, []);
});

test('cutNotes returns clipboard and deletes atomically', () => {
  const composition = buildComposition();
  const refs = [makeNoteRef('melody', 'n1')];
  const result = cutNotes(composition, refs);
  assert.equal(result.ok, true);
  assert.equal(result.clipboard.notes.length, 1);
  assert.equal(result.composition.tracks[0].events.some((event) => event.id === 'n1'), false);
  assert.deepEqual(result.selection.refs, []);
});

test('pasteNotes remaps ids, rebuilds complete ties, drops partial ties', () => {
  const composition = buildComposition({
    tracks: [
      {
        id: 'melody',
        name: 'Melody',
        instrument: 'Piano',
        role: 'melody',
        midi_program: 0,
        channel: 1,
        events: [
          {
            id: 't1',
            type: 'note',
            pitch: 'C4',
            start_tick: 0,
            duration_ticks: 480,
            velocity: 90,
            articulations: [],
            tie: { group_id: 'g1', type: 'start' },
          },
          {
            id: 't2',
            type: 'note',
            pitch: 'C4',
            start_tick: 480,
            duration_ticks: 480,
            velocity: 90,
            articulations: [],
            tie: { group_id: 'g1', type: 'stop' },
          },
        ],
      },
    ],
  });

  const full = copyNotes(composition, [makeNoteRef('melody', 't1'), makeNoteRef('melody', 't2')]);
  const pasted = pasteNotes(composition, full.clipboard, { pasteTick: 1920 });
  assert.equal(pasted.ok, true);
  assert.equal(pasted.selection.refs.length, 2);
  const newEvents = pasted.composition.tracks[0].events.slice(-2);
  assert.notEqual(newEvents[0].id, 't1');
  assert.notEqual(newEvents[0].tie.group_id, 'g1');
  assert.equal(newEvents[0].tie.type, 'start');
  assert.equal(newEvents[1].tie.type, 'stop');
  assert.equal(newEvents[0].tie.group_id, newEvents[1].tie.group_id);

  const partial = copyNotes(composition, [makeNoteRef('melody', 't1')]);
  const partialPaste = pasteNotes(composition, partial.clipboard, { pasteTick: 3000 });
  assert.equal(partialPaste.ok, true);
  const lone = partialPaste.composition.tracks[0].events.at(-1);
  assert.equal(lone.tie, null);
  assert.equal(partialPaste.summary.droppedTieCount > 0, true);
});

test('pasteNotes preserves multi-track relative placement and can target active track for single-track clips', () => {
  const composition = buildComposition();
  const multi = copyNotes(composition, [makeNoteRef('melody', 'n1'), makeNoteRef('bass', 'b1')]);
  const multiPaste = pasteNotes(composition, multi.clipboard, { pasteTick: 2400 });
  assert.equal(multiPaste.ok, true);
  const melodyIds = new Set(multiPaste.composition.tracks[0].events.map((event) => event.id));
  const bassIds = new Set(multiPaste.composition.tracks[1].events.map((event) => event.id));
  assert.equal(multiPaste.selection.refs.every((ref) => (
    (ref.trackId === 'melody' && melodyIds.has(ref.eventId))
    || (ref.trackId === 'bass' && bassIds.has(ref.eventId))
  )), true);

  const single = copyNotes(composition, [makeNoteRef('melody', 'n1')]);
  const retarget = pasteNotes(composition, single.clipboard, {
    pasteTick: 3600,
    activeTrackId: 'pad',
    preferActiveTrackForSingleTrackClip: true,
  });
  assert.equal(retarget.ok, true);
  assert.equal(retarget.selection.refs[0].trackId, 'pad');
});

test('pasteNotes rejects locked targets and empty clipboard; allows polyphonic overlap', () => {
  const composition = buildComposition();
  assert.equal(pasteNotes(composition, { notes: [] }).code, REJECT.empty_clipboard);
  const copied = copyNotes(composition, [makeNoteRef('melody', 'n1')]);
  assert.equal(
    pasteNotes(composition, copied.clipboard, {
      pasteTick: 0,
      lockedTrackIds: ['melody'],
    }).code,
    REJECT.locked_targets,
  );

  const overlap = pasteNotes(composition, copied.clipboard, { pasteTick: 0 });
  assert.equal(overlap.ok, true);
  const atZero = overlap.composition.tracks[0].events.filter((event) => event.start_tick === 0);
  assert.ok(atZero.length >= 2);
});

test('duplicateNotes places copy after selection aligned to snap', () => {
  const composition = buildComposition();
  const result = duplicateNotes(composition, [makeNoteRef('melody', 'n1'), makeNoteRef('melody', 'n2')], {
    snapValue: '1/8',
  });
  assert.equal(result.ok, true);
  assert.equal(result.summary.pasteTick, 960);
  assert.equal(result.selection.refs.length, 2);
  const starts = result.selection.refs.map((ref) => {
    const event = result.composition.tracks[0].events.find((item) => item.id === ref.eventId);
    return event.start_tick;
  }).sort((a, b) => a - b);
  assert.deepEqual(starts, [960, 1440]);
});

test('transposeNotes rejects out-of-range pitches and supports octave steps', () => {
  const composition = buildComposition();
  const high = transposeNotes(composition, [makeNoteRef('melody', 'n1')], { semitones: 100 });
  assert.equal(high.code, REJECT.pitch_out_of_range);

  const octave = transposeNotes(composition, [makeNoteRef('melody', 'n1')], { semitones: 12 });
  assert.equal(octave.ok, true);
  const event = octave.composition.tracks[0].events.find((item) => item.id === 'n1');
  assert.equal(pitchToMidi(event.pitch).midi, pitchToMidi('C5').midi);
  assert.equal(octave.composition.tracks[1], composition.tracks[1]);
});

test('velocity helpers clamp to 1-127', () => {
  const composition = buildComposition();
  const refs = [makeNoteRef('melody', 'n1')];

  const setHigh = setNoteVelocities(composition, refs, { velocity: 200 });
  assert.equal(setHigh.composition.tracks[0].events[0].velocity, 127);

  const setLow = setNoteVelocities(composition, refs, { velocity: -5 });
  assert.equal(setLow.composition.tracks[0].events[0].velocity, 1);

  const delta = deltaNoteVelocities(composition, refs, { delta: 50 });
  assert.equal(delta.composition.tracks[0].events[0].velocity, 127);

  const scaled = scaleNoteVelocities(composition, refs, { factor: 0.01 });
  assert.equal(scaled.composition.tracks[0].events[0].velocity, 1);
});

test('quantizeNotes supports 0/50/100% strength and keeps positive lengths inside duration', () => {
  const composition = buildComposition();
  const refs = [makeNoteRef('melody', 'n3')]; // start 1000, duration 400

  const zero = quantizeNotes(composition, refs, {
    mode: 'start',
    snapValue: '1/8',
    strength: 0,
  });
  assert.equal(zero.ok, true);
  assert.equal(zero.composition, composition);
  assert.equal(zero.summary.noOp, true);

  const half = quantizeNotes(composition, refs, {
    mode: 'start',
    snapValue: '1/8',
    strength: 50,
  });
  assert.equal(half.ok, true);
  const halfEvent = half.composition.tracks[0].events.find((event) => event.id === 'n3');
  // nearest snap for 1000 with 240-tick grid is 960; 50% => 980
  assert.equal(halfEvent.start_tick, 980);
  assert.equal(halfEvent.duration_ticks, 400);

  const full = quantizeNotes(composition, refs, {
    mode: 'start_end',
    snapValue: '1/8',
    strength: 100,
  });
  assert.equal(full.ok, true);
  const fullEvent = full.composition.tracks[0].events.find((event) => event.id === 'n3');
  assert.equal(fullEvent.start_tick, 960);
  assert.ok(fullEvent.duration_ticks >= 1);
  assert.ok(fullEvent.start_tick + fullEvent.duration_ticks <= composition.duration_ticks);
});

test('note length helpers enforce positive lengths and composition bounds', () => {
  const composition = buildComposition();
  const refs = [makeNoteRef('melody', 'n1')];

  const setLen = setNoteLengths(composition, refs, { durationTicks: 240 });
  assert.equal(setLen.ok, true);
  assert.equal(setLen.composition.tracks[0].events[0].duration_ticks, 240);

  const nudge = nudgeNoteLengths(composition, refs, { snapValue: '1/8', steps: 1 });
  assert.equal(nudge.ok, true);
  assert.equal(nudge.composition.tracks[0].events[0].duration_ticks, 720);

  assert.equal(
    nudgeNoteLengths(composition, refs, { snapValue: '1/8', steps: -10 }).code,
    REJECT.invalid_timing,
  );

  const ends = quantizeNoteEnds(composition, [makeNoteRef('melody', 'n3')], {
    snapValue: '1/8',
    strength: 100,
  });
  assert.equal(ends.ok, true);
  const event = ends.composition.tracks[0].events.find((item) => item.id === 'n3');
  assert.equal((event.start_tick + event.duration_ticks) % 240, 0);
});

test('legatoNotes extends within same track/voice without exceeding composition', () => {
  const composition = buildComposition();
  // n2 ends at 960; n3 starts at 1000 — gap should close to 520 ticks
  const result = legatoNotes(composition, [makeNoteRef('melody', 'n2')]);
  assert.equal(result.ok, true);
  assert.equal(result.composition.tracks[0].events[1].duration_ticks, 520);

  const last = legatoNotes(composition, [makeNoteRef('melody', 'n3')]);
  assert.equal(last.ok, true);
  const event = last.composition.tracks[0].events.find((item) => item.id === 'n3');
  assert.equal(event.start_tick + event.duration_ticks, composition.duration_ticks);
});

test('setNoteArticulations skips incompatible tied notes and reports summary.skipped', () => {
  const composition = buildComposition({
    tracks: [
      {
        id: 'melody',
        name: 'Melody',
        instrument: 'Piano',
        role: 'melody',
        midi_program: 0,
        channel: 1,
        events: [
          {
            id: 't1',
            type: 'note',
            pitch: 'C4',
            start_tick: 0,
            duration_ticks: 480,
            velocity: 90,
            articulations: [],
            tie: { group_id: 'g1', type: 'start' },
          },
          {
            id: 't2',
            type: 'note',
            pitch: 'C4',
            start_tick: 480,
            duration_ticks: 480,
            velocity: 90,
            articulations: [],
            tie: { group_id: 'g1', type: 'stop' },
          },
          {
            id: 'free',
            type: 'note',
            pitch: 'E4',
            start_tick: 960,
            duration_ticks: 480,
            velocity: 90,
            articulations: [],
            tie: null,
          },
        ],
      },
    ],
  });

  const result = setNoteArticulations(
    composition,
    [makeNoteRef('melody', 't2'), makeNoteRef('melody', 'free')],
    { articulation: 'staccato', mode: 'set' },
  );
  assert.equal(result.ok, true);
  assert.equal(result.summary.skippedCount, 1);
  assert.equal(result.summary.skipped[0].eventId, 't2');
  const free = result.composition.tracks[0].events.find((event) => event.id === 'free');
  assert.deepEqual(free.articulations, ['staccato']);
  const tied = result.composition.tracks[0].events.find((event) => event.id === 't2');
  assert.deepEqual(tied.articulations, []);
});

test('humanizeNotes is deterministic with seeded random and preserves bounds', () => {
  const composition = buildComposition();
  const refs = [makeNoteRef('melody', 'n1'), makeNoteRef('melody', 'n2')];
  const first = humanizeNotes(composition, refs, {
    timingAmount: 20,
    velocityAmount: 15,
    random: seededRandom(42),
  });
  const second = humanizeNotes(composition, refs, {
    timingAmount: 20,
    velocityAmount: 15,
    random: seededRandom(42),
  });
  assert.equal(first.ok, true);
  assert.deepEqual(first.composition.tracks[0].events, second.composition.tracks[0].events);

  for (const event of first.composition.tracks[0].events) {
    assert.ok(event.start_tick >= 0);
    assert.ok(event.duration_ticks >= 1);
    assert.ok(event.start_tick + event.duration_ticks <= composition.duration_ticks);
    assert.ok(event.velocity >= 1 && event.velocity <= 127);
  }
  assert.equal(first.composition.tracks[1], composition.tracks[1]);
});

test('operations preserve structural sharing for untouched tracks and events', () => {
  const composition = buildComposition();
  const untouchedBass = composition.tracks[1];
  const untouchedPad = composition.tracks[2];
  const untouchedN2 = composition.tracks[0].events[1];

  const result = setNoteVelocities(composition, [makeNoteRef('melody', 'n1')], { velocity: 100 });
  assert.equal(result.ok, true);
  assert.equal(result.composition.tracks[1], untouchedBass);
  assert.equal(result.composition.tracks[2], untouchedPad);
  assert.equal(result.composition.tracks[0].events[1], untouchedN2);
  assert.notEqual(result.composition.tracks[0].events[0], composition.tracks[0].events[0]);
});

test('reject codes are stable strings', () => {
  assert.deepEqual(
    Object.values(REJECT).sort(),
    [
      'empty_clipboard',
      'empty_selection',
      'invalid_options',
      'invalid_timing',
      'locked_targets',
      'missing_targets',
      'no_op',
      'pitch_out_of_range',
    ],
  );
});
