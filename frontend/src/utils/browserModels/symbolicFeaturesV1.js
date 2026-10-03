/**
 * JS twin of backend `embeddings/features.py` — profile symbolic.features.v1.
 * Handcrafted extract on browser CPU; never logs full vectors.
 */

import { createAppLogger } from '../appLogger.js';
import { compositionSourceFingerprint } from '../compositionAnalysis.js';
import { pitchToMidi } from '../pianoRollEvents.js';
import { barDurationTicks, compileBarBoundaries } from '../compositionTimeline.js';
import { embedScopeDigest } from './embedScopeDigest.js';
import {
  BROWSER_SYMBOLIC_FEATURES_MODEL_ID,
  FALLBACK_REASONS,
  SYMBOLIC_FEATURES_ALGORITHM_VERSION,
  SYMBOLIC_FEATURES_DIMS,
  SYMBOLIC_FEATURES_DISTANCE_METRIC,
  SYMBOLIC_FEATURES_PROFILE_ID,
  SYMBOLIC_FEATURES_PROJECTION_NONE,
} from './constants.js';

const log = createAppLogger('browserModels');

const PC_DIMS = 12;
const DUR_BINS = 8;
const ONSET_BINS = 8;
const INTERVAL_BINS = 25;
const CONCURRENT_BINS = 4;
const SECTION_TYPE_DIMS = 8;

const SECTION_TYPE_ORDER = [
  'intro', 'verse', 'chorus', 'bridge', 'outro', 'solo', 'break', 'other',
];
const ROLE_ORDER = ['melody', 'bass', 'accompaniment', 'other', 'drum'];

/** @type {Map<string, object>} */
const sessionMemo = new Map();

function normalizeHist(values) {
  const total = values.reduce((sum, v) => sum + v, 0);
  if (total <= 0) return;
  const inv = 1 / total;
  for (let i = 0; i < values.length; i += 1) {
    values[i] *= inv;
  }
}

function durationBin(ratio) {
  const thresholds = [0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0];
  for (let i = 0; i < thresholds.length; i += 1) {
    if (ratio < thresholds[i]) return i;
  }
  return DUR_BINS - 1;
}

function concurrentBin(overlap) {
  if (overlap <= 1) return 0;
  if (overlap <= 2) return 1;
  if (overlap <= 4) return 2;
  return 3;
}

function normalizeRole(role, isDrum) {
  if (isDrum) return 'drum';
  const text = String(role || 'other').trim().toLowerCase();
  if (text === 'melody' || text === 'lead') return 'melody';
  if (text === 'bass') return 'bass';
  if (text === 'accompaniment' || text === 'harmony' || text === 'pad' || text === 'chord') {
    return 'accompaniment';
  }
  if (text === 'drums' || text === 'percussion' || text === 'drum') return 'drum';
  return 'other';
}

function midiPitchNumber(pitch) {
  if (typeof pitch === 'number' && Number.isFinite(pitch)) {
    return Math.round(pitch);
  }
  const { midi } = pitchToMidi(String(pitch));
  if (midi == null) {
    throw new Error('invalid pitch');
  }
  return midi;
}

function l2Normalize(raw) {
  let sumSq = 0;
  for (let i = 0; i < raw.length; i += 1) {
    sumSq += raw[i] * raw[i];
  }
  if (sumSq <= 0) {
    return raw.map(() => 0);
  }
  const inv = 1 / Math.sqrt(sumSq);
  return raw.map((v) => v * inv);
}

function barsCovering(boundaries, startTick, endTick) {
  let startBar = 1;
  let endBar = Math.max(1, boundaries.length - 1);
  for (let i = 0; i < boundaries.length - 1; i += 1) {
    if (boundaries[i] <= startTick && startTick < boundaries[i + 1]) {
      startBar = i + 1;
      break;
    }
  }
  for (let i = 0; i < boundaries.length - 1; i += 1) {
    if (
      (boundaries[i] < endTick && endTick <= boundaries[i + 1])
      || (i === boundaries.length - 2 && endTick >= boundaries[i])
    ) {
      endBar = i + 1;
    }
  }
  return [startBar, Math.max(startBar, endBar)];
}

function windowForTickRange(composition, {
  startTick,
  endTick,
  startBar,
  endBarInclusive,
  scopeKind,
  sectionIndex,
  sectionType,
  formPosition,
  trackIdFilter = null,
}) {
  const notes = [];
  for (const track of composition.tracks || []) {
    if (trackIdFilter != null && track.id !== trackIdFilter) continue;
    const isDrum = Boolean(track.is_drum);
    const role = normalizeRole(track.role, isDrum);
    for (const event of track.events || []) {
      const onset = Number(event.start_tick);
      if (!Number.isFinite(onset) || onset < startTick || onset >= endTick) continue;
      try {
        const pitch = midiPitchNumber(event.pitch);
        notes.push({
          pitch_midi: pitch,
          start_tick: onset,
          duration_ticks: Math.max(1, Number(event.duration_ticks) || 1),
          track_id: track.id,
          role,
          is_drum: isDrum,
        });
      } catch {
        // skip invalid pitch
      }
    }
  }
  return {
    scope_kind: scopeKind,
    start_tick: startTick,
    end_tick: endTick,
    start_bar: startBar,
    end_bar_inclusive: endBarInclusive,
    section_index: sectionIndex,
    section_type: sectionType,
    form_position: Math.max(0, Math.min(1, formPosition)),
    motif_relative_pitch_span: 0,
    motif_relative_rhythm_span: 0,
    notes,
  };
}

function resolveSectionWindow(composition, boundaries, scope) {
  const sections = composition.sections || [];
  if (scope.section_index >= sections.length) {
    throw Object.assign(new Error('invalid section scope'), { code: 'embed_scope_invalid' });
  }
  const section = sections[scope.section_index];
  if (scope.section_id != null && section.id && section.id !== scope.section_id) {
    throw Object.assign(new Error('section_id mismatch'), { code: 'embed_scope_invalid' });
  }
  if (scope.expected_start_bar != null && section.start_bar !== scope.expected_start_bar) {
    throw Object.assign(new Error('expected_start_bar mismatch'), { code: 'embed_scope_invalid' });
  }
  if (scope.expected_bar_count != null && section.bar_count !== scope.expected_bar_count) {
    throw Object.assign(new Error('expected_bar_count mismatch'), { code: 'embed_scope_invalid' });
  }
  const endBar = section.start_bar + section.bar_count - 1;
  let formPosition = 0;
  if (composition.bar_count > 1) {
    formPosition = (section.start_bar - 1) / (composition.bar_count - 1);
  }
  return windowForTickRange(composition, {
    startTick: section.start_tick,
    endTick: section.start_tick + section.duration_ticks,
    startBar: section.start_bar,
    endBarInclusive: endBar,
    scopeKind: 'section',
    sectionIndex: scope.section_index,
    sectionType: section.type ? String(section.type) : null,
    formPosition,
  });
}

function resolveBarRangeWindow(composition, boundaries, scope) {
  if (scope.end_bar > composition.bar_count || scope.start_bar < 1) {
    throw Object.assign(new Error('invalid bar_range'), { code: 'embed_scope_invalid' });
  }
  const startTick = boundaries[scope.start_bar - 1];
  const endTick = boundaries[scope.end_bar];
  let formPosition = 0;
  if (composition.bar_count > 1) {
    formPosition = (scope.start_bar - 1) / (composition.bar_count - 1);
  }
  return windowForTickRange(composition, {
    startTick,
    endTick,
    startBar: scope.start_bar,
    endBarInclusive: scope.end_bar,
    scopeKind: 'bar_range',
    sectionIndex: null,
    sectionType: null,
    formPosition,
    trackIdFilter: scope.track_id ?? null,
  });
}

function resolveMotifWindow(composition, boundaries, scope) {
  const motifs = composition.motifs || [];
  const motif = motifs.find((m) => m.id === scope.motif_id);
  if (!motif) {
    throw Object.assign(new Error('motif not found'), { code: 'embed_scope_invalid' });
  }
  let occurrence = null;
  if (scope.occurrence_id) {
    occurrence = (motif.occurrences || []).find((o) => o.id === scope.occurrence_id);
    if (!occurrence) {
      throw Object.assign(new Error('occurrence not found'), { code: 'embed_scope_invalid' });
    }
  } else {
    occurrence = (motif.occurrences || []).find((o) => o.relationship === 'original')
      || (motif.occurrences || [])[0];
  }
  if (!occurrence) {
    throw Object.assign(new Error('no occurrence'), { code: 'embed_scope_invalid' });
  }
  const track = (composition.tracks || []).find((t) => t.id === occurrence.track_id);
  if (!track) {
    throw Object.assign(new Error('track not found'), { code: 'embed_scope_invalid' });
  }
  const idSet = new Set(occurrence.event_ids || []);
  const notes = [];
  const isDrum = Boolean(track.is_drum);
  const role = normalizeRole(track.role, isDrum);
  for (const event of track.events || []) {
    if (!event.id || !idSet.has(event.id)) continue;
    try {
      notes.push({
        pitch_midi: midiPitchNumber(event.pitch),
        start_tick: Number(event.start_tick),
        duration_ticks: Math.max(1, Number(event.duration_ticks) || 1),
        track_id: track.id,
        role,
        is_drum: isDrum,
      });
    } catch {
      // skip
    }
  }
  if (!notes.length) {
    throw Object.assign(new Error('empty motif scope'), { code: FALLBACK_REASONS.TWIN_EMPTY_SCOPE });
  }
  const startTick = Math.min(...notes.map((n) => n.start_tick));
  const endTick = Math.max(...notes.map((n) => n.start_tick + n.duration_ticks));
  const [startBar, endBar] = barsCovering(boundaries, startTick, endTick);
  const pitches = notes.map((n) => n.pitch_midi);
  const anchorPitch = pitches[0];
  const relPitches = pitches.map((p) => p - anchorPitch);
  const pitchSpan = relPitches.length
    ? (Math.max(...relPitches) - Math.min(...relPitches)) / 24
    : 0;
  const anchorOnset = notes[0].start_tick;
  const relOnsets = notes.map((n) => n.start_tick - anchorOnset);
  let rhythmSpan = 0;
  if (relOnsets.length && composition.ticks_per_quarter > 0) {
    rhythmSpan = (Math.max(...relOnsets) - Math.min(...relOnsets))
      / (4 * composition.ticks_per_quarter);
  }
  let formPosition = 0;
  if (composition.bar_count > 1) {
    formPosition = (startBar - 1) / (composition.bar_count - 1);
  }
  return {
    scope_kind: 'motif',
    start_tick: startTick,
    end_tick: endTick,
    start_bar: startBar,
    end_bar_inclusive: endBar,
    section_index: null,
    section_type: null,
    form_position: Math.max(0, Math.min(1, formPosition)),
    motif_relative_pitch_span: Math.max(0, Math.min(1, pitchSpan)),
    motif_relative_rhythm_span: Math.max(0, Math.min(1, rhythmSpan)),
    notes,
  };
}

/**
 * @param {object} composition
 * @param {object} scope
 */
export function resolveEmbedWindow(composition, scope) {
  const boundaries = compileBarBoundaries({
    timeSignature: composition.time_signature,
    ticksPerQuarter: composition.ticks_per_quarter,
    barCount: composition.bar_count,
    durationTicks: composition.duration_ticks,
    timeSignatureChanges: composition.time_signature_changes || [],
  });
  if (!boundaries) {
    throw Object.assign(new Error('invalid timeline'), { code: 'embed_scope_invalid' });
  }
  const kind = scope?.kind || 'composition';
  if (kind === 'composition') {
    return windowForTickRange(composition, {
      startTick: 0,
      endTick: composition.duration_ticks,
      startBar: 1,
      endBarInclusive: composition.bar_count,
      scopeKind: 'composition',
      sectionIndex: null,
      sectionType: null,
      formPosition: 0.5,
    });
  }
  if (kind === 'section') return resolveSectionWindow(composition, boundaries, scope);
  if (kind === 'bar_range') return resolveBarRangeWindow(composition, boundaries, scope);
  if (kind === 'motif') return resolveMotifWindow(composition, boundaries, scope);
  throw Object.assign(new Error('unsupported scope'), { code: 'embed_scope_invalid' });
}

function buildFeatureVector(window, { barTicks, ppq }) {
  const notes = window.notes;
  const n = notes.length;
  const pitched = notes.filter((note) => !note.is_drum);
  const sample = pitched.length ? pitched : notes.slice();

  const pc = new Array(PC_DIMS).fill(0);
  for (const note of sample) {
    pc[note.pitch_midi % 12] += 1;
  }
  normalizeHist(pc);

  const midis = sample.map((note) => note.pitch_midi);
  const rangeFeats = [
    Math.min(...midis) / 127,
    Math.max(...midis) / 127,
    (midis.reduce((a, b) => a + b, 0) / midis.length) / 127,
  ];

  const dur = new Array(DUR_BINS).fill(0);
  for (const note of notes) {
    dur[durationBin(note.duration_ticks / ppq)] += 1;
  }
  normalizeHist(dur);

  const onset = new Array(ONSET_BINS).fill(0);
  const safeBar = Math.max(1, barTicks);
  for (const note of notes) {
    const mod = note.start_tick % safeBar;
    const idx = Math.min(ONSET_BINS - 1, Math.floor((mod / safeBar) * ONSET_BINS));
    onset[idx] += 1;
  }
  normalizeHist(onset);

  const barSpan = Math.max(1, window.end_bar_inclusive - window.start_bar + 1);
  const density = [Math.min(1, (n / barSpan) / 32)];

  const interval = new Array(INTERVAL_BINS).fill(0);
  const contour = [0, 0, 0];
  const ordered = sample.slice().sort((a, b) => (
    a.start_tick - b.start_tick || a.pitch_midi - b.pitch_midi
  ));
  for (let i = 0; i < ordered.length - 1; i += 1) {
    const left = ordered[i];
    const right = ordered[i + 1];
    const delta = right.pitch_midi - left.pitch_midi;
    const clamped = Math.max(-12, Math.min(12, delta));
    interval[clamped + 12] += 1;
    if (delta > 0) contour[0] += 1;
    else if (delta < 0) contour[1] += 1;
    else contour[2] += 1;
  }
  normalizeHist(interval);
  normalizeHist(contour);

  const concurrent = new Array(CONCURRENT_BINS).fill(0);
  for (const note of notes) {
    let overlap = 0;
    for (const other of notes) {
      if (
        other.start_tick < note.start_tick + note.duration_ticks
        && note.start_tick < other.start_tick + other.duration_ticks
      ) {
        overlap += 1;
      }
    }
    concurrent[concurrentBin(overlap)] += 1;
  }
  normalizeHist(concurrent);

  const roleCounts = Object.fromEntries(ROLE_ORDER.map((name) => [name, 0]));
  const trackIds = new Set();
  for (const note of notes) {
    roleCounts[roleCounts[note.role] != null ? note.role : 'other'] += 1;
    trackIds.add(note.track_id);
  }
  const roleFeats = ROLE_ORDER.map((name) => roleCounts[name]);
  normalizeHist(roleFeats);
  const trackCount = [Math.min(1, trackIds.size / 8)];

  const formPos = [window.form_position];
  const sectionOneHot = new Array(SECTION_TYPE_DIMS).fill(0);
  let stype = (window.section_type || 'other').trim().toLowerCase();
  if (!SECTION_TYPE_ORDER.includes(stype)) stype = 'other';
  sectionOneHot[SECTION_TYPE_ORDER.indexOf(stype)] = 1;

  const motifExtra = [
    window.motif_relative_pitch_span,
    window.motif_relative_rhythm_span,
  ];

  return pc
    .concat(rangeFeats)
    .concat(dur)
    .concat(onset)
    .concat(density)
    .concat(interval)
    .concat(contour)
    .concat(concurrent)
    .concat(roleFeats)
    .concat(trackCount)
    .concat(formPos)
    .concat(sectionOneHot)
    .concat(motifExtra);
}

/**
 * Count notes across tracks (for max_note_count gate).
 * @param {object} composition
 */
export function countCompositionNotes(composition) {
  let total = 0;
  for (const track of composition?.tracks || []) {
    total += (track.events || []).length;
  }
  return total;
}

/**
 * Compute `composition.embedding.v1` card via browser CPU twin.
 * @param {object} composition
 * @param {object} [scope]
 * @returns {Promise<object>}
 */
export async function embedCompositionScopeBrowser(composition, scope = { kind: 'composition' }) {
  const resolvedScope = scope && typeof scope === 'object' ? scope : { kind: 'composition' };
  const fingerprint = await compositionSourceFingerprint(composition);
  const digest = await embedScopeDigest(resolvedScope);
  const memoKey = `${SYMBOLIC_FEATURES_ALGORITHM_VERSION}|${fingerprint}|${digest}`;
  if (sessionMemo.has(memoKey)) {
    log.debug('Symbolic twin memo hit', {
      dims: SYMBOLIC_FEATURES_DIMS,
      fingerprintPrefix: fingerprint.slice(0, 12),
    });
    return sessionMemo.get(memoKey);
  }

  const window = resolveEmbedWindow(composition, resolvedScope);
  if (!window.notes.length) {
    throw Object.assign(new Error('empty embed scope'), {
      code: FALLBACK_REASONS.TWIN_EMPTY_SCOPE,
    });
  }

  const barTicks = barDurationTicks(composition.time_signature, composition.ticks_per_quarter);
  if (barTicks == null) {
    throw Object.assign(new Error('invalid meter'), { code: 'embed_scope_invalid' });
  }
  const ppq = Math.max(1, Number(composition.ticks_per_quarter) || 480);
  const raw = buildFeatureVector(window, { barTicks, ppq });
  if (raw.length !== SYMBOLIC_FEATURES_DIMS) {
    throw Object.assign(new Error(`dims mismatch ${raw.length}`), {
      code: FALLBACK_REASONS.HOST_ERROR,
    });
  }
  const vector = l2Normalize(raw);
  const card = {
    schema_version: 'composition.embedding.v1',
    profile_id: SYMBOLIC_FEATURES_PROFILE_ID,
    algorithm_version: SYMBOLIC_FEATURES_ALGORITHM_VERSION,
    model_id: BROWSER_SYMBOLIC_FEATURES_MODEL_ID,
    dims: vector.length,
    distance_metric: SYMBOLIC_FEATURES_DISTANCE_METRIC,
    projection_id: SYMBOLIC_FEATURES_PROJECTION_NONE,
    projection_digest: null,
    source_fingerprint: fingerprint,
    scope: resolvedScope,
    scope_digest: digest,
    note_count: window.notes.length,
    vector,
    artist_label_used: false,
  };
  sessionMemo.set(memoKey, card);
  log.debug('Symbolic twin embedded', {
    dims: card.dims,
    note_count: card.note_count,
    fingerprintPrefix: fingerprint.slice(0, 12),
  });
  return card;
}

/** Test helper: clear session memo. */
export function clearSymbolicFeaturesMemoForTests() {
  sessionMemo.clear();
}

/**
 * Cosine similarity for L2-normalized vectors (CPU).
 * @param {number[]} a
 * @param {number[]} b
 */
export function cosineSimilarityCpu(a, b) {
  const n = Math.min(a.length, b.length);
  let dot = 0;
  for (let i = 0; i < n; i += 1) {
    dot += a[i] * b[i];
  }
  return dot;
}

/**
 * Batched cosine: query vs rows of matrix (each row length = dims).
 * @param {number[]} query
 * @param {number[][]} matrix
 * @returns {number[]}
 */
export function batchedCosineCpu(query, matrix) {
  return matrix.map((row) => cosineSimilarityCpu(query, row));
}
