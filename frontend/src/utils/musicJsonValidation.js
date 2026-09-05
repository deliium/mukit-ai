const KEY_PATTERN = /^[A-G](?:#|b)?\s+(?:major|minor)$/;
const TIME_SIGNATURE_PATTERN = /^\d{1,2}\/\d{1,2}$/;
const PITCH_PATTERN = /^([A-G])([#b]?)(-?\d+)$/;
const SUPPORTED_DENOMINATORS = new Set([1, 2, 4, 8, 16, 32]);
const NOTE_TO_SEMITONE = {
  C: 0,
  'C#': 1,
  Db: 1,
  D: 2,
  'D#': 3,
  Eb: 3,
  E: 4,
  F: 5,
  'F#': 6,
  Gb: 6,
  G: 7,
  'G#': 8,
  Ab: 8,
  A: 9,
  'A#': 10,
  Bb: 10,
  B: 11,
};

export function validateMusicJson(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return invalid('Music JSON must be an object.');
  }

  if (value.schema_version === 'composition.v1') {
    return validateCanonicalComposition(value);
  }

  return validateLegacyMusicJson(value);
}

export function isCanonicalComposition(value) {
  return Boolean(value && typeof value === 'object' && value.schema_version === 'composition.v1');
}

function validateCanonicalComposition(value) {
  const base = validateCommonFields(value);
  if (!base.valid) {
    return base;
  }

  const ticksPerQuarter = Number(value.ticks_per_quarter);
  const durationTicks = Number(value.duration_ticks);
  const barCount = Number(value.bar_count);
  if (!Number.isInteger(ticksPerQuarter) || ticksPerQuarter <= 0) {
    return invalid('ticks_per_quarter must be a positive integer.');
  }
  if (!Number.isInteger(durationTicks) || durationTicks <= 0) {
    return invalid('duration_ticks must be a positive integer.');
  }
  if (!Number.isInteger(barCount) || barCount <= 0) {
    return invalid('bar_count must be a positive integer.');
  }

  const barTicks = barDurationTicks(value.time_signature, ticksPerQuarter);
  if (!Number.isInteger(barTicks)) {
    return invalid('time_signature must convert to whole canonical ticks.');
  }
  if (durationTicks !== barCount * barTicks) {
    return invalid('duration_ticks must match bar_count and time_signature.');
  }

  const sectionsResult = validateCanonicalSections(value.sections, barTicks, barCount, durationTicks);
  if (!sectionsResult.valid) {
    return sectionsResult;
  }

  const tracksResult = validateCanonicalTracks(value.tracks, durationTicks);
  if (!tracksResult.valid) {
    return tracksResult;
  }

  console.debug('[musicJsonValidation] Canonical composition validation completed', {
    schemaVersion: value.schema_version,
    barCount,
    trackCount: value.tracks.length,
    eventCount: value.tracks.reduce((count, track) => count + (Array.isArray(track.events) ? track.events.length : 0), 0),
    durationTicks,
  });
  return valid('Canonical composition.v1 JSON is valid for preview and playback.');
}

function validateLegacyMusicJson(value) {
  const base = validateCommonFields(value);
  if (!base.valid) {
    return base;
  }
  if (!Array.isArray(value.harmony)) {
    return invalid('Harmony must be an array.');
  }
  if (value.notes !== undefined && !Array.isArray(value.notes)) {
    return invalid('Notes must be an array.');
  }
  if (!Array.isArray(value.notes) || value.notes.length === 0) {
    console.warn('[musicJsonValidation] Legacy JSON requires migration before canonical playback', {
      hasHarmony: Array.isArray(value.harmony) && value.harmony.length > 0,
    });
    return invalid('Legacy JSON has no note events; regenerate or add notes before canonical playback.');
  }
  console.warn('[musicJsonValidation] Legacy JSON accepted for editing but requires backend normalization', {
    noteCount: value.notes.length,
  });
  return valid('Legacy JSON is editable, but playback/export requires canonical normalization.');
}

function validateCommonFields(value) {
  if (!Number.isFinite(Number(value.tempo)) || Number(value.tempo) < 40 || Number(value.tempo) > 240) {
    return invalid('Tempo must be a number between 40 and 240.');
  }
  if (!value.key || typeof value.key !== 'string' || !KEY_PATTERN.test(value.key.trim())) {
    return invalid('Key must use format like C minor or F# major.');
  }
  if (!isValidTimeSignature(value.time_signature)) {
    return invalid('Time signature must use a supported meter like 4/4, 3/4, or 6/8.');
  }
  if (!Array.isArray(value.sections) || value.sections.length === 0) {
    return invalid('At least one section is required.');
  }
  if (!Array.isArray(value.tracks) || value.tracks.length === 0) {
    return invalid('At least one track is required.');
  }
  return valid('Common music JSON fields are valid.');
}

function validateCanonicalSections(sections, barTicks, barCount, durationTicks) {
  let expectedStartBar = 1;
  let expectedStartTick = 0;
  for (const section of sections) {
    const sectionBarCount = Number(section.bar_count);
    const sectionStartBar = Number(section.start_bar);
    const sectionStartTick = Number(section.start_tick);
    const sectionDurationTicks = Number(section.duration_ticks);
    if (!section.type || typeof section.type !== 'string') {
      return invalid('Every section requires a type.');
    }
    if (!Number.isInteger(sectionBarCount) || sectionBarCount <= 0) {
      return invalid('Every section requires a positive integer bar_count.');
    }
    if (sectionStartBar !== expectedStartBar || sectionStartTick !== expectedStartTick) {
      return invalid('Sections must be contiguous and non-overlapping.');
    }
    if (sectionDurationTicks !== sectionBarCount * barTicks) {
      return invalid('Section duration_ticks must match bar_count and meter.');
    }
    expectedStartBar += sectionBarCount;
    expectedStartTick += sectionDurationTicks;
  }
  if (expectedStartBar - 1 !== barCount || expectedStartTick !== durationTicks) {
    return invalid('Sections must exactly cover the composition duration.');
  }
  return valid('Canonical sections are valid.');
}

function validateCanonicalTracks(tracks, durationTicks) {
  const ids = new Set();
  for (const track of tracks) {
    if (!track.id || typeof track.id !== 'string' || ids.has(track.id)) {
      return invalid('Track IDs must be non-empty and unique.');
    }
    ids.add(track.id);
    if (!track.name || !track.instrument || !track.role) {
      return invalid('Each track requires name, instrument, and role metadata.');
    }
    if (!Number.isInteger(Number(track.midi_program)) || Number(track.midi_program) < 0 || Number(track.midi_program) > 127) {
      return invalid('Track midi_program must be an integer from 0 to 127.');
    }
    if (!Number.isInteger(Number(track.channel)) || Number(track.channel) < 1 || Number(track.channel) > 16) {
      return invalid('Track channel must be an integer from 1 to 16.');
    }
    if (!Array.isArray(track.events)) {
      return invalid('Track events must be an array.');
    }
    for (const event of track.events) {
      const eventResult = validateCanonicalEvent(event, durationTicks);
      if (!eventResult.valid) {
        return eventResult;
      }
    }
  }
  return valid('Canonical tracks are valid.');
}

function validateCanonicalEvent(event, durationTicks) {
  const startTick = Number(event.start_tick);
  const eventDurationTicks = Number(event.duration_ticks);
  const velocity = Number(event.velocity);
  if (event.type !== undefined && event.type !== 'note') {
    return invalid('Only note events are supported.');
  }
  if (!isValidPitch(event.pitch)) {
    return invalid('Event pitch must use MIDI-range scientific notation like C4 or F#3.');
  }
  if (!Number.isInteger(startTick) || startTick < 0) {
    return invalid('Event start_tick must be a non-negative integer.');
  }
  if (!Number.isInteger(eventDurationTicks) || eventDurationTicks <= 0) {
    return invalid('Event duration_ticks must be a positive integer.');
  }
  if (startTick + eventDurationTicks > durationTicks) {
    return invalid('Event timing must fit within composition duration_ticks.');
  }
  if (!Number.isInteger(velocity) || velocity < 1 || velocity > 127) {
    return invalid('Event velocity must be an integer from 1 to 127.');
  }
  return valid('Canonical event is valid.');
}

function isValidTimeSignature(value) {
  if (!value || typeof value !== 'string' || !TIME_SIGNATURE_PATTERN.test(value.trim())) {
    return false;
  }
  const [numerator, denominator] = value.split('/').map(Number);
  return Number.isInteger(numerator) && numerator >= 1 && numerator <= 32 && SUPPORTED_DENOMINATORS.has(denominator);
}

function barDurationTicks(timeSignature, ticksPerQuarter) {
  const [numerator, denominator] = timeSignature.split('/').map(Number);
  const ticks = (numerator * 4 * ticksPerQuarter) / denominator;
  return Number.isInteger(ticks) ? ticks : NaN;
}

function isValidPitch(value) {
  if (!value || typeof value !== 'string') {
    return false;
  }
  const match = value.trim().match(PITCH_PATTERN);
  if (!match) {
    return false;
  }
  const noteName = `${match[1]}${match[2]}`;
  const octave = Number(match[3]);
  const midi = (octave + 1) * 12 + NOTE_TO_SEMITONE[noteName];
  return Number.isInteger(midi) && midi >= 0 && midi <= 127;
}

function valid(message) {
  return { valid: true, message };
}

function invalid(message) {
  console.error('[musicJsonValidation] Invalid music JSON', { message });
  return { valid: false, message };
}
