import { classifyCompositionVersion, SCHEMA_VERSION_V2 } from './compositionVersion.js';
import { barEndTick, barStartTick, compileTimeline } from './compositionTimeline.js';
import { validateMotifDefinitions } from './compositionMotifs.js';

const KEY_PATTERN = /^[A-G](?:#|b)?\s+(?:major|minor)$/;
const TIME_SIGNATURE_PATTERN = /^\d{1,2}\/\d{1,2}$/;
const PITCH_PATTERN = /^([A-G])([#b]?)(-?\d+)$/;
const SUPPORTED_DENOMINATORS = new Set([1, 2, 4, 8, 16, 32]);
const ARTICULATION_VALUES = new Set(['staccato', 'staccatissimo', 'tenuto', 'accent', 'marcato']);
const GATE_SHORTENING_ARTICULATIONS = new Set(['staccato', 'staccatissimo', 'marcato']);
const ATTACK_ARTICULATIONS = new Set(['accent', 'marcato']);
/** Mirrors backend ``SUPPORTED_SECTION_TYPES`` (includes import-neutral ``unsectioned``). */
export const SUPPORTED_SECTION_TYPES = new Set([
  'intro',
  'verse',
  'pre_chorus',
  'chorus',
  'bridge',
  'solo',
  'breakdown',
  'outro',
  'unsectioned',
]);
/** Mirrors backend ``SUPPORTED_TRACK_ROLES`` (includes import-neutral ``other``). */
export const SUPPORTED_TRACK_ROLES = new Set([
  'melody',
  'harmony',
  'bass',
  'drums',
  'percussion',
  'countermelody',
  'pad',
  'lead',
  'rhythm',
  'other',
]);
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

  const category = classifyCompositionVersion(value);
  if (category === 'unsupported') {
    const version = value.schema_version ?? 'missing';
    return invalid(`Unsupported schema_version: ${version}`);
  }
  if (category === 'legacy') {
    return validateLegacyMusicJson(value);
  }
  if (category === 'v1') {
    return validateCanonicalComposition(value, 'v1');
  }
  return validateCanonicalComposition(value, 'v2');
}

export function isCanonicalComposition(value) {
  return Boolean(value && typeof value === 'object' && value.schema_version === SCHEMA_VERSION_V2);
}

function validateCanonicalComposition(value, variant) {
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

  // V2 duration/sections must match the compiled variable-meter bar map, not root meter alone.
  let timeline = null;
  if (variant === 'v2') {
    timeline = compileTimeline(value);
    if (!timeline) {
      console.debug('[FIX:variable-meter-validation] compileTimeline rejected composition', {
        barCount,
        durationTicks,
        rootMeter: value.time_signature,
        meterChangeCount: Array.isArray(value.time_signature_changes)
          ? value.time_signature_changes.length
          : 0,
      });
      return invalid('duration_ticks must match bar_count and the compiled meter map.');
    }
    console.debug('[FIX:variable-meter-validation] compiled V2 timeline accepted', {
      barCount: timeline.barCount,
      durationTicks: timeline.durationTicks,
      meterChangeCount: timeline.timeSignatureChanges.length,
    });
  } else if (durationTicks !== barCount * barTicks) {
    return invalid('duration_ticks must match bar_count and time_signature.');
  }

  const sectionsResult = validateCanonicalSections(
    value.sections,
    barTicks,
    barCount,
    durationTicks,
    variant,
    timeline,
  );
  if (!sectionsResult.valid) {
    return sectionsResult;
  }

  const tracksResult = validateCanonicalTracks(value.tracks, durationTicks, variant);
  if (!tracksResult.valid) {
    return tracksResult;
  }

  if (variant === 'v2') {
    const timelineResult = validateV2TimelineChanges(value, durationTicks, timeline);
    if (!timelineResult.valid) {
      return timelineResult;
    }
    const markersResult = validateV2Markers(value.markers, durationTicks);
    if (!markersResult.valid) {
      return markersResult;
    }
    const harmonyResult = validateV2HarmonySpans(value.harmony, durationTicks);
    if (!harmonyResult.valid) {
      return harmonyResult;
    }
    const motifsResult = validateMotifDefinitions(value);
    if (!motifsResult.valid) {
      return motifsResult;
    }
  }

  console.debug('[musicJsonValidation] Canonical composition validation completed', {
    schemaVersion: value.schema_version,
    barCount,
    trackCount: value.tracks.length,
    eventCount: value.tracks.reduce((count, track) => count + (Array.isArray(track.events) ? track.events.length : 0), 0),
    durationTicks,
    meterChangeCount: timeline ? timeline.timeSignatureChanges.length : 0,
    motifCount: Array.isArray(value.motifs) ? value.motifs.length : 0,
  });
  const label = value.schema_version === SCHEMA_VERSION_V2 ? 'composition.v2' : 'composition.v1';
  return valid(`Canonical ${label} JSON is valid for preview and playback.`);
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

function validateCanonicalSections(sections, barTicks, barCount, durationTicks, variant, timeline = null) {
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
    const normalizedSectionType = String(section.type)
      .trim()
      .toLowerCase()
      .replace(/[-\s]+/g, '_');
    if (!SUPPORTED_SECTION_TYPES.has(normalizedSectionType)) {
      return invalid(`Unsupported section type: ${section.type}`);
    }
    if (variant === 'v2' && section.id != null && typeof section.id !== 'string') {
      return invalid('Section id must be a string when provided.');
    }
    if (!Number.isInteger(sectionBarCount) || sectionBarCount <= 0) {
      return invalid('Every section requires a positive integer bar_count.');
    }
    if (sectionStartBar !== expectedStartBar || sectionStartTick !== expectedStartTick) {
      return invalid('Sections must be contiguous and non-overlapping.');
    }
    if (timeline) {
      const expectedStart = barStartTick(timeline, sectionStartBar);
      const expectedEnd = barEndTick(timeline, sectionStartBar + sectionBarCount - 1);
      if (expectedStart == null || expectedEnd == null) {
        return invalid('Section bar range must fit within the composition.');
      }
      if (sectionStartTick !== expectedStart || sectionDurationTicks !== expectedEnd - expectedStart) {
        return invalid('Section duration_ticks must match bar_count and meter.');
      }
    } else if (sectionDurationTicks !== sectionBarCount * barTicks) {
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

function validateCanonicalTracks(tracks, durationTicks, variant) {
  const ids = new Set();
  for (const track of tracks) {
    if (!track.id || typeof track.id !== 'string' || ids.has(track.id)) {
      return invalid('Track IDs must be non-empty and unique.');
    }
    ids.add(track.id);
    if (!track.name || !track.instrument || !track.role) {
      return invalid('Each track requires name, instrument, and role metadata.');
    }
    const normalizedRole = String(track.role)
      .trim()
      .toLowerCase()
      .replace(/[-\s]+/g, '_');
    if (!SUPPORTED_TRACK_ROLES.has(normalizedRole)) {
      return invalid(`Unsupported track role: ${track.role}`);
    }
    if (!Number.isInteger(Number(track.midi_program)) || Number(track.midi_program) < 0 || Number(track.midi_program) > 127) {
      return invalid('Track midi_program must be an integer from 0 to 127.');
    }
    if (!Number.isInteger(Number(track.channel)) || Number(track.channel) < 1 || Number(track.channel) > 16) {
      return invalid('Track channel must be an integer from 1 to 16.');
    }
    if (variant === 'v2') {
      const expression = Number(track.expression);
      if (!Number.isInteger(expression) || expression < 0 || expression > 127) {
        return invalid('Track expression must be an integer from 0 to 127.');
      }
      const pedalsResult = validateSustainPedals(track.sustain_pedals, durationTicks, track.id);
      if (!pedalsResult.valid) {
        return pedalsResult;
      }
    }
    if (!Array.isArray(track.events)) {
      return invalid('Track events must be an array.');
    }
    for (const event of track.events) {
      const eventResult = validateCanonicalEvent(event, durationTicks, variant);
      if (!eventResult.valid) {
        return eventResult;
      }
    }
    if (variant === 'v2') {
      const tieResult = validateTieChains(track);
      if (!tieResult.valid) {
        return tieResult;
      }
    }
  }
  return valid('Canonical tracks are valid.');
}

function validateCanonicalEvent(event, durationTicks, variant) {
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
  if (variant === 'v2') {
    const articulations = Array.isArray(event.articulations) ? event.articulations : [];
    const articulationResult = validateArticulations(articulations, event.tie);
    if (!articulationResult.valid) {
      return articulationResult;
    }
    if (event.tie != null) {
      if (typeof event.tie !== 'object' || !event.tie.group_id || !event.tie.type) {
        return invalid('Event tie must include group_id and type.');
      }
      if (event.tie.type !== 'start' && event.tie.type !== 'continue' && event.tie.type !== 'stop') {
        return invalid('Event tie type must be start, continue, or stop.');
      }
    }
  }
  return valid('Canonical event is valid.');
}

function validateArticulations(articulations, tie) {
  const names = articulations;
  const unique = new Set(names);
  if (unique.size !== names.length) {
    return invalid('Duplicate articulations on a note are not allowed.');
  }
  for (const name of names) {
    if (!ARTICULATION_VALUES.has(name)) {
      return invalid(`Unsupported articulation: ${name}`);
    }
  }
  if (names.includes('staccato') && names.includes('staccatissimo')) {
    return invalid('staccato and staccatissimo cannot combine.');
  }
  if ((names.includes('staccato') || names.includes('staccatissimo')) && names.includes('tenuto')) {
    return invalid('Short articulations cannot combine with tenuto.');
  }
  if (names.includes('accent') && names.includes('marcato')) {
    return invalid('accent and marcato cannot combine.');
  }
  if (tie != null) {
    if (names.some((name) => GATE_SHORTENING_ARTICULATIONS.has(name))) {
      return invalid('Gate-shortening articulations are not allowed in a tie chain.');
    }
    if (names.some((name) => ATTACK_ARTICULATIONS.has(name)) && tie.type !== 'start') {
      return invalid('Attack articulations are only allowed on the tie chain head.');
    }
  }
  return valid('Articulations are valid.');
}

function validateTieChains(track) {
  const groups = new Map();
  for (const event of track.events) {
    if (!event.tie) {
      continue;
    }
    const groupId = event.tie.group_id;
    if (!groups.has(groupId)) {
      groups.set(groupId, []);
    }
    groups.get(groupId).push(event);
  }
  for (const [groupId, members] of groups) {
    const ordered = [...members].sort((a, b) => a.start_tick - b.start_tick || a.duration_ticks - b.duration_ticks);
    const types = ordered.map((event) => event.tie.type);
    if (types.filter((type) => type === 'start').length !== 1
      || types.filter((type) => type === 'stop').length !== 1
      || types[0] !== 'start'
      || types[types.length - 1] !== 'stop') {
      return invalid(`Tie group ${groupId} on track ${track.id} is musically invalid.`);
    }
    if (types.some((type, index) => type === 'continue' && (index === 0 || index === types.length - 1))) {
      return invalid(`Tie group ${groupId} continue placement is invalid.`);
    }
    if (types.some((type) => type !== 'start' && type !== 'continue' && type !== 'stop')) {
      return invalid(`Tie group ${groupId} has invalid tie types.`);
    }
    const head = ordered[0];
    for (const event of ordered) {
      if (event.pitch !== head.pitch || event.staff !== head.staff || event.voice !== head.voice) {
        return invalid(`Tie group ${groupId} members must share pitch/staff/voice.`);
      }
    }
    for (let index = 1; index < ordered.length; index += 1) {
      const previous = ordered[index - 1];
      const current = ordered[index];
      const previousEnd = previous.start_tick + previous.duration_ticks;
      if (current.start_tick !== previousEnd) {
        return invalid(`Tie group ${groupId} members must be contiguous.`);
      }
    }
  }
  return valid('Tie chains are valid.');
}

function validateSustainPedals(pedals, durationTicks, trackId) {
  if (!Array.isArray(pedals)) {
    return invalid(`Track ${trackId} sustain_pedals must be an array.`);
  }
  let previousEnd = -1;
  for (const pedal of pedals) {
    const startTick = Number(pedal.start_tick);
    const pedalDuration = Number(pedal.duration_ticks);
    if (!Number.isInteger(startTick) || startTick < 0) {
      return invalid('Sustain pedal start_tick must be a non-negative integer.');
    }
    if (!Number.isInteger(pedalDuration) || pedalDuration <= 0) {
      return invalid('Sustain pedal duration_ticks must be a positive integer.');
    }
    if (startTick + pedalDuration > durationTicks) {
      return invalid('Sustain pedals must fit within composition duration.');
    }
    if (startTick < previousEnd) {
      return invalid('Sustain pedals must be non-overlapping.');
    }
    if (startTick === previousEnd && previousEnd >= 0) {
      return invalid('Adjacent sustain pedal spans are not allowed.');
    }
    previousEnd = startTick + pedalDuration;
  }
  return valid('Sustain pedals are valid.');
}

function validateV2TimelineChanges(value, durationTicks, timeline) {
  const tempoChanges = Array.isArray(value.tempo_changes) ? value.tempo_changes : [];
  const meterChanges = Array.isArray(value.time_signature_changes) ? value.time_signature_changes : [];
  const keyChanges = Array.isArray(value.key_changes) ? value.key_changes : [];
  const barStarts = new Set(timeline.barBoundaries.slice(0, -1));

  let previousTempoTick = -1;
  for (const change of tempoChanges) {
    const tick = Number(change.tick);
    const tempo = Number(change.bpm ?? change.tempo);
    if (!Number.isInteger(tick) || tick <= 0 || tick >= durationTicks) {
      return invalid('tempo_changes ticks must be in (0, duration_ticks).');
    }
    if (tick <= previousTempoTick) {
      return invalid('tempo_changes must be in ascending tick order with unique ticks.');
    }
    if (!Number.isFinite(tempo) || tempo < 40 || tempo > 240) {
      return invalid('tempo_changes tempo must be between 40 and 240.');
    }
    previousTempoTick = tick;
  }

  let previousMeterTick = -1;
  for (const change of meterChanges) {
    const tick = Number(change.tick);
    if (!Number.isInteger(tick) || tick <= 0 || tick >= durationTicks) {
      return invalid('time_signature_changes ticks must be in (0, duration_ticks).');
    }
    if (tick <= previousMeterTick) {
      return invalid('time_signature_changes must be in ascending tick order.');
    }
    if (!isValidTimeSignature(change.time_signature)) {
      return invalid('time_signature_changes must use supported meters.');
    }
    if (!barStarts.has(tick)) {
      return invalid('time_signature_changes must occur on bar boundaries.');
    }
    previousMeterTick = tick;
  }

  let previousKeyTick = -1;
  for (const change of keyChanges) {
    const tick = Number(change.tick);
    if (!Number.isInteger(tick) || tick <= 0 || tick >= durationTicks) {
      return invalid('key_changes ticks must be in (0, duration_ticks).');
    }
    if (tick <= previousKeyTick) {
      return invalid('key_changes must be in ascending tick order with unique ticks.');
    }
    if (!change.key || typeof change.key !== 'string' || !KEY_PATTERN.test(change.key.trim())) {
      return invalid('key_changes key must use format like C minor or F# major.');
    }
    if (!barStarts.has(tick)) {
      return invalid('key_changes must occur on bar boundaries.');
    }
    previousKeyTick = tick;
  }

  return valid('V2 timeline changes are valid.');
}

function validateV2Markers(markers, durationTicks) {
  if (!Array.isArray(markers)) {
    return invalid('markers must be an array.');
  }
  const fingerprints = new Set();
  for (const marker of markers) {
    const tick = Number(marker.tick);
    if (!Number.isInteger(tick) || tick < 0 || tick > durationTicks) {
      return invalid('Marker tick must be within composition duration.');
    }
    const fingerprint = `${tick}:${marker.kind}:${marker.label}`;
    if (fingerprints.has(fingerprint)) {
      return invalid('Exact duplicate markers are not allowed.');
    }
    fingerprints.add(fingerprint);
  }
  return valid('Markers are valid.');
}

function validateV2HarmonySpans(harmony, durationTicks) {
  if (harmony == null) {
    return valid('Harmony omitted.');
  }
  if (!Array.isArray(harmony)) {
    return invalid('Harmony must be an array of explicit tick spans.');
  }
  let previousStart = -1;
  let previousEnd = 0;
  for (const item of harmony) {
    if (!item || typeof item !== 'object' || Array.isArray(item)) {
      return invalid('Harmony items must be objects with start_tick, duration_ticks, and chord.');
    }
    if (Object.prototype.hasOwnProperty.call(item, 'bar')) {
      return invalid('Canonical composition.v2 harmony must use tick spans, not bar points.');
    }
    const startTick = Number(item.start_tick);
    const duration = Number(item.duration_ticks);
    const chord = typeof item.chord === 'string' ? item.chord.trim() : '';
    if (!Number.isInteger(startTick) || startTick < 0) {
      return invalid('Harmony start_tick must be a non-negative integer.');
    }
    if (!Number.isInteger(duration) || duration <= 0) {
      return invalid('Harmony duration_ticks must be a positive integer.');
    }
    if (startTick + duration > durationTicks) {
      return invalid('Harmony spans must fit within composition duration_ticks.');
    }
    if (!chord || chord.length > 32) {
      return invalid('Harmony chord must be a non-empty string up to 32 characters.');
    }
    if (startTick <= previousStart) {
      return invalid('Harmony spans must be sorted by unique start_tick.');
    }
    if (startTick < previousEnd) {
      return invalid('Harmony spans must not overlap.');
    }
    previousStart = startTick;
    previousEnd = startTick + duration;
  }
  return valid('Harmony spans are valid.');
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
