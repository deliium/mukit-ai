/**
 * Deterministic 100-bar multi-track V2 fixture for editor E2E / culling budgets.
 * Dense enough to exceed a typical viewport while staying schema-valid.
 */

const TPQ = 480;
const TICKS_PER_BAR = TPQ * 4; // 4/4
const MELODY_PITCHES = ['C4', 'D4', 'E4', 'G4'];
const BASS_PITCHES = ['C2', 'G2', 'A2', 'F2'];
const HARMONY_PITCHES = ['C3', 'E3', 'G3', 'B3'];

function sectionBlocks(barCount) {
  const half = Math.floor(barCount / 2);
  return [
    {
      id: 'section-a',
      type: 'verse',
      label: 'A',
      start_bar: 1,
      bar_count: half,
      start_tick: 0,
      duration_ticks: half * TICKS_PER_BAR,
    },
    {
      id: 'section-b',
      type: 'chorus',
      label: 'B',
      start_bar: half + 1,
      bar_count: barCount - half,
      start_tick: half * TICKS_PER_BAR,
      duration_ticks: (barCount - half) * TICKS_PER_BAR,
    },
  ];
}

function buildTrackEvents({
  trackId,
  barCount,
  pitches,
  notesPerBar,
  velocityBase,
  idPrefix,
}) {
  const events = [];
  const step = Math.floor(TICKS_PER_BAR / notesPerBar);
  for (let bar = 0; bar < barCount; bar += 1) {
    const barStart = bar * TICKS_PER_BAR;
    for (let n = 0; n < notesPerBar; n += 1) {
      const pitch = pitches[(bar + n) % pitches.length];
      events.push({
        id: `${idPrefix}-${bar + 1}-${n + 1}`,
        pitch,
        start_tick: barStart + n * step,
        duration_ticks: Math.max(120, Math.floor(step * 0.75)),
        velocity: velocityBase + ((bar + n) % 8),
      });
    }
  }
  return events;
}

/**
 * @param {{ barCount?: number, notesPerBar?: number }} [options]
 * @returns {object} composition.v2 document
 */
export function buildLargeScoreEditorFixture({
  barCount = 100,
  notesPerBar = 4,
} = {}) {
  const bars = Math.max(8, Math.floor(Number(barCount) || 100));
  const perBar = Math.max(1, Math.min(8, Math.floor(Number(notesPerBar) || 4)));
  const durationTicks = bars * TICKS_PER_BAR;

  const melodyEvents = buildTrackEvents({
    trackId: 'melody-1',
    barCount: bars,
    pitches: MELODY_PITCHES,
    notesPerBar: perBar,
    velocityBase: 80,
    idPrefix: 'm',
  });
  const harmonyEvents = buildTrackEvents({
    trackId: 'harmony-1',
    barCount: bars,
    pitches: HARMONY_PITCHES,
    notesPerBar: Math.max(1, Math.floor(perBar / 2)),
    velocityBase: 60,
    idPrefix: 'h',
  });
  const bassEvents = buildTrackEvents({
    trackId: 'bass-1',
    barCount: bars,
    pitches: BASS_PITCHES,
    notesPerBar: 2,
    velocityBase: 70,
    idPrefix: 'b',
  });

  return {
    schema_version: 'composition.v2',
    tempo: 120,
    key: 'C major',
    time_signature: '4/4',
    ticks_per_quarter: TPQ,
    duration_ticks: durationTicks,
    bar_count: bars,
    sections: sectionBlocks(bars),
    tracks: [
      {
        id: 'melody-1',
        name: 'Melody',
        instrument: 'piano',
        role: 'melody',
        midi_program: 0,
        channel: 1,
        events: melodyEvents,
      },
      {
        id: 'harmony-1',
        name: 'Harmony',
        instrument: 'strings',
        role: 'harmony',
        midi_program: 48,
        channel: 2,
        events: harmonyEvents,
      },
      {
        id: 'bass-1',
        name: 'Bass',
        instrument: 'acoustic bass',
        role: 'bass',
        midi_program: 32,
        channel: 3,
        events: bassEvents,
      },
    ],
    harmony: [
      { start_tick: 0, duration_ticks: durationTicks, chord: 'C' },
    ],
    tempo_changes: [],
    time_signature_changes: [],
    key_changes: [],
    markers: [],
    motifs: [],
  };
}

/** Drop any non-canonical helper keys before API seed. */
export function toCanonicalLargeScore(fixture) {
  const { __testNoteCount, ...composition } = fixture || {};
  return composition;
}

export function largeScoreNoteCount(fixture) {
  if (typeof fixture?.__testNoteCount === 'number') {
    return fixture.__testNoteCount;
  }
  return (fixture?.tracks || []).reduce(
    (sum, track) => sum + (Array.isArray(track.events) ? track.events.length : 0),
    0,
  );
}
