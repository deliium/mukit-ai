import assert from 'node:assert/strict';
import test from 'node:test';

import { setAppLogLevelForTests } from './appLogger.js';
import { SUPPORTED_TRACK_ROLES } from './musicJsonValidation.js';
import { compositionEditFingerprint } from './compositionCandidates.js';
import {
  ARRANGEMENT_OPERATIONS,
  ARRANGEMENT_CATALOG_VERSION,
  ARRANGEMENT_RANGE_POLICY_VERSION,
  cacheArrangementCatalog,
  clearArrangementCatalogCache,
  computeArrangementTopologyDiff,
  findArrangementCandidateById,
  normalizeArrangementCatalog,
  normalizeArrangementPreviewResponse,
  normalizeArrangementRequest,
  normalizeRejectedAttempt,
  verifyArrangementCandidate,
  verifyArrangementCandidateForApply,
} from './compositionArrangementCandidates.js';

function track({
  id,
  name = id,
  instrument = 'piano',
  role = 'melody',
  midi_program = 0,
  channel = 1,
  events = [],
}) {
  return {
    id,
    name,
    instrument,
    role,
    midi_program,
    channel,
    is_drum: false,
    volume: 100,
    pan: 0,
    expression: 127,
    staff: null,
    events,
    dynamic_marks: [],
    sustain_pedals: [],
    automation: [],
  };
}

function note(id, pitch, start = 0, duration = 480, velocity = 80) {
  return { id, pitch, start_tick: start, duration_ticks: duration, velocity };
}

function arrangementSource() {
  return {
    schema_version: 'composition.v2',
    tempo: 100,
    key: 'C major',
    time_signature: '4/4',
    ticks_per_quarter: 480,
    duration_ticks: 3840,
    bar_count: 2,
    sections: [{
      id: 'a',
      type: 'verse',
      label: 'A',
      start_bar: 1,
      bar_count: 2,
      start_tick: 0,
      duration_ticks: 3840,
    }],
    tracks: [
      track({
        id: 'melody-1',
        name: 'Melody',
        instrument: 'piano',
        role: 'melody',
        midi_program: 0,
        channel: 1,
        events: [note('m1', 'C4', 0), note('m2', 'E4', 1920)],
      }),
      track({
        id: 'harmony-1',
        name: 'Accomp',
        instrument: 'piano',
        role: 'harmony',
        midi_program: 0,
        channel: 2,
        events: [note('h1', 'C3', 0, 1920), note('h2', 'G3', 1920, 1920)],
      }),
      track({
        id: 'bass-1',
        name: 'Bass',
        instrument: 'acoustic_bass',
        role: 'bass',
        midi_program: 32,
        channel: 3,
        events: [note('b1', 'C2', 0, 3840)],
      }),
    ],
    harmony: [{ chord: 'C', start_tick: 0, duration_ticks: 3840 }],
    tempo_changes: [],
    time_signature_changes: [],
    key_changes: [],
    markers: [],
    motifs: [],
  };
}

function inventoryFrom(composition) {
  return (composition.tracks || []).map((item) => ({
    track_id: item.id,
    instrument: item.instrument,
    role: item.role,
    midi_program: item.midi_program ?? null,
    event_count: (item.events || []).length,
    part_id: null,
  }));
}

function sampleCatalogResponse(overrides = {}) {
  const instruments = overrides.instruments || [
    {
      instrument_id: 'acoustic_grand_piano',
      display_name: 'Acoustic Grand Piano',
      aliases: ['piano'],
      midi_program: 0,
      gm_family: 'piano',
      compatibility_identity: 'piano',
      compatibility_family: 'keyboard',
      is_drum: false,
      range_policy: 'absolute',
      playable_low: 21,
      playable_high: 108,
      preferred_low: 36,
      preferred_high: 96,
      suggested_roles: ['melody', 'harmony'],
      fingerprint: 'p'.repeat(64),
    },
    {
      instrument_id: 'cello',
      display_name: 'Cello',
      aliases: [],
      midi_program: 42,
      gm_family: 'strings',
      compatibility_identity: 'cello',
      compatibility_family: 'strings',
      is_drum: false,
      range_policy: 'absolute',
      playable_low: 36,
      playable_high: 84,
      preferred_low: 36,
      preferred_high: 72,
      suggested_roles: ['melody', 'bass', 'harmony'],
      fingerprint: 'c'.repeat(64),
    },
    {
      instrument_id: 'string_ensemble_1',
      display_name: 'String Ensemble',
      aliases: ['strings'],
      midi_program: 48,
      gm_family: 'ensemble',
      compatibility_identity: 'string_ensemble',
      compatibility_family: 'strings',
      is_drum: false,
      range_policy: 'absolute',
      playable_low: 36,
      playable_high: 96,
      preferred_low: 48,
      preferred_high: 84,
      suggested_roles: ['harmony', 'pad'],
      fingerprint: 's'.repeat(64),
    },
    {
      instrument_id: 'acoustic_bass',
      display_name: 'Acoustic Bass',
      aliases: ['bass'],
      midi_program: 32,
      gm_family: 'bass',
      compatibility_identity: 'acoustic_bass',
      compatibility_family: 'bass',
      is_drum: false,
      range_policy: 'absolute',
      playable_low: 28,
      playable_high: 60,
      preferred_low: 28,
      preferred_high: 55,
      suggested_roles: ['bass'],
      fingerprint: 'b'.repeat(64),
    },
  ];
  return {
    catalog_version: ARRANGEMENT_CATALOG_VERSION,
    range_policy_version: ARRANGEMENT_RANGE_POLICY_VERSION,
    fingerprint: 'f'.repeat(64),
    source_path_category: 'packaged',
    ...overrides,
    instruments: overrides.instruments || instruments,
    track_roles: overrides.track_roles || [...SUPPORTED_TRACK_ROLES],
  };
}

function baseParts() {
  return {
    before: [
      {
        part_id: 'p-melody',
        instrument_id: 'acoustic_grand_piano',
        role: 'melody',
        source_track_ids: ['melody-1'],
        doubling_policy: 'none',
      },
      {
        part_id: 'p-harmony',
        instrument_id: 'acoustic_grand_piano',
        role: 'harmony',
        source_track_ids: ['harmony-1'],
        doubling_policy: 'none',
      },
      {
        part_id: 'p-bass',
        instrument_id: 'acoustic_bass',
        role: 'bass',
        source_track_ids: ['bass-1'],
        doubling_policy: 'none',
      },
    ],
    after: [
      {
        part_id: 'p-melody',
        instrument_id: 'acoustic_grand_piano',
        role: 'melody',
        source_track_ids: ['melody-1'],
        doubling_policy: 'none',
      },
      {
        part_id: 'p-cello',
        instrument_id: 'cello',
        role: 'bass',
        source_track_ids: ['bass-1'],
        doubling_policy: 'none',
      },
      {
        part_id: 'p-strings',
        instrument_id: 'string_ensemble_1',
        role: 'harmony',
        source_track_ids: ['harmony-1'],
        doubling_policy: 'none',
      },
    ],
  };
}

function validRequest(overrides = {}) {
  const composition = overrides.composition || arrangementSource();
  return {
    composition,
    operation: 'piano_to_ensemble',
    source_track_ids: ['melody-1', 'harmony-1', 'bass-1'],
    protected_track_ids: [],
    instrumentation: baseParts(),
    preserve_melody: true,
    preserve_harmony: true,
    range_adjustment: 'reject',
    candidate_count: 1,
    ...overrides,
  };
}

async function buildValidCandidate(source, { catalogFingerprint = 'f'.repeat(64) } = {}) {
  const composition = structuredClone(source);
  // Re-instrument bass to cello; keep melody/harmony topology IDs.
  const bass = composition.tracks.find((item) => item.id === 'bass-1');
  bass.instrument = 'cello';
  bass.midi_program = 42;
  bass.name = 'Cello';

  const sourceFp = await compositionEditFingerprint(source);
  const candidateFp = await compositionEditFingerprint(composition);
  return {
    candidate_id: 'arr-candidate-0001',
    candidate_fingerprint: candidateFp,
    edit_source_fingerprint: sourceFp,
    algorithm_version: 'composition.arrangement.v1',
    catalog_version: ARRANGEMENT_CATALOG_VERSION,
    range_policy_version: ARRANGEMENT_RANGE_POLICY_VERSION,
    catalog_fingerprint: catalogFingerprint,
    target_profile_fingerprints: [
      { instrument_id: 'cello', profile_fingerprint: 'c'.repeat(64) },
    ],
    operation: 'piano_to_ensemble',
    composition,
    provider: 'fake',
    model: 'fake-deterministic',
    before_inventory: inventoryFrom(source),
    after_inventory: inventoryFrom(composition),
    manifest: {
      retained_track_ids: ['melody-1', 'harmony-1'],
      removed_track_ids: [],
      added_track_ids: [],
      reordered_track_ids: [],
      reinstrumented_track_ids: ['bass-1'],
      split_track_ids: [],
      merged_track_ids: [],
      source_to_target: [
        { source_track_id: 'melody-1', target_track_id: 'melody-1', relationship: 'retained' },
        { source_track_id: 'harmony-1', target_track_id: 'harmony-1', relationship: 'retained' },
        { source_track_id: 'bass-1', target_track_id: 'bass-1', relationship: 'reinstrumented' },
      ],
    },
    event_counts: {
      copied: 5,
      moved: 0,
      generated: 0,
      removed: 0,
      octave_adjusted: 0,
      unchanged: 5,
    },
    density: null,
    range_findings: [],
    duplicate_findings: [],
    harmony_compatibility: null,
    assertions: [
      {
        kind: 'melody_preservation',
        satisfied: true,
        required: true,
        detail: 'melody preserved',
        track_id: null,
      },
      {
        kind: 'topology_authorization',
        satisfied: true,
        required: true,
        detail: 'topology authorized',
        track_id: null,
      },
    ],
    warning_codes: [],
  };
}

test('normalizeArrangementCatalog accepts role-list parity and fingerprints', () => {
  clearArrangementCatalogCache();
  const result = normalizeArrangementCatalog(sampleCatalogResponse());
  assert.equal(result.ok, true);
  assert.equal(result.catalog.catalog_version, ARRANGEMENT_CATALOG_VERSION);
  assert.equal(result.catalog.fingerprint.length, 64);
  assert.equal(result.catalog.instruments.length, 4);
  for (const role of SUPPORTED_TRACK_ROLES) {
    assert.ok(result.catalog.track_roles.includes(role));
  }
  cacheArrangementCatalog(result.catalog);
  assert.equal(result.catalog.instrumentById.cello.fingerprint, 'c'.repeat(64));
});

test('normalizeArrangementCatalog rejects missing role or fingerprint change shape', () => {
  const missingRole = sampleCatalogResponse({
    track_roles: [...SUPPORTED_TRACK_ROLES].filter((role) => role !== 'countermelody'),
  });
  const bad = normalizeArrangementCatalog(missingRole);
  assert.equal(bad.ok, false);
  assert.equal(bad.code, 'arrangement_catalog_unavailable');

  const noFp = sampleCatalogResponse();
  noFp.instruments[0].fingerprint = 'short';
  assert.equal(normalizeArrangementCatalog(noFp).ok, false);
});

test('every arrangement operation normalizes a valid request', () => {
  const source = arrangementSource();
  for (const operation of ARRANGEMENT_OPERATIONS) {
    let instrumentation = baseParts();
    const overrides = {
      operation,
      composition: source,
      instrumentation,
      source_track_ids: ['melody-1', 'harmony-1', 'bass-1'],
      protected_track_ids: [],
      candidate_count: 1,
    };

    if (operation === 'change_instrumentation') {
      instrumentation = {
        before: baseParts().before,
        after: baseParts().before.map((part, index) => (
          index === 0
            ? { ...part, instrument_id: 'cello' }
            : part
        )),
      };
      overrides.instrumentation = instrumentation;
      overrides.source_track_ids = ['melody-1'];
    } else if (operation === 'add_accompaniment') {
      overrides.instrumentation = {
        before: baseParts().before,
        after: [
          ...baseParts().before,
          {
            part_id: 'p-pad',
            instrument_id: 'string_ensemble_1',
            role: 'pad',
            source_track_ids: [],
            doubling_policy: 'none',
          },
        ],
      };
    } else if (operation === 'remove_accompaniment') {
      overrides.instrumentation = {
        before: [
          {
            part_id: 'p-harmony',
            instrument_id: 'acoustic_grand_piano',
            role: 'harmony',
            source_track_ids: ['harmony-1'],
            doubling_policy: 'none',
          },
        ],
        after: [
          {
            part_id: 'p-melody',
            instrument_id: 'acoustic_grand_piano',
            role: 'melody',
            source_track_ids: ['melody-1'],
            doubling_policy: 'none',
          },
        ],
      };
      overrides.source_track_ids = ['harmony-1'];
      overrides.protected_track_ids = ['melody-1'];
    } else if (operation === 'create_countermelody') {
      overrides.instrumentation = {
        before: baseParts().before,
        after: [
          ...baseParts().before,
          {
            part_id: 'p-counter',
            instrument_id: 'cello',
            role: 'countermelody',
            source_track_ids: [],
            doubling_policy: 'none',
          },
        ],
      };
    } else if (operation === 'double_melody') {
      overrides.instrumentation = {
        before: [baseParts().before[0]],
        after: [
          baseParts().before[0],
          {
            part_id: 'p-double',
            instrument_id: 'cello',
            role: 'melody',
            source_track_ids: ['melody-1'],
            doubling_policy: 'octave',
          },
        ],
      };
      overrides.source_track_ids = ['melody-1'];
    } else if (
      operation === 'simplify_arrangement'
      || operation === 'increase_texture_density'
      || operation === 'decrease_texture_density'
    ) {
      // before/after may be identical for density ops
      overrides.instrumentation = {
        before: baseParts().before,
        after: baseParts().before.map((part) => ({ ...part })),
      };
    }

    const result = normalizeArrangementRequest(overrides);
    assert.equal(result.ok, true, `${operation}: ${result.message}`);
    assert.equal(result.request.operation, operation);
    assert.equal(result.request.preserve_melody, true);
    assert.equal(result.request.range_adjustment, 'reject');
  }
});

test('normalizeArrangementRequest rejects source/protected overlap and duplicate ids', () => {
  const overlap = normalizeArrangementRequest(validRequest({
    source_track_ids: ['melody-1', 'harmony-1'],
    protected_track_ids: ['harmony-1'],
  }));
  assert.equal(overlap.ok, false);
  assert.equal(overlap.code, 'arrangement_source_protected_overlap');

  const dupSource = normalizeArrangementRequest(validRequest({
    source_track_ids: ['melody-1', 'melody-1'],
  }));
  assert.equal(dupSource.ok, false);

  const dupPart = normalizeArrangementRequest(validRequest({
    instrumentation: {
      before: [
        { part_id: 'same', instrument_id: 'acoustic_grand_piano', role: 'melody', source_track_ids: ['melody-1'] },
        { part_id: 'same', instrument_id: 'cello', role: 'bass', source_track_ids: ['bass-1'] },
      ],
      after: baseParts().after,
    },
  }));
  assert.equal(dupPart.ok, false);
  assert.equal(dupPart.code, 'arrangement_inventory_mismatch');
});

test('normalizeArrangementRequest enforces one-row-per-part and unknown track ids', () => {
  const emptyBefore = normalizeArrangementRequest(validRequest({
    instrumentation: { before: [], after: baseParts().after },
  }));
  assert.equal(emptyBefore.ok, false);

  const unknown = normalizeArrangementRequest(validRequest({
    source_track_ids: ['missing-track'],
  }));
  assert.equal(unknown.ok, false);
  assert.equal(unknown.code, 'arrangement_invalid_source');
});

test('normalizeArrangementRequest is immutable on the inbound payload', () => {
  const payload = validRequest({
    instruction: '  keep the melody   recognizable  ',
    protected_track_ids: ['melody-1'],
    source_track_ids: ['harmony-1', 'bass-1'],
  });
  const snapshot = structuredClone(payload);
  const result = normalizeArrangementRequest(payload);
  assert.equal(result.ok, true);
  assert.deepEqual(payload, snapshot);
  assert.equal(result.request.instruction, 'keep the melody recognizable');
});

test('operation-specific request guards: piano/countermelody/doubling/noop', () => {
  assert.equal(
    normalizeArrangementRequest(validRequest({
      operation: 'piano_to_ensemble',
      instrumentation: {
        before: [{
          part_id: 'b1',
          instrument_id: 'cello',
          role: 'melody',
          source_track_ids: ['melody-1'],
        }],
        after: baseParts().after,
      },
    })).code,
    'arrangement_piano_required',
  );

  assert.equal(
    normalizeArrangementRequest(validRequest({
      operation: 'create_countermelody',
      instrumentation: {
        before: baseParts().before,
        after: baseParts().after,
      },
    })).code,
    'arrangement_countermelody_required',
  );

  assert.equal(
    normalizeArrangementRequest(validRequest({
      operation: 'double_melody',
      instrumentation: {
        before: [baseParts().before[0]],
        after: [
          baseParts().before[0],
          {
            part_id: 'x',
            instrument_id: 'cello',
            role: 'melody',
            source_track_ids: ['melody-1'],
            doubling_policy: 'none',
          },
        ],
      },
      source_track_ids: ['melody-1'],
    })).code,
    'arrangement_doubling_required',
  );

  assert.equal(
    normalizeArrangementRequest(validRequest({
      operation: 'change_instrumentation',
      instrumentation: {
        before: baseParts().before,
        after: baseParts().before.map((part) => ({ ...part })),
      },
    })).code,
    'arrangement_noop_instrumentation',
  );
});

test('normalizeArrangementPreviewResponse validates candidates and rejected attempts', async () => {
  const source = arrangementSource();
  const candidate = await buildValidCandidate(source);
  const response = {
    edit_source_fingerprint: candidate.edit_source_fingerprint,
    algorithm_version: 'composition.arrangement.v1',
    catalog_version: ARRANGEMENT_CATALOG_VERSION,
    range_policy_version: ARRANGEMENT_RANGE_POLICY_VERSION,
    catalog_fingerprint: candidate.catalog_fingerprint,
    operation: 'piano_to_ensemble',
    requested_candidate_count: 2,
    candidates: [candidate],
    rejected_attempts: [{
      ordinal: 2,
      stage: 'preservation',
      codes: ['arrangement_preservation_failed'],
      reasons: ['melody dropped'],
    }],
    warning_codes: ['candidate_partial_success'],
    provider: 'fake',
    model: 'fake-deterministic',
  };
  const normalized = normalizeArrangementPreviewResponse(response);
  assert.equal(normalized.ok, true);
  assert.equal(normalized.response.candidates.length, 1);
  assert.equal(normalized.response.rejected_attempts.length, 1);
  assert.equal(
    findArrangementCandidateById(normalized.response.candidates, 'arr-candidate-0001')?.operation,
    'piano_to_ensemble',
  );
});

test('normalizeRejectedAttempt rejects compositions and unknown stages', () => {
  assert.equal(
    normalizeRejectedAttempt({
      ordinal: 1,
      stage: 'range',
      codes: ['arrangement_range_failed'],
      composition: arrangementSource(),
    }).ok,
    false,
  );
  assert.equal(
    normalizeRejectedAttempt({
      ordinal: 1,
      stage: 'not-a-stage',
      codes: ['x'],
    }).ok,
    false,
  );
});

test('fingerprint parity: verifyArrangementCandidateForApply accepts valid topology change', async () => {
  clearArrangementCatalogCache();
  const catalog = normalizeArrangementCatalog(sampleCatalogResponse()).catalog;
  cacheArrangementCatalog(catalog);

  const source = arrangementSource();
  const candidate = await buildValidCandidate(source);
  const request = normalizeArrangementRequest(validRequest({ composition: source })).request;

  const verified = await verifyArrangementCandidateForApply(source, candidate, {
    catalog,
    request,
    responseSourceFingerprint: candidate.edit_source_fingerprint,
  });
  assert.equal(verified.ok, true, JSON.stringify(verified.failures));
  assert.ok(verified.topologyDiff.reinstrumented_track_ids.includes('bass-1'));
});

test('rejects unexplained topology, tampered metadata, and protected changes', async () => {
  clearArrangementCatalogCache();
  const catalog = normalizeArrangementCatalog(sampleCatalogResponse()).catalog;
  cacheArrangementCatalog(catalog);
  const source = arrangementSource();
  const request = normalizeArrangementRequest(validRequest({
    composition: source,
    source_track_ids: ['harmony-1', 'bass-1'],
    protected_track_ids: ['melody-1'],
  })).request;

  const unexplained = await buildValidCandidate(source);
  unexplained.manifest.retained_track_ids = ['melody-1'];
  unexplained.manifest.reinstrumented_track_ids = [];
  const unexplainedResult = await verifyArrangementCandidate({
    baseComposition: source,
    candidate: unexplained,
    request,
    responseSourceFingerprint: unexplained.edit_source_fingerprint,
    loadedCatalog: catalog,
  });
  assert.equal(unexplainedResult.ok, false);
  assert.ok(unexplainedResult.failures.some((item) => item.code === 'unexplained_base_track'
    || item.code === 'actual_reinstrument_not_in_manifest'));

  const tampered = await buildValidCandidate(source);
  tampered.composition.tempo = 120;
  tampered.candidate_fingerprint = await compositionEditFingerprint(tampered.composition);
  const tamperedResult = await verifyArrangementCandidateForApply(source, tampered, {
    catalog,
    request,
    responseSourceFingerprint: tampered.edit_source_fingerprint,
  });
  assert.equal(tamperedResult.ok, false);
  assert.ok(tamperedResult.failures.some((item) => item.code === 'root_metadata_changed'));

  const protectedChanged = await buildValidCandidate(source);
  protectedChanged.composition.tracks[0].events[0].pitch = 'D4';
  protectedChanged.candidate_fingerprint = await compositionEditFingerprint(
    protectedChanged.composition,
  );
  const protectedResult = await verifyArrangementCandidateForApply(source, protectedChanged, {
    catalog,
    request,
    responseSourceFingerprint: protectedChanged.edit_source_fingerprint,
  });
  assert.equal(protectedResult.ok, false);
  assert.ok(protectedResult.failures.some((item) => item.code === 'protected_track_changed'));
});

test('rejects fingerprint mismatch, stale catalog, failed assertions, and summary contradictions', async () => {
  clearArrangementCatalogCache();
  const catalog = normalizeArrangementCatalog(sampleCatalogResponse()).catalog;
  cacheArrangementCatalog(catalog);
  const source = arrangementSource();
  const request = normalizeArrangementRequest(validRequest({ composition: source })).request;

  const badFp = await buildValidCandidate(source);
  badFp.candidate_fingerprint = '0'.repeat(64);
  const fpResult = await verifyArrangementCandidateForApply(source, badFp, {
    catalog,
    request,
    responseSourceFingerprint: badFp.edit_source_fingerprint,
  });
  assert.equal(fpResult.ok, false);
  assert.ok(fpResult.failures.some((item) => item.code === 'candidate_fingerprint_mismatch'));

  const staleCatalog = await buildValidCandidate(source, { catalogFingerprint: 'z'.repeat(64) });
  const staleResult = await verifyArrangementCandidateForApply(source, staleCatalog, {
    catalog,
    request,
    responseSourceFingerprint: staleCatalog.edit_source_fingerprint,
  });
  assert.equal(staleResult.ok, false);
  assert.ok(staleResult.failures.some((item) => item.code === 'stale_catalog_fingerprint'));

  const failedAssertion = await buildValidCandidate(source);
  failedAssertion.assertions = [{
    kind: 'melody_preservation',
    satisfied: false,
    required: true,
    detail: 'failed',
    track_id: null,
  }];
  const assertionResult = await verifyArrangementCandidateForApply(source, failedAssertion, {
    catalog,
    request,
    responseSourceFingerprint: failedAssertion.edit_source_fingerprint,
  });
  assert.equal(assertionResult.ok, false);
  assert.ok(assertionResult.failures.some((item) => item.code === 'melody_preservation'));

  const summaryBad = await buildValidCandidate(source);
  summaryBad.after_inventory = summaryBad.after_inventory.map((item) => ({
    ...item,
    event_count: item.event_count + 9,
  }));
  const summaryResult = await verifyArrangementCandidateForApply(source, summaryBad, {
    catalog,
    request,
    responseSourceFingerprint: summaryBad.edit_source_fingerprint,
  });
  assert.equal(summaryResult.ok, false);
  assert.ok(summaryResult.failures.some((item) => item.code === 'inventory_event_count_mismatch'));
});

test('intentional doubling topology with declared policy is accepted', async () => {
  clearArrangementCatalogCache();
  const catalog = normalizeArrangementCatalog(sampleCatalogResponse()).catalog;
  cacheArrangementCatalog(catalog);
  const source = arrangementSource();
  const composition = structuredClone(source);
  composition.tracks.push(track({
    id: 'melody-double',
    name: 'Cello Double',
    instrument: 'cello',
    role: 'melody',
    midi_program: 42,
    channel: 4,
    events: composition.tracks[0].events.map((event, index) => ({
      ...event,
      id: `d${index}`,
      pitch: event.pitch.replace('4', '3'),
    })),
  }));

  const request = normalizeArrangementRequest({
    composition: source,
    operation: 'double_melody',
    source_track_ids: ['melody-1'],
    protected_track_ids: [],
    instrumentation: {
      before: [baseParts().before[0]],
      after: [
        baseParts().before[0],
        {
          part_id: 'p-double',
          instrument_id: 'cello',
          role: 'melody',
          source_track_ids: ['melody-1'],
          doubling_policy: 'octave',
        },
      ],
    },
  }).request;

  const candidate = {
    candidate_id: 'arr-double-candidate1',
    candidate_fingerprint: await compositionEditFingerprint(composition),
    edit_source_fingerprint: await compositionEditFingerprint(source),
    algorithm_version: 'composition.arrangement.v1',
    catalog_version: ARRANGEMENT_CATALOG_VERSION,
    range_policy_version: ARRANGEMENT_RANGE_POLICY_VERSION,
    catalog_fingerprint: catalog.fingerprint,
    target_profile_fingerprints: [
      { instrument_id: 'cello', profile_fingerprint: 'c'.repeat(64) },
    ],
    operation: 'double_melody',
    composition,
    provider: 'fake',
    model: null,
    before_inventory: inventoryFrom(source),
    after_inventory: inventoryFrom(composition),
    manifest: {
      retained_track_ids: ['melody-1', 'harmony-1', 'bass-1'],
      removed_track_ids: [],
      added_track_ids: ['melody-double'],
      reordered_track_ids: [],
      reinstrumented_track_ids: [],
      split_track_ids: [],
      merged_track_ids: [],
      source_to_target: [
        { source_track_id: 'melody-1', target_track_id: 'melody-1', relationship: 'retained' },
        { source_track_id: 'melody-1', target_track_id: 'melody-double', relationship: 'doubled' },
        { source_track_id: 'harmony-1', target_track_id: 'harmony-1', relationship: 'retained' },
        { source_track_id: 'bass-1', target_track_id: 'bass-1', relationship: 'retained' },
      ],
    },
    event_counts: {
      copied: 2,
      moved: 0,
      generated: 0,
      removed: 0,
      octave_adjusted: 0,
      unchanged: 5,
    },
    density: null,
    range_findings: [{
      severity: 'warning',
      code: 'questionable_range',
      track_id: 'melody-double',
      instrument_id: 'cello',
      detail: 'preferred range advisory',
    }],
    duplicate_findings: [{
      severity: 'info',
      code: 'declared_doubling',
      source_track_id: 'melody-1',
      target_track_id: 'melody-double',
      detail: 'declared octave doubling',
    }],
    harmony_compatibility: null,
    assertions: [{
      kind: 'declared_doubling',
      satisfied: true,
      required: true,
      detail: 'doubling authorized',
      track_id: 'melody-double',
    }],
    warning_codes: ['questionable_range', 'declared_doubling_applied'],
  };

  const verified = await verifyArrangementCandidateForApply(source, candidate, {
    catalog,
    request,
    responseSourceFingerprint: candidate.edit_source_fingerprint,
  });
  assert.equal(verified.ok, true, JSON.stringify(verified.failures));
});

test('range hard failures reject apply; questionable_range alone does not', async () => {
  clearArrangementCatalogCache();
  const catalog = normalizeArrangementCatalog(sampleCatalogResponse()).catalog;
  cacheArrangementCatalog(catalog);
  const source = arrangementSource();
  const request = normalizeArrangementRequest(validRequest({ composition: source })).request;

  const hard = await buildValidCandidate(source);
  hard.range_findings = [{
    severity: 'error',
    code: 'absolute_out_of_range',
    track_id: 'bass-1',
    instrument_id: 'cello',
    detail: 'below playable',
  }];
  const hardResult = await verifyArrangementCandidateForApply(source, hard, {
    catalog,
    request,
    responseSourceFingerprint: hard.edit_source_fingerprint,
  });
  assert.equal(hardResult.ok, false);
  assert.ok(hardResult.failures.some((item) => item.code === 'range_hard_failure'));

  const warnOnly = await buildValidCandidate(source);
  warnOnly.range_findings = [{
    severity: 'warning',
    code: 'questionable_range',
    track_id: 'bass-1',
    instrument_id: 'cello',
    detail: 'outside preferred',
  }];
  const warnResult = await verifyArrangementCandidateForApply(source, warnOnly, {
    catalog,
    request,
    responseSourceFingerprint: warnOnly.edit_source_fingerprint,
  });
  assert.equal(warnResult.ok, true, JSON.stringify(warnResult.failures));
});

test('event field tampering on unselected track is rejected', async () => {
  clearArrangementCatalogCache();
  const catalog = normalizeArrangementCatalog(sampleCatalogResponse()).catalog;
  cacheArrangementCatalog(catalog);
  const source = arrangementSource();
  const request = normalizeArrangementRequest(validRequest({
    composition: source,
    operation: 'change_instrumentation',
    source_track_ids: ['bass-1'],
    protected_track_ids: [],
    instrumentation: {
      before: [baseParts().before[2]],
      after: [{
        part_id: 'p-bass',
        instrument_id: 'cello',
        role: 'bass',
        source_track_ids: ['bass-1'],
        doubling_policy: 'none',
      }],
    },
  })).request;

  const candidate = await buildValidCandidate(source);
  // Tamper an unselected melody event while claiming bass reinstrument only.
  candidate.composition.tracks[0].events[0].velocity = 11;
  candidate.candidate_fingerprint = await compositionEditFingerprint(candidate.composition);
  candidate.manifest = {
    retained_track_ids: ['melody-1', 'harmony-1'],
    removed_track_ids: [],
    added_track_ids: [],
    reordered_track_ids: [],
    reinstrumented_track_ids: ['bass-1'],
    split_track_ids: [],
    merged_track_ids: [],
    source_to_target: [
      { source_track_id: 'melody-1', target_track_id: 'melody-1', relationship: 'retained' },
      { source_track_id: 'harmony-1', target_track_id: 'harmony-1', relationship: 'retained' },
      { source_track_id: 'bass-1', target_track_id: 'bass-1', relationship: 'reinstrumented' },
    ],
  };
  candidate.before_inventory = inventoryFrom(source);
  candidate.after_inventory = inventoryFrom(candidate.composition);
  candidate.operation = 'change_instrumentation';

  const result = await verifyArrangementCandidateForApply(source, candidate, {
    catalog,
    request,
    responseSourceFingerprint: candidate.edit_source_fingerprint,
  });
  assert.equal(result.ok, false);
  assert.ok(result.failures.some((item) => item.code === 'unselected_track_changed'));
});

test('same-instrument different roles stay independent in request and verify', async () => {
  clearArrangementCatalogCache();
  const catalog = normalizeArrangementCatalog(sampleCatalogResponse()).catalog;
  cacheArrangementCatalog(catalog);
  const source = arrangementSource();
  // Two piano parts with distinct roles are not treated as duplicates.
  const request = normalizeArrangementRequest({
    composition: source,
    operation: 'change_instrumentation',
    source_track_ids: ['melody-1', 'harmony-1'],
    protected_track_ids: ['bass-1'],
    instrumentation: {
      before: [
        {
          part_id: 'p-melody',
          instrument_id: 'acoustic_grand_piano',
          role: 'melody',
          source_track_ids: ['melody-1'],
          doubling_policy: 'none',
        },
        {
          part_id: 'p-harmony',
          instrument_id: 'acoustic_grand_piano',
          role: 'harmony',
          source_track_ids: ['harmony-1'],
          doubling_policy: 'none',
        },
      ],
      after: [
        {
          part_id: 'p-melody',
          instrument_id: 'acoustic_grand_piano',
          role: 'melody',
          source_track_ids: ['melody-1'],
          doubling_policy: 'none',
        },
        {
          part_id: 'p-harmony',
          instrument_id: 'string_ensemble_1',
          role: 'harmony',
          source_track_ids: ['harmony-1'],
          doubling_policy: 'none',
        },
      ],
    },
  });
  assert.equal(request.ok, true, request.message);
  assert.equal(request.request.instrumentation.before[0].instrument_id, 'acoustic_grand_piano');
  assert.equal(request.request.instrumentation.before[1].instrument_id, 'acoustic_grand_piano');
  assert.notEqual(
    request.request.instrumentation.before[0].role,
    request.request.instrumentation.before[1].role,
  );

  const composition = structuredClone(source);
  const harmony = composition.tracks.find((item) => item.id === 'harmony-1');
  harmony.instrument = 'strings';
  harmony.midi_program = 48;
  const candidate = {
    ...(await buildValidCandidate(source)),
    operation: 'change_instrumentation',
    composition,
    candidate_fingerprint: await compositionEditFingerprint(composition),
    before_inventory: inventoryFrom(source),
    after_inventory: inventoryFrom(composition),
    target_profile_fingerprints: [
      { instrument_id: 'string_ensemble_1', profile_fingerprint: 's'.repeat(64) },
    ],
    manifest: {
      retained_track_ids: ['melody-1', 'bass-1'],
      removed_track_ids: [],
      added_track_ids: [],
      reordered_track_ids: [],
      reinstrumented_track_ids: ['harmony-1'],
      split_track_ids: [],
      merged_track_ids: [],
      source_to_target: [
        { source_track_id: 'melody-1', target_track_id: 'melody-1', relationship: 'retained' },
        { source_track_id: 'harmony-1', target_track_id: 'harmony-1', relationship: 'reinstrumented' },
        { source_track_id: 'bass-1', target_track_id: 'bass-1', relationship: 'retained' },
      ],
    },
  };
  const verified = await verifyArrangementCandidateForApply(source, candidate, {
    catalog,
    request: request.request,
    responseSourceFingerprint: candidate.edit_source_fingerprint,
  });
  assert.equal(verified.ok, true, JSON.stringify(verified.failures));
});

test('accidental undeclared doubling fails required assertion gate', async () => {
  clearArrangementCatalogCache();
  const catalog = normalizeArrangementCatalog(sampleCatalogResponse()).catalog;
  cacheArrangementCatalog(catalog);
  const source = arrangementSource();
  const composition = structuredClone(source);
  composition.tracks.push(track({
    id: 'melody-clone',
    name: 'Accidental Clone',
    instrument: 'cello',
    role: 'melody',
    midi_program: 42,
    channel: 4,
    events: composition.tracks[0].events.map((event, index) => ({
      ...event,
      id: `clone-${index}`,
    })),
  }));
  const request = normalizeArrangementRequest(validRequest({ composition: source })).request;
  const candidate = {
    ...(await buildValidCandidate(source)),
    composition,
    candidate_fingerprint: await compositionEditFingerprint(composition),
    before_inventory: inventoryFrom(source),
    after_inventory: inventoryFrom(composition),
    manifest: {
      retained_track_ids: ['melody-1', 'harmony-1', 'bass-1'],
      removed_track_ids: [],
      added_track_ids: ['melody-clone'],
      reordered_track_ids: [],
      reinstrumented_track_ids: [],
      split_track_ids: [],
      merged_track_ids: [],
      source_to_target: [
        { source_track_id: 'melody-1', target_track_id: 'melody-1', relationship: 'retained' },
        { source_track_id: 'harmony-1', target_track_id: 'harmony-1', relationship: 'retained' },
        { source_track_id: 'bass-1', target_track_id: 'bass-1', relationship: 'retained' },
        { source_track_id: 'melody-1', target_track_id: 'melody-clone', relationship: 'doubled' },
      ],
    },
    duplicate_findings: [{
      severity: 'error',
      code: 'accidental_clone',
      source_track_id: 'melody-1',
      target_track_id: 'melody-clone',
      detail: 'undeclared doubling',
    }],
    assertions: [{
      kind: 'declared_doubling',
      satisfied: false,
      required: true,
      detail: 'doubling was not authorized',
      track_id: 'melody-clone',
    }],
    warning_codes: ['candidate_failed_duplicate'],
  };
  const result = await verifyArrangementCandidateForApply(source, candidate, {
    catalog,
    request,
    responseSourceFingerprint: candidate.edit_source_fingerprint,
  });
  assert.equal(result.ok, false);
  assert.ok(result.failures.some((item) => item.code === 'declared_doubling'));
});

test('topology add/remove/reorder manifests verify independently', async () => {
  clearArrangementCatalogCache();
  const catalog = normalizeArrangementCatalog(sampleCatalogResponse()).catalog;
  cacheArrangementCatalog(catalog);
  const source = arrangementSource();

  // Remove accompaniment, add pad, reorder remaining tracks.
  const composition = structuredClone(source);
  composition.tracks = composition.tracks.filter((item) => item.id !== 'harmony-1');
  composition.tracks.push(track({
    id: 'pad-new',
    name: 'Pad',
    instrument: 'strings',
    role: 'pad',
    midi_program: 48,
    channel: 4,
    events: [note('pad1', 'G3', 0, 3840)],
  }));
  // Reorder: bass, melody, pad
  const byId = Object.fromEntries(composition.tracks.map((item) => [item.id, item]));
  composition.tracks = [byId['bass-1'], byId['melody-1'], byId['pad-new']];

  const request = normalizeArrangementRequest({
    composition: source,
    operation: 'add_accompaniment',
    source_track_ids: ['harmony-1'],
    protected_track_ids: ['melody-1', 'bass-1'],
    instrumentation: {
      before: [baseParts().before[1]],
      after: [
        {
          part_id: 'p-pad',
          instrument_id: 'string_ensemble_1',
          role: 'pad',
          source_track_ids: [],
          doubling_policy: 'none',
        },
      ],
    },
  }).request;

  const candidate = {
    candidate_id: 'arr-topology-addrm1',
    candidate_fingerprint: await compositionEditFingerprint(composition),
    edit_source_fingerprint: await compositionEditFingerprint(source),
    algorithm_version: 'composition.arrangement.v1',
    catalog_version: ARRANGEMENT_CATALOG_VERSION,
    range_policy_version: ARRANGEMENT_RANGE_POLICY_VERSION,
    catalog_fingerprint: catalog.fingerprint,
    target_profile_fingerprints: [
      { instrument_id: 'string_ensemble_1', profile_fingerprint: 's'.repeat(64) },
    ],
    operation: 'add_accompaniment',
    composition,
    provider: 'fake',
    model: null,
    before_inventory: inventoryFrom(source),
    after_inventory: inventoryFrom(composition),
    manifest: {
      retained_track_ids: ['melody-1', 'bass-1'],
      removed_track_ids: ['harmony-1'],
      added_track_ids: ['pad-new'],
      reordered_track_ids: ['bass-1', 'melody-1', 'pad-new'],
      reinstrumented_track_ids: [],
      split_track_ids: [],
      merged_track_ids: [],
      source_to_target: [
        { source_track_id: 'melody-1', target_track_id: 'melody-1', relationship: 'retained' },
        { source_track_id: 'bass-1', target_track_id: 'bass-1', relationship: 'retained' },
        { source_track_id: 'harmony-1', target_track_id: 'harmony-1', relationship: 'removed' },
        { source_track_id: 'harmony-1', target_track_id: 'pad-new', relationship: 'redistributed' },
      ],
    },
    event_counts: {
      copied: 3,
      moved: 0,
      generated: 1,
      removed: 2,
      octave_adjusted: 0,
      unchanged: 3,
    },
    density: null,
    range_findings: [],
    duplicate_findings: [],
    harmony_compatibility: null,
    assertions: [{
      kind: 'topology_authorization',
      satisfied: true,
      required: true,
      detail: 'ok',
      track_id: null,
    }],
    warning_codes: [],
  };

  const verified = await verifyArrangementCandidateForApply(source, candidate, {
    catalog,
    request,
    responseSourceFingerprint: candidate.edit_source_fingerprint,
  });
  assert.equal(verified.ok, true, JSON.stringify(verified.failures));
  const diff = computeArrangementTopologyDiff(source, composition);
  assert.deepEqual(diff.removed_track_ids, ['harmony-1']);
  assert.deepEqual(diff.added_track_ids, ['pad-new']);
  assert.equal(composition.tracks[0].id, 'bass-1');
});

test('motifs remain valid on apply; broken motif references reject', async () => {
  clearArrangementCatalogCache();
  const catalog = normalizeArrangementCatalog(sampleCatalogResponse()).catalog;
  cacheArrangementCatalog(catalog);
  const source = arrangementSource();
  source.tracks[0].events = [
    note('m1', 'C4', 0),
    note('m2', 'D4', 480),
    note('m3', 'E4', 960),
  ];
  source.motifs = [{
    id: 'motif-a',
    label: 'Motif A',
    occurrences: [{
      id: 'occ-orig',
      track_id: 'melody-1',
      event_ids: ['m1', 'm2', 'm3'],
      relationship: 'original',
    }],
  }];
  const request = normalizeArrangementRequest(validRequest({ composition: source })).request;

  const okCandidate = await buildValidCandidate(source);
  okCandidate.composition.motifs = structuredClone(source.motifs);
  okCandidate.candidate_fingerprint = await compositionEditFingerprint(okCandidate.composition);
  okCandidate.before_inventory = inventoryFrom(source);
  okCandidate.after_inventory = inventoryFrom(okCandidate.composition);
  const okResult = await verifyArrangementCandidateForApply(source, okCandidate, {
    catalog,
    request,
    responseSourceFingerprint: okCandidate.edit_source_fingerprint,
  });
  assert.equal(okResult.ok, true, JSON.stringify(okResult.failures));

  const broken = structuredClone(okCandidate);
  broken.composition.motifs[0].occurrences[0].event_ids = ['missing-a', 'missing-b', 'missing-c'];
  broken.candidate_fingerprint = await compositionEditFingerprint(broken.composition);
  const brokenResult = await verifyArrangementCandidateForApply(source, broken, {
    catalog,
    request,
    responseSourceFingerprint: broken.edit_source_fingerprint,
  });
  assert.equal(brokenResult.ok, false);
  assert.ok(brokenResult.failures.some((item) => item.code === 'motif_integrity_failed'));
});

test('empty harmony and variable meter are preserved; mutations reject', async () => {
  clearArrangementCatalogCache();
  const catalog = normalizeArrangementCatalog(sampleCatalogResponse()).catalog;
  cacheArrangementCatalog(catalog);

  const emptyHarmony = arrangementSource();
  emptyHarmony.harmony = [];
  const emptyRequest = normalizeArrangementRequest(validRequest({
    composition: emptyHarmony,
  })).request;
  const emptyCandidate = await buildValidCandidate(emptyHarmony);
  const emptyOk = await verifyArrangementCandidateForApply(emptyHarmony, emptyCandidate, {
    catalog,
    request: emptyRequest,
    responseSourceFingerprint: emptyCandidate.edit_source_fingerprint,
  });
  assert.equal(emptyOk.ok, true, JSON.stringify(emptyOk.failures));

  const emptyTampered = await buildValidCandidate(emptyHarmony);
  emptyTampered.composition.harmony = [{ chord: 'G', start_tick: 0, duration_ticks: 3840 }];
  emptyTampered.candidate_fingerprint = await compositionEditFingerprint(emptyTampered.composition);
  const emptyBad = await verifyArrangementCandidateForApply(emptyHarmony, emptyTampered, {
    catalog,
    request: emptyRequest,
    responseSourceFingerprint: emptyTampered.edit_source_fingerprint,
  });
  assert.equal(emptyBad.ok, false);
  assert.ok(emptyBad.failures.some((item) => item.code === 'harmony_metadata_changed'));

  const variable = arrangementSource();
  variable.time_signature_changes = [
    { tick: 0, time_signature: '4/4' },
    { tick: 1920, time_signature: '3/4' },
  ];
  variable.duration_ticks = 3360;
  variable.bar_count = 2;
  variable.sections[0].duration_ticks = 3360;
  const variableRequest = normalizeArrangementRequest(validRequest({
    composition: variable,
  })).request;
  const variableCandidate = await buildValidCandidate(variable);
  const variableOk = await verifyArrangementCandidateForApply(variable, variableCandidate, {
    catalog,
    request: variableRequest,
    responseSourceFingerprint: variableCandidate.edit_source_fingerprint,
  });
  assert.equal(variableOk.ok, true, JSON.stringify(variableOk.failures));

  const meterTampered = await buildValidCandidate(variable);
  meterTampered.composition.time_signature_changes = [
    { tick: 0, time_signature: '4/4' },
  ];
  meterTampered.candidate_fingerprint = await compositionEditFingerprint(meterTampered.composition);
  const meterBad = await verifyArrangementCandidateForApply(variable, meterTampered, {
    catalog,
    request: variableRequest,
    responseSourceFingerprint: meterTampered.edit_source_fingerprint,
  });
  assert.equal(meterBad.ok, false);
  assert.ok(meterBad.failures.some((item) => item.code === 'root_metadata_changed'));
});

test('inventory count mismatch rejects apply', async () => {
  clearArrangementCatalogCache();
  const catalog = normalizeArrangementCatalog(sampleCatalogResponse()).catalog;
  cacheArrangementCatalog(catalog);
  const source = arrangementSource();
  const request = normalizeArrangementRequest(validRequest({ composition: source })).request;
  const candidate = await buildValidCandidate(source);
  candidate.after_inventory = candidate.after_inventory.slice(0, 1);
  const result = await verifyArrangementCandidateForApply(source, candidate, {
    catalog,
    request,
    responseSourceFingerprint: candidate.edit_source_fingerprint,
  });
  assert.equal(result.ok, false);
  assert.ok(result.failures.some((item) => item.code === 'inventory_count_mismatch'));
});

test('frontend fingerprint vectors match locally recomputed source and candidate digests', async () => {
  clearArrangementCatalogCache();
  const catalog = normalizeArrangementCatalog(sampleCatalogResponse()).catalog;
  cacheArrangementCatalog(catalog);
  const source = arrangementSource();
  const candidate = await buildValidCandidate(source);
  const request = normalizeArrangementRequest(validRequest({ composition: source })).request;

  const localSource = await compositionEditFingerprint(source);
  const localCandidate = await compositionEditFingerprint(candidate.composition);
  assert.equal(candidate.edit_source_fingerprint, localSource);
  assert.equal(candidate.candidate_fingerprint, localCandidate);

  const verified = await verifyArrangementCandidate({
    baseComposition: source,
    candidate,
    request,
    responseSourceFingerprint: localSource,
    loadedCatalog: catalog,
  });
  assert.equal(verified.ok, true, JSON.stringify(verified.failures));
  assert.equal(verified.localSourceFingerprint, localSource);
  assert.equal(verified.localCandidateFingerprint, localCandidate);
});

test('arrangement candidate logger gate suppresses and sanitizes console output', async () => {
  clearArrangementCatalogCache();
  const catalog = normalizeArrangementCatalog(sampleCatalogResponse()).catalog;
  cacheArrangementCatalog(catalog);
  const source = arrangementSource();
  const candidate = await buildValidCandidate(source);
  const request = normalizeArrangementRequest(validRequest({
    composition: source,
    instruction: 'secret arrange instruction with C4 pitch',
  })).request;

  const lines = [];
  const originalDebug = console.debug;
  const originalInfo = console.info;
  console.debug = (...args) => { lines.push(JSON.stringify(args)); };
  console.info = (...args) => { lines.push(JSON.stringify(args)); };

  try {
    setAppLogLevelForTests('silent');
    lines.length = 0;
    await verifyArrangementCandidateForApply(source, candidate, {
      catalog,
      request,
      responseSourceFingerprint: candidate.edit_source_fingerprint,
    });
    assert.equal(lines.length, 0);

    setAppLogLevelForTests('debug');
    lines.length = 0;
    await verifyArrangementCandidateForApply(source, candidate, {
      catalog,
      request,
      responseSourceFingerprint: candidate.edit_source_fingerprint,
    });
    assert.ok(lines.length >= 1);
    const joined = lines.join('\n');
    assert.equal(joined.includes('secret arrange'), false);
    assert.equal(joined.includes('C4'), false);
    assert.equal(joined.includes('"events"'), false);
    assert.equal(joined.includes(candidate.candidate_id), false);
    assert.equal(joined.includes('m1'), false);
    assert.match(joined, /operation|failureCount|candidatePrefix|ok/i);
  } finally {
    console.debug = originalDebug;
    console.info = originalInfo;
    setAppLogLevelForTests(null);
  }
});
