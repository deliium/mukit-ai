/**
 * Legacy-only playback event builders.
 * Canonical Composition V1 must never call these helpers.
 */

const NOTE_TO_MIDI = {
  C: 60,
  'C#': 61,
  Db: 61,
  D: 62,
  'D#': 63,
  Eb: 63,
  E: 64,
  F: 65,
  'F#': 66,
  Gb: 66,
  G: 67,
  'G#': 68,
  Ab: 68,
  A: 69,
  'A#': 70,
  Bb: 70,
  B: 71,
};

/**
 * Build playback events from legacy top-level notes, or harmony-derived chords
 * when notes are absent. Returns an empty list when neither source is usable.
 */
export function buildLegacyPlaybackEvents(musicJson, { frequencyToNote } = {}) {
  if (musicJson?.schema_version === 'composition.v1') {
    console.warn('[legacyPlaybackEvents] Refusing to build legacy events for composition.v1 payload');
    return [];
  }

  if (Array.isArray(musicJson?.notes) && musicJson.notes.length) {
    const beatsPerMeasure = measureQuarterLength(musicJson.time_signature);
    const quarterSeconds = 60 / Number(musicJson.tempo || 100);
    const events = musicJson.notes
      .map((item) => {
        const bar = Number(item.bar);
        const beat = Number(item.beat || 1);
        const duration = Number(item.duration);
        const pitch = normalizePitch(item.pitch);
        if (!Number.isFinite(bar) || bar < 1 || !Number.isFinite(beat) || beat < 1 || !Number.isFinite(duration) || duration <= 0 || !pitch) {
          console.warn('[legacyPlaybackEvents] Invalid legacy note event skipped', {
            bar: item.bar,
            beat: item.beat,
            duration: item.duration,
            pitch: item.pitch,
          });
          return null;
        }
        return {
          bar,
          beat,
          notes: [pitch],
          duration: duration * quarterSeconds,
          velocity: 0.8,
          position: positionForNote(bar, beat, beatsPerMeasure, quarterSeconds),
          stopPosition: positionForNote(bar, beat + duration, beatsPerMeasure, quarterSeconds),
        };
      })
      .filter(Boolean)
      .sort((left, right) => left.bar - right.bar || left.beat - right.beat);

    if (events.length) {
      console.info('[legacyPlaybackEvents] Using legacy top-level notes path', { eventCount: events.length });
      return events;
    }
  }

  if (!Array.isArray(musicJson?.harmony)) {
    console.warn('[legacyPlaybackEvents] Harmony data skipped because it is not an array');
    return [];
  }

  console.warn('[legacyPlaybackEvents] Falling back to harmony-derived chord playback for legacy JSON');
  return musicJson.harmony
    .map((item) => {
      const notes = chordToNotes(item.chord, frequencyToNote);
      if (!notes.length) {
        console.warn('[legacyPlaybackEvents] Unsupported chord data skipped', { chord: item.chord });
        return null;
      }
      const bar = Number(item.bar);
      return {
        bar,
        beat: 1,
        notes,
        duration: '1m',
        velocity: 0.7,
        position: `${bar - 1}:0:0`,
        stopPosition: `${bar}:0:0`,
      };
    })
    .filter((event) => event && Number.isFinite(event.bar) && event.bar > 0);
}

function normalizePitch(pitch) {
  if (!pitch || typeof pitch !== 'string') {
    return '';
  }
  return pitch.trim();
}

function measureQuarterLength(timeSignature) {
  if (!timeSignature || typeof timeSignature !== 'string') {
    return 4;
  }
  const [numerator, denominator] = timeSignature.split('/').map(Number);
  if (!Number.isFinite(numerator) || !Number.isFinite(denominator) || denominator === 0) {
    return 4;
  }
  return numerator * (4 / denominator);
}

function positionForNote(bar, beat, beatsPerMeasure, quarterSeconds) {
  const absoluteQuarterOffset = (bar - 1) * beatsPerMeasure + (beat - 1);
  return absoluteQuarterOffset * quarterSeconds;
}

function chordToNotes(chordName, frequencyToNote) {
  if (!chordName || typeof chordName !== 'string') {
    return [];
  }

  const match = chordName.trim().match(/^([A-G])([#b]?)(.*)$/);
  if (!match) {
    return [];
  }

  const [, rootName, accidental, suffix] = match;
  const root = `${rootName}${accidental}`;
  const rootMidi = NOTE_TO_MIDI[root];
  if (rootMidi === undefined) {
    return [];
  }

  const isMinor = suffix.toLowerCase().startsWith('m') && !suffix.toLowerCase().startsWith('maj');
  const intervals = isMinor ? [0, 3, 7] : [0, 4, 7];
  if (typeof frequencyToNote !== 'function') {
    return intervals.map((interval) => midiNumberToPitch(rootMidi + interval));
  }
  return intervals.map((interval) => frequencyToNote(rootMidi + interval, 'midi'));
}

function midiNumberToPitch(midi) {
  const noteNames = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
  const octave = Math.floor(midi / 12) - 1;
  const name = noteNames[((midi % 12) + 12) % 12];
  return `${name}${octave}`;
}
