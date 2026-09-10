import assert from 'node:assert/strict';
import test from 'node:test';

import {
  EDIT_FINGERPRINT_PROFILE,
  compositionEditFingerprint,
  findDevelopmentCandidateById,
  normalizeDevelopmentPreviewResponse,
  normalizeDevelopmentRequest,
  resolveDevelopmentDefaults,
  verifyDevelopmentCandidate,
} from './compositionCandidates.js';

function minimalEditDocument() {
  return {
    schema_version: 'composition.v2',
    tempo: 100,
    key: 'C major',
    time_signature: '4/4',
    ticks_per_quarter: 480,
    duration_ticks: 3840,
    bar_count: 2,
    sections: [
      {
        id: null,
        type: 'intro',
        label: null,
        start_bar: 1,
        bar_count: 2,
        start_tick: 0,
        duration_ticks: 3840,
      },
    ],
    tracks: [
      {
        id: 'piano-1',
        name: 'Piano',
        instrument: 'piano',
        role: 'harmony',
        midi_program: 0,
        channel: 1,
        is_drum: false,
        volume: 100,
        pan: 0,
        expression: 127,
        staff: null,
        events: [],
        dynamic_marks: [],
        sustain_pedals: [],
        automation: [],
      },
    ],
    harmony: [],
    tempo_changes: [],
    time_signature_changes: [],
    key_changes: [],
    markers: [],
    motifs: [],
  };
}

function sixteenBarA() {
  const eventsMelody = [];
  const eventsBass = [];
  for (let bar = 0; bar < 16; bar += 1) {
    const start = bar * 1920;
    eventsMelody.push({
      id: `m-${bar}`,
      pitch: bar % 2 === 0 ? 'C4' : 'E4',
      start_tick: start,
      duration_ticks: 480,
      velocity: 80,
    });
    eventsBass.push({
      id: `b-${bar}`,
      pitch: 'C2',
      start_tick: start,
      duration_ticks: 1920,
      velocity: 70,
    });
  }
  return {
    schema_version: 'composition.v2',
    tempo: 100,
    key: 'C major',
    time_signature: '4/4',
    ticks_per_quarter: 480,
    bar_count: 16,
    duration_ticks: 16 * 1920,
    sections: [{
      id: 'a',
      type: 'verse',
      label: 'A',
      start_bar: 1,
      bar_count: 16,
      start_tick: 0,
      duration_ticks: 16 * 1920,
    }],
    tracks: [
      {
        id: 'melody-1',
        name: 'Melody',
        instrument: 'piano',
        role: 'melody',
        midi_program: 0,
        channel: 1,
        events: eventsMelody,
      },
      {
        id: 'bass-1',
        name: 'Bass',
        instrument: 'bass',
        role: 'bass',
        midi_program: 32,
        channel: 2,
        events: eventsBass,
      },
    ],
    harmony: [],
    tempo_changes: [],
    time_signature_changes: [],
    key_changes: [],
    markers: [],
    motifs: [],
  };
}

test('compositionEditFingerprint matches backend golden vector', async () => {
  const fingerprint = await compositionEditFingerprint(minimalEditDocument());
  assert.equal(EDIT_FINGERPRINT_PROFILE, 'composition.edit.v1');
  assert.equal(
    fingerprint,
    'ce1949b5c7d942e79b1dc0cbb47da43964f4f0f0fa549468b8b6785e264860d8',
  );
  const again = await compositionEditFingerprint(minimalEditDocument());
  assert.equal(fingerprint, again);
});

test('edit fingerprint changes when markers differ', async () => {
  const base = minimalEditDocument();
  const withMarker = {
    ...structuredClone(base),
    markers: [{ tick: 0, kind: 'rehearsal', label: 'A' }],
  };
  assert.notEqual(
    await compositionEditFingerprint(base),
    await compositionEditFingerprint(withMarker),
  );
});

test('normalizeDevelopmentRequest enforces operation matrix', () => {
  const composition = sixteenBarA();
  const ok = normalizeDevelopmentRequest({
    composition,
    operation: 'continue',
    output_bars: 8,
    variation_strength: 'balanced',
    candidate_count: 2,
  });
  assert.equal(ok.ok, true);
  assert.equal(ok.request.output_bars, 8);

  const varyMissing = normalizeDevelopmentRequest({
    composition,
    operation: 'vary_section',
    variation_strength: 'balanced',
  });
  assert.equal(varyMissing.ok, false);
  assert.equal(varyMissing.code, 'development_source_required');

  const tooMany = normalizeDevelopmentRequest({
    composition,
    operation: 'continue',
    output_bars: 8,
    variation_strength: 'balanced',
    candidate_count: 5,
  });
  assert.equal(tooMany.ok, false);
});

test('normalizeDevelopmentPreviewResponse rejects duplicates and malformed', () => {
  const composition = sixteenBarA();
  const candidateComposition = structuredClone(composition);
  candidateComposition.bar_count = 24;
  candidateComposition.duration_ticks = 24 * 1920;
  candidateComposition.sections = [
    ...composition.sections,
    {
      id: 'cont',
      type: 'verse',
      start_bar: 17,
      bar_count: 8,
      start_tick: 16 * 1920,
      duration_ticks: 8 * 1920,
    },
  ];
  for (const track of candidateComposition.tracks) {
    for (let bar = 16; bar < 24; bar += 1) {
      track.events.push({
        id: `${track.id}-x-${bar}`,
        pitch: 'G4',
        start_tick: bar * 1920,
        duration_ticks: 480,
        velocity: 70,
      });
    }
  }
  const good = {
    edit_source_fingerprint: 'a'.repeat(64),
    operation: 'continue',
    development_intent: 'continue',
    variation_strength: 'balanced',
    requested_candidate_count: 2,
    candidates: [
      {
        candidate_id: 'cand-alpha-1',
        candidate_fingerprint: 'b'.repeat(64),
        edit_source_fingerprint: 'a'.repeat(64),
        composition: candidateComposition,
        preservation: [],
        output_range: { start_tick: 30720, end_tick: 46080 },
      },
      {
        candidate_id: 'cand-beta-2',
        candidate_fingerprint: 'c'.repeat(64),
        edit_source_fingerprint: 'a'.repeat(64),
        composition: structuredClone(candidateComposition),
        preservation: [],
        output_range: { start_tick: 30720, end_tick: 46080 },
      },
    ],
    warning_codes: [],
    provider: 'fake',
  };
  const normalized = normalizeDevelopmentPreviewResponse(good);
  assert.equal(normalized.ok, true, normalized.message);

  const dup = structuredClone(good);
  dup.candidates[1].candidate_id = 'cand-alpha-1';
  assert.equal(normalizeDevelopmentPreviewResponse(dup).ok, false);

  const missingId = structuredClone(good);
  delete missingId.candidates[0].candidate_id;
  assert.equal(normalizeDevelopmentPreviewResponse(missingId).ok, false);
});

test('findDevelopmentCandidateById selects by id not index', () => {
  const candidates = [
    { candidate_id: 'a', composition: { bar_count: 1 } },
    { candidate_id: 'b', composition: { bar_count: 2 } },
  ];
  assert.equal(findDevelopmentCandidateById(candidates, 'b').composition.bar_count, 2);
  assert.equal(findDevelopmentCandidateById(candidates, 'missing'), null);
});

test('resolveDevelopmentDefaults uses trailing section and AI selection for vary', () => {
  const composition = sixteenBarA();
  const cont = resolveDevelopmentDefaults(composition, { operation: 'continue' });
  assert.equal(cont.outputBars, 8);
  assert.equal(cont.sourceStartBar, 1);
  assert.equal(cont.sourceEndBar, 16);

  const vary = resolveDevelopmentDefaults(composition, {
    operation: 'vary_section',
    aiEditStartBar: 9,
    aiEditEndBar: 12,
  });
  assert.equal(vary.sourceStartBar, 9);
  assert.equal(vary.sourceEndBar, 12);
  assert.equal(vary.outputBars, null);
});

test('verifyDevelopmentCandidate accepts exact append prefix', async () => {
  const base = sixteenBarA();
  const candidateComposition = structuredClone(base);
  candidateComposition.bar_count = 24;
  candidateComposition.duration_ticks = 24 * 1920;
  candidateComposition.sections[0] = { ...base.sections[0] };
  candidateComposition.sections.push({
    id: 'cont',
    type: 'verse',
    start_bar: 17,
    bar_count: 8,
    start_tick: 16 * 1920,
    duration_ticks: 8 * 1920,
  });
  for (const track of candidateComposition.tracks) {
    for (let bar = 16; bar < 24; bar += 1) {
      track.events.push({
        id: `${track.id}-${bar}`,
        pitch: track.role === 'bass' ? 'C2' : 'G4',
        start_tick: bar * 1920,
        duration_ticks: 480,
        velocity: 70,
      });
    }
  }

  const sourceFp = await compositionEditFingerprint(base);
  const candFp = await compositionEditFingerprint(candidateComposition);
  const result = await verifyDevelopmentCandidate({
    baseComposition: base,
    candidate: {
      candidate_id: 'x',
      candidate_fingerprint: candFp,
      edit_source_fingerprint: sourceFp,
      composition: candidateComposition,
      preservation: [{ code: 'immutable_prefix', required: true, passed: true }],
      output_range: { start_tick: 16 * 1920, end_tick: 24 * 1920 },
    },
    operation: 'continue',
    responseSourceFingerprint: sourceFp,
  });
  assert.equal(result.ok, true, JSON.stringify(result.failures));
});

test('verifyDevelopmentCandidate rejects satisfied=false preservation assertion', async () => {
  const base = sixteenBarA();
  const candidateComposition = structuredClone(base);
  const sourceFp = await compositionEditFingerprint(base);
  const candidateFp = await compositionEditFingerprint(candidateComposition);
  const result = await verifyDevelopmentCandidate({
    baseComposition: base,
    candidate: {
      candidate_id: 'x',
      candidate_fingerprint: candidateFp,
      edit_source_fingerprint: sourceFp,
      composition: candidateComposition,
      preservation: [{
        assertion: 'melody_events_exact',
        satisfied: false,
        severity: 'required',
        required: true,
      }],
      output_range: { start_tick: 0, end_tick: 1920 },
    },
    operation: 'vary_section',
    responseSourceFingerprint: sourceFp,
  });
  assert.equal(result.ok, false);
  assert.ok(result.failures.some((item) => (
    item.code === 'melody_events_exact' || item.code === 'required_assertion_failed'
  )));
});
