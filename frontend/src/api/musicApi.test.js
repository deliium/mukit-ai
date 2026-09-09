import assert from 'node:assert/strict';
import test from 'node:test';

import axios from 'axios';
import { migrateV1ToV2 } from '../utils/compositionVersion.js';
import {
  AnalysisApiError,
  analyzeComposition,
  applyMotif,
  ArrangementApiError,
  DevelopmentApiError,
  editCompositionRegion,
  fetchArrangementInstruments,
  generateLlmMusicJson,
  importMidi,
  importMusicXml,
  ImportApiError,
  loadArrangementInstruments,
  MotifApiError,
  parseProjectionHeaders,
  previewCompositionArrangement,
  previewCompositionDevelopment,
  previewReharmonization,
  projectionWarningsFromHeaders,
  ReharmonizeApiError,
  resetArrangementInstrumentCache,
} from './musicApi.js';
import {
  ARRANGEMENT_ALGORITHM_VERSION,
  ARRANGEMENT_CATALOG_VERSION,
  ARRANGEMENT_RANGE_POLICY_VERSION,
  clearArrangementCatalogCache,
  getCachedArrangementCatalog,
} from '../utils/compositionArrangementCandidates.js';
import { compositionEditFingerprint } from '../utils/compositionCandidates.js';
import { SUPPORTED_TRACK_ROLES } from '../utils/musicJsonValidation.js';

function canonicalV1Composition() {
  return {
    schema_version: 'composition.v1',
    tempo: 100,
    key: 'C major',
    time_signature: '4/4',
    ticks_per_quarter: 480,
    bar_count: 2,
    duration_ticks: 3840,
    sections: [{ type: 'intro', start_bar: 1, bar_count: 2, start_tick: 0, duration_ticks: 3840 }],
    tracks: [
      {
        id: 'melody-1',
        name: 'Melody',
        instrument: 'piano',
        role: 'melody',
        midi_program: 0,
        channel: 1,
        events: [
          { type: 'note', pitch: 'C4', start_tick: 0, duration_ticks: 480, velocity: 90 },
          { type: 'note', pitch: 'E4', start_tick: 1920, duration_ticks: 480, velocity: 90 },
        ],
      },
    ],
    harmony: [{ bar: 1, chord: 'C' }],
  };
}

test('parseProjectionHeaders handles missing and malformed values safely', () => {
  assert.deepEqual(parseProjectionHeaders({}), {
    status: 'exact',
    issues: [],
    exactCount: 0,
    approximatedCount: 0,
    omittedCount: 0,
    failedCount: 0,
    hasIssues: false,
  });
  const parsed = parseProjectionHeaders({
    'X-Mukit-Projection-Status': 'omitted',
    'X-Mukit-Projection-Issues': 'automation_omitted_from_notation,sustain_projected',
    'X-Mukit-Projection-Omitted-Count': '2',
    'X-Mukit-Projection-Approximated-Count': 'bad',
  });
  assert.equal(parsed.status, 'omitted');
  assert.deepEqual(parsed.issues, ['automation_omitted_from_notation', 'sustain_projected']);
  assert.equal(parsed.omittedCount, 2);
  assert.equal(parsed.approximatedCount, 0);
  assert.equal(projectionWarningsFromHeaders({
    'X-Mukit-Projection-Issues': 'automation_omitted_from_notation',
  }).length, 1);
});

test('generateLlmMusicJson normalizes v1 responses to v2', async (t) => {
  const previousAdapter = axios.defaults.adapter;
  axios.defaults.adapter = async () => ({
    data: {
      music: canonicalV1Composition(),
      musicxml: '<score/>',
      warnings: [],
    },
    status: 200,
    statusText: 'OK',
    headers: {},
    config: {},
  });
  t.after(() => {
    axios.defaults.adapter = previousAdapter;
  });

  const response = await generateLlmMusicJson({});
  assert.equal(response.music.schema_version, 'composition.v2');
});

test('editCompositionRegion validates response composition and patch', async (t) => {
  const previousAdapter = axios.defaults.adapter;
  axios.defaults.adapter = async (config) => {
    assert.equal(String(config.method || 'get').toLowerCase(), 'post');
    assert.equal(config.url, '/llm/edit-composition-region');
    const payload = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
    assert.equal(payload.composition.schema_version, 'composition.v2');
    return {
      data: {
        composition: canonicalV1Composition(),
        patch: {
          schema_version: 'composition.v2',
          operation: 'replace_region',
          start_bar: 1,
          end_bar: 1,
          target_track_ids: ['melody-1'],
          replace_tracks: [],
          added_tracks: [],
          warnings: [],
        },
        provider: 'openai',
        model: 'test-model',
        musicxml: '<score/>',
        warnings: ['ok'],
      },
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    };
  };
  t.after(() => {
    axios.defaults.adapter = previousAdapter;
  });

  const response = await editCompositionRegion({
    composition: canonicalV1Composition(),
    edit: {
      instruction: 'make this phrase more dramatic but keep the harmony',
      selection: { start_bar: 1, end_bar: 1, track_ids: ['melody-1'] },
    },
    selection: { provider: 'openai', model: 'test-model' },
  });

  assert.equal(response.composition.schema_version, 'composition.v2');
  assert.equal(response.patch.operation, 'replace_region');
  assert.equal(response.warnings.length, 1);
});

test('editCompositionRegion rejects missing replace_region patch', async (t) => {
  const previousAdapter = axios.defaults.adapter;
  axios.defaults.adapter = async () => ({
    data: {
      composition: migrateV1ToV2(canonicalV1Composition()),
      patch: null,
      provider: 'openai',
      model: 'test-model',
      warnings: [],
    },
    status: 200,
    statusText: 'OK',
    headers: {},
    config: {},
  });
  t.after(() => {
    axios.defaults.adapter = previousAdapter;
  });

  await assert.rejects(
    () => editCompositionRegion({
      composition: canonicalV1Composition(),
      edit: {
        instruction: 'edit',
        selection: { start_bar: 1, end_bar: 1, track_ids: ['melody-1'] },
      },
    }),
    /replace_region patch/,
  );
});

test('importMidi posts FormData without forcing multipart boundary', async (t) => {
  const previousAdapter = axios.defaults.adapter;
  axios.defaults.adapter = async (config) => {
    assert.equal(String(config.method || 'get').toLowerCase(), 'post');
    assert.equal(config.url, '/imports/midi');
    assert.ok(config.data instanceof FormData);
    const contentType = String(
      config.headers?.['Content-Type']
      ?? config.headers?.['content-type']
      ?? '',
    );
    assert.notEqual(contentType, 'application/x-www-form-urlencoded');
    if (contentType) {
      assert.ok(
        contentType === 'false'
        || contentType.includes('multipart/form-data')
        || contentType === 'undefined',
        `unexpected Content-Type for FormData upload: ${contentType}`,
      );
    }
    return {
      data: {
        composition: migrateV1ToV2(canonicalV1Composition()),
        musicxml: '<score-partwise/>',
        import_report: {
          status: 'exact',
          issues: [],
          summary: {
            detected_format: 'midi',
            display_filename: 'demo.mid',
            input_bytes: 12,
            target_ppq: 480,
            source_track_count: 1,
            result_track_count: 1,
            source_note_count: 2,
            result_note_count: 2,
            bar_count: 2,
            duration_ticks: 3840,
          },
        },
        notation_report: { status: 'exact', issue_codes: [] },
      },
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    };
  };
  t.after(() => {
    axios.defaults.adapter = previousAdapter;
  });

  const file = { name: 'demo.mid', size: 4 };
  const response = await importMidi(file);
  assert.equal(response.composition.schema_version, 'composition.v2');
  assert.equal(response.import_report.status, 'exact');
  assert.equal(response.notation_report.status, 'exact');
  assert.match(response.musicxml, /score-partwise/);
});

test('importMusicXml preserves structured error code and status', async (t) => {
  const previousAdapter = axios.defaults.adapter;
  axios.defaults.adapter = async () => {
    const error = new Error('Request failed');
    error.response = {
      status: 415,
      data: {
        detail: {
          code: 'import_unsupported_media_type',
          message: 'Content signature does not match MusicXML import endpoint',
          details: { detected: 'midi' },
        },
      },
    };
    throw error;
  };
  t.after(() => {
    axios.defaults.adapter = previousAdapter;
  });

  const file = { name: 'bad.musicxml', size: 4 };
  await assert.rejects(
    () => importMusicXml(file),
    (error) => {
      assert.ok(error instanceof ImportApiError);
      assert.equal(error.status, 415);
      assert.equal(error.code, 'import_unsupported_media_type');
      assert.match(error.message, /Content signature/);
      assert.equal(error.details?.detected, 'midi');
      return true;
    },
  );
});

function canonicalV2Composition() {
  return migrateV1ToV2(canonicalV1Composition());
}

function sampleAnalysisReport(overrides = {}) {
  return {
    schema_version: 'composition.analysis.v1',
    algorithm_version: 'native-v1',
    source_schema_version: 'composition.v2',
    source_fingerprint: 'a'.repeat(64),
    status: 'ok',
    resolved_scope: {
      kind: 'composition',
      start_tick: 0,
      end_tick: 3840,
      start_bar: 1,
      end_bar_exclusive: 3,
    },
    warnings: [],
    section_summaries: [],
    tonality: { status: 'ok' },
    harmony: { status: 'ok' },
    ...overrides,
  };
}

function installAxiosStub(handler) {
  const previousAdapter = axios.defaults.adapter;
  axios.defaults.adapter = handler;
  return () => {
    axios.defaults.adapter = previousAdapter;
  };
}

test('analyzeComposition posts composition scope with complete V2 payload', async (t) => {
  const composition = canonicalV2Composition();
  let posted = null;
  const restore = installAxiosStub(async (config) => {
    assert.equal(String(config.method || 'get').toLowerCase(), 'post');
    assert.equal(config.url, '/analysis/composition');
    posted = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
    return {
      data: sampleAnalysisReport(),
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    };
  });
  t.after(restore);

  const report = await analyzeComposition(composition, { kind: 'composition' });
  assert.equal(posted.composition.schema_version, 'composition.v2');
  assert.ok(Array.isArray(posted.composition.tracks));
  assert.ok(posted.composition.tracks[0].events.length >= 1);
  assert.deepEqual(posted.scope, { kind: 'composition' });
  assert.equal(report.schema_version, 'composition.analysis.v1');
  assert.equal(report.resolved_scope.kind, 'composition');
  assert.deepEqual(report.warnings, []);
  assert.deepEqual(report.section_summaries, []);
});

test('analyzeComposition accepts section and track scopes', async (t) => {
  const composition = canonicalV2Composition();
  const seen = [];
  const restore = installAxiosStub(async (config) => {
    const payload = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
    seen.push(payload.scope);
    return {
      data: sampleAnalysisReport({
        resolved_scope: {
          ...sampleAnalysisReport().resolved_scope,
          kind: payload.scope.kind,
        },
      }),
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    };
  });
  t.after(restore);

  const sectionReport = await analyzeComposition(composition, {
    kind: 'section',
    section_index: 0,
    expected_start_bar: composition.sections[0].start_bar,
    expected_bar_count: composition.sections[0].bar_count,
    expected_start_tick: composition.sections[0].start_tick,
    expected_duration_ticks: composition.sections[0].duration_ticks,
  });
  assert.equal(sectionReport.resolved_scope.kind, 'section');
  assert.equal(seen[0].kind, 'section');
  assert.equal(seen[0].section_index, 0);

  const trackReport = await analyzeComposition(composition, {
    kind: 'track',
    track_id: 'melody-1',
  });
  assert.equal(trackReport.resolved_scope.kind, 'track');
  assert.equal(seen[1].kind, 'track');
  assert.equal(seen[1].track_id, 'melody-1');
});

test('analyzeComposition rejects invalid local targets before network', async () => {
  const composition = canonicalV2Composition();
  await assert.rejects(
    () => analyzeComposition(composition, { kind: 'track', track_id: 'missing-track' }),
    (error) => {
      assert.ok(error instanceof AnalysisApiError);
      assert.equal(error.code, 'analysis_invalid_scope');
      return true;
    },
  );
  await assert.rejects(
    () => analyzeComposition(composition, { kind: 'section', section_index: 99 }),
    (error) => {
      assert.ok(error instanceof AnalysisApiError);
      assert.equal(error.code, 'analysis_invalid_scope');
      return true;
    },
  );
  await assert.rejects(
    () => analyzeComposition(composition, {
      kind: 'section',
      section_index: 0,
      expected_start_bar: 999,
    }),
    (error) => {
      assert.ok(error instanceof AnalysisApiError);
      assert.equal(error.code, 'analysis_invalid_scope');
      assert.ok(error.details?.mismatch_fields?.includes('start_bar'));
      return true;
    },
  );
});

test('analyzeComposition normalizes optional arrays and warnings', async (t) => {
  const composition = canonicalV2Composition();
  const restore = installAxiosStub(async (config) => ({
    data: sampleAnalysisReport({
      warnings: [
        { code: 'empty_analysis_scope', severity: 'warning', message: 'empty' },
        null,
        { severity: 'info' },
        { code: 'dense_overlapping_material' },
      ],
      section_summaries: undefined,
    }),
    status: 200,
    statusText: 'OK',
    headers: {},
    config,
  }));
  t.after(restore);

  const report = await analyzeComposition(composition, { kind: 'composition' });
  assert.equal(report.warnings.length, 2);
  assert.equal(report.warnings[0].code, 'empty_analysis_scope');
  assert.equal(report.warnings[1].code, 'dense_overlapping_material');
  assert.equal(report.warnings[1].severity, 'warning');
  assert.deepEqual(report.section_summaries, []);
});

test('analyzeComposition rejects malformed responses as AnalysisApiError', async (t) => {
  const composition = canonicalV2Composition();
  const restore = installAxiosStub(async (config) => ({
    data: {
      schema_version: 'wrong',
      algorithm_version: 'native-v1',
      source_schema_version: 'composition.v2',
      source_fingerprint: 'a'.repeat(64),
      status: 'ok',
      resolved_scope: { kind: 'composition' },
    },
    status: 200,
    statusText: 'OK',
    headers: {},
    config,
  }));
  t.after(restore);

  await assert.rejects(
    () => analyzeComposition(composition, { kind: 'composition' }),
    (error) => {
      assert.ok(error instanceof AnalysisApiError);
      assert.equal(error.code, 'analysis_invalid_response');
      assert.match(error.message, /schema_version/);
      return true;
    },
  );
});

function motifReadyComposition() {
  const composition = migrateV1ToV2(canonicalV1Composition());
  composition.bar_count = 4;
  composition.duration_ticks = 7680;
  composition.sections = [
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
  ];
  composition.tracks[0].events = [
    { id: 'n1', type: 'note', pitch: 'C4', start_tick: 0, duration_ticks: 480, velocity: 80 },
    { id: 'n2', type: 'note', pitch: 'D4', start_tick: 480, duration_ticks: 480, velocity: 80 },
    { id: 'n3', type: 'note', pitch: 'E4', start_tick: 960, duration_ticks: 480, velocity: 80 },
    { id: 'n4', type: 'note', pitch: 'F4', start_tick: 1440, duration_ticks: 480, velocity: 80 },
  ];
  composition.motifs = [{
    id: 'motif-a',
    label: 'Motif A',
    occurrences: [{
      id: 'occ-orig',
      track_id: 'melody-1',
      event_ids: ['n1', 'n2', 'n3'],
      relationship: 'original',
    }],
  }];
  return composition;
}

function sampleMotifApplyResponse(composition) {
  return {
    composition: {
      ...composition,
      motifs: [{
        ...composition.motifs[0],
        occurrences: [
          ...composition.motifs[0].occurrences,
          {
            id: 'occ-repeat-1',
            track_id: 'melody-1',
            event_ids: ['n5', 'n6', 'n7'],
            relationship: 'repeat',
            transform: { operation: 'repeat', source_occurrence_id: 'occ-orig' },
          },
        ],
      }],
      tracks: [{
        ...composition.tracks[0],
        events: [
          ...composition.tracks[0].events,
          { id: 'n5', type: 'note', pitch: 'C4', start_tick: 3840, duration_ticks: 480, velocity: 80 },
          { id: 'n6', type: 'note', pitch: 'D4', start_tick: 4320, duration_ticks: 480, velocity: 80 },
          { id: 'n7', type: 'note', pitch: 'E4', start_tick: 4800, duration_ticks: 480, velocity: 80 },
        ],
      }],
    },
    result: {
      motif_id: 'motif-a',
      source_occurrence_id: 'occ-orig',
      destination_section_id: 'chorus',
      destination_track_id: 'melody-1',
      destination_start_bar: 3,
      destination_start_tick: 3840,
      created_event_ids: ['n5', 'n6', 'n7'],
      new_occurrence_id: 'occ-repeat-1',
      relationship: 'repeat',
      identity_score: 1,
      provider: null,
      model: null,
      transform: {
        operation: 'repeat',
        source_occurrence_id: 'occ-orig',
      },
      diagnostics: {
        source_event_count: 3,
        created_event_count: 3,
        replaced_event_count: 0,
        destination_span_ticks: 1440,
        warning_codes: [],
      },
    },
    warnings: [],
    musicxml: '<score/>',
    musicxml_filename: 'composition-c-major-100bpm.musicxml',
  };
}

test('applyMotif posts exact payload and validates typed response', async (t) => {
  const composition = motifReadyComposition();
  let posted = null;
  const restore = installAxiosStub(async (config) => {
    assert.equal(String(config.method || 'get').toLowerCase(), 'post');
    assert.equal(config.url, '/motifs/apply');
    posted = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
    return {
      data: sampleMotifApplyResponse(composition),
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    };
  });
  t.after(restore);

  const response = await applyMotif({
    composition,
    source: { motif_id: 'motif-a', occurrence_id: 'occ-orig' },
    destination: { section_id: 'chorus', track_id: 'melody-1', start_bar: 3 },
    operation: 'repeat',
    parameters: {},
  });

  assert.equal(posted.composition.schema_version, 'composition.v2');
  assert.deepEqual(posted.source, { motif_id: 'motif-a', occurrence_id: 'occ-orig' });
  assert.equal(posted.destination.start_bar, 3);
  assert.equal(posted.operation, 'repeat');
  assert.equal(response.composition.schema_version, 'composition.v2');
  assert.equal(response.result.relationship, 'repeat');
  assert.deepEqual(response.result.created_event_ids, ['n5', 'n6', 'n7']);
  assert.equal(response.result.diagnostics.created_event_count, 3);
});

test('applyMotif rejects malformed responses as MotifApiError', async (t) => {
  const composition = motifReadyComposition();
  const restore = installAxiosStub(async () => ({
    data: {
      composition,
      result: {
        motif_id: 'motif-a',
        source_occurrence_id: 'occ-orig',
      },
      warnings: [],
    },
    status: 200,
    statusText: 'OK',
    headers: {},
    config: {},
  }));
  t.after(restore);

  await assert.rejects(
    () => applyMotif({
      composition,
      source: { motif_id: 'motif-a', occurrence_id: 'occ-orig' },
      destination: { section_id: 'chorus', track_id: 'melody-1', start_bar: 3 },
      operation: 'repeat',
      parameters: {},
    }),
    (error) => {
      assert.ok(error instanceof MotifApiError);
      assert.equal(error.code, 'motif_invalid_response');
      assert.match(error.message, /Motif apply result missing/);
      return true;
    },
  );
});

test('applyMotif preserves structured backend motif errors', async (t) => {
  const composition = motifReadyComposition();
  const restore = installAxiosStub(async () => {
    const error = new Error('Request failed');
    error.response = {
      status: 422,
      data: {
        detail: {
          code: 'motif_destination_out_of_bounds',
          message: 'Destination placement exceeds composition bounds',
          details: { start_bar: 99 },
        },
      },
    };
    throw error;
  });
  t.after(restore);

  await assert.rejects(
    () => applyMotif({
      composition,
      source: { motif_id: 'motif-a', occurrence_id: 'occ-orig' },
      destination: { section_id: 'chorus', track_id: 'melody-1', start_bar: 99 },
      operation: 'repeat',
      parameters: {},
    }),
    (error) => {
      assert.ok(error instanceof MotifApiError);
      assert.equal(error.status, 422);
      assert.equal(error.code, 'motif_destination_out_of_bounds');
      assert.match(error.message, /bounds/);
      assert.equal(error.details?.start_bar, 99);
      return true;
    },
  );
});

test('analyzeComposition preserves structured backend analysis errors', async (t) => {
  const composition = canonicalV2Composition();
  const restore = installAxiosStub(async () => {
    const error = new Error('Request failed');
    error.isAxiosError = true;
    error.response = {
      status: 422,
      data: {
        detail: {
          code: 'analysis_invalid_composition',
          message: 'Composition failed structural validation',
          details: { reason: 'broken_meter_map' },
        },
      },
    };
    throw error;
  });
  t.after(restore);

  await assert.rejects(
    () => analyzeComposition(composition, { kind: 'composition' }),
    (error) => {
      assert.ok(error instanceof AnalysisApiError);
      assert.equal(error.status, 422);
      assert.equal(error.code, 'analysis_invalid_composition');
      assert.match(error.message, /structural validation/);
      assert.equal(error.details?.reason, 'broken_meter_map');
      return true;
    },
  );
});

function sampleReharmonizePreviewResponse(composition, overrides = {}) {
  return {
    base_fingerprint: 'b'.repeat(64),
    proposal_fingerprint: 'c'.repeat(64),
    composition,
    harmony_changes: [{ kind: 'replaced', start_tick: 15360, duration_ticks: 7680, chord: 'E7(b9)' }],
    track_changes: [{ track_id: 'bass-1', events_changed: 4, events_added: 0, events_removed: 0 }],
    preservation: [{ assertion: 'melody_events_exact', status: 'ok' }],
    compatibility: { status: 'compatible_with_warnings', findings: [{ code: 'melody_nct', severity: 'info' }] },
    provider: 'deterministic',
    model: null,
    warnings: [],
    start_tick: 15360,
    end_tick: 23040,
    active_key: 'C major',
    recommended_target_track_ids: ['bass-1', 'harmony-1'],
    ...overrides,
  };
}

test('previewReharmonization posts bars 9-12 payload and validates response', async (t) => {
  const composition = canonicalV2Composition();
  const immutable = structuredClone(composition);
  let posted = null;
  const restore = installAxiosStub(async (config) => {
    assert.equal(String(config.method || 'get').toLowerCase(), 'post');
    assert.equal(config.url, '/harmony/reharmonize/preview');
    posted = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
    const candidate = structuredClone(composition);
    candidate.harmony = [
      { start_tick: 0, duration_ticks: 1920, chord: 'E7(b9)' },
    ];
    return {
      data: sampleReharmonizePreviewResponse(candidate, {
        start_tick: 0,
        end_tick: 3840,
        harmony_changes: [{ kind: 'replaced', start_tick: 0, duration_ticks: 1920, chord: 'E7(b9)' }],
      }),
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    };
  });
  t.after(restore);

  const response = await previewReharmonization({
    composition,
    selection: { start_bar: 9, end_bar: 12 },
    operation: 'increase_tension',
    content_policy: 'preserve_melody_adapt_harmony',
    target_track_ids: ['bass-1', 'harmony-1'],
    engine: 'deterministic',
    instruction: 'make the harmony more tense while keeping the melody',
    tonal_context: { allow_modulation: false, target_key: null, target_chord: null },
    selection_options: { provider: null, model: null },
  });

  assert.deepEqual(composition, immutable);
  assert.equal(posted.selection.start_bar, 9);
  assert.equal(posted.selection.end_bar, 12);
  assert.equal(posted.operation, 'increase_tension');
  assert.equal(posted.content_policy, 'preserve_melody_adapt_harmony');
  assert.deepEqual(posted.target_track_ids, ['bass-1', 'harmony-1']);
  assert.equal(posted.engine, 'deterministic');
  assert.equal(response.provider, 'deterministic');
  assert.equal(response.compatibility.status, 'compatible_with_warnings');
  assert.equal(response.start_tick, 0);
  assert.equal(response.end_tick, 3840);
  assert.equal(response.composition.schema_version, 'composition.v2');
});

test('previewReharmonization rejects invalid enums and malformed responses', async (t) => {
  const composition = canonicalV2Composition();
  await assert.rejects(
    () => previewReharmonization({
      composition,
      selection: { start_bar: 9, end_bar: 12 },
      operation: 'not_a_real_op',
      content_policy: 'preserve_melody_adapt_harmony',
      target_track_ids: ['bass-1'],
      engine: 'deterministic',
    }),
    (error) => error instanceof ReharmonizeApiError && error.code === 'reharmonize_invalid_request',
  );

  await assert.rejects(
    () => previewReharmonization({
      composition,
      selection: { start_bar: 9, end_bar: 12 },
      operation: 'increase_tension',
      content_policy: 'preserve_melody_adapt_harmony',
      target_track_ids: [],
      engine: 'deterministic',
    }),
    (error) => error instanceof ReharmonizeApiError && error.code === 'reharmonize_invalid_targets',
  );

  const restore = installAxiosStub(async () => ({
    data: sampleReharmonizePreviewResponse(composition, { base_fingerprint: 'short' }),
    status: 200,
    statusText: 'OK',
    headers: {},
    config: {},
  }));
  t.after(restore);

  await assert.rejects(
    () => previewReharmonization({
      composition,
      selection: { start_bar: 9, end_bar: 12 },
      operation: 'increase_tension',
      content_policy: 'preserve_melody_adapt_harmony',
      target_track_ids: ['bass-1'],
      engine: 'deterministic',
    }),
    (error) => error instanceof ReharmonizeApiError && error.code === 'reharmonize_invalid_response',
  );
});

test('previewReharmonization preserves structured backend errors', async (t) => {
  const composition = canonicalV2Composition();
  const restore = installAxiosStub(async () => {
    const error = new Error('Request failed');
    error.isAxiosError = true;
    error.response = {
      status: 422,
      data: {
        detail: {
          code: 'reharmonize_no_realizable_targets',
          message: 'No authorized harmonic-support tracks can realize the proposal',
          details: { target_count: 0 },
        },
      },
    };
    throw error;
  });
  t.after(restore);

  await assert.rejects(
    () => previewReharmonization({
      composition,
      selection: { start_bar: 9, end_bar: 12 },
      operation: 'increase_tension',
      content_policy: 'preserve_melody_adapt_harmony',
      target_track_ids: ['bass-1'],
      engine: 'deterministic',
    }),
    (error) => {
      assert.ok(error instanceof ReharmonizeApiError);
      assert.equal(error.status, 422);
      assert.equal(error.code, 'reharmonize_no_realizable_targets');
      return true;
    },
  );
});

function sampleDevelopmentPreviewResponse(composition, overrides = {}) {
  const candidateComposition = structuredClone(composition);
  const baseBars = composition.bar_count || 2;
  const barTicks = Math.floor((composition.duration_ticks || 3840) / baseBars);
  candidateComposition.bar_count = baseBars + 8;
  candidateComposition.duration_ticks = candidateComposition.bar_count * barTicks;
  const lastSection = (candidateComposition.sections || [])[0] || {
    type: 'intro',
    start_bar: 1,
    bar_count: baseBars,
    start_tick: 0,
    duration_ticks: baseBars * barTicks,
  };
  candidateComposition.sections = [
    { ...lastSection },
    {
      id: 'dev-cont',
      type: 'verse',
      start_bar: baseBars + 1,
      bar_count: 8,
      start_tick: baseBars * barTicks,
      duration_ticks: 8 * barTicks,
    },
  ];
  for (const track of candidateComposition.tracks || []) {
    for (let bar = baseBars; bar < baseBars + 8; bar += 1) {
      track.events = [...(track.events || []), {
        id: `${track.id}-dev-${bar}`,
        pitch: 'G4',
        start_tick: bar * barTicks,
        duration_ticks: Math.min(480, barTicks),
        velocity: 70,
      }];
    }
  }
  return {
    edit_source_fingerprint: 'a'.repeat(64),
    algorithm_version: 'composition.development.v1',
    operation: 'continue',
    development_intent: 'continue',
    variation_strength: 'balanced',
    requested_candidate_count: 2,
    candidates: [
      {
        candidate_id: 'dev-cand-1',
        candidate_fingerprint: 'b'.repeat(64),
        edit_source_fingerprint: 'a'.repeat(64),
        algorithm_version: 'composition.development.v1',
        operation: 'continue',
        development_intent: 'continue',
        variation_strength: 'balanced',
        composition: candidateComposition,
        source_range: { start_bar: 1, end_bar: baseBars, start_tick: 0, end_tick: baseBars * barTicks },
        output_range: {
          start_bar: baseBars + 1,
          end_bar: baseBars + 8,
          start_tick: baseBars * barTicks,
          end_tick: (baseBars + 8) * barTicks,
        },
        section_changes: [],
        harmony_changes: [],
        motif_changes: [],
        track_changes: [],
        preservation: [{ code: 'immutable_prefix', required: true, passed: true }],
        identity_diagnostics: [],
        provider: 'fake',
        model: 'fake-deterministic',
        warning_codes: [],
      },
      {
        candidate_id: 'dev-cand-2',
        candidate_fingerprint: 'c'.repeat(64),
        edit_source_fingerprint: 'a'.repeat(64),
        algorithm_version: 'composition.development.v1',
        operation: 'continue',
        development_intent: 'continue',
        variation_strength: 'balanced',
        composition: structuredClone(candidateComposition),
        source_range: { start_bar: 1, end_bar: baseBars, start_tick: 0, end_tick: baseBars * barTicks },
        output_range: {
          start_bar: baseBars + 1,
          end_bar: baseBars + 8,
          start_tick: baseBars * barTicks,
          end_tick: (baseBars + 8) * barTicks,
        },
        section_changes: [],
        harmony_changes: [],
        motif_changes: [],
        track_changes: [],
        preservation: [{ code: 'immutable_prefix', required: true, passed: true }],
        identity_diagnostics: [],
        provider: 'fake',
        model: 'fake-deterministic',
        warning_codes: [],
      },
    ],
    warning_codes: [],
    provider: 'fake',
    model: 'fake-deterministic',
    ...overrides,
  };
}

test('previewCompositionDevelopment posts continue payload and validates candidates', async (t) => {
  const composition = canonicalV2Composition();
  const immutable = structuredClone(composition);
  let posted = null;
  const restore = installAxiosStub(async (config) => {
    assert.equal(String(config.method || 'get').toLowerCase(), 'post');
    assert.equal(config.url, '/composition/development/preview');
    posted = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
    return {
      data: sampleDevelopmentPreviewResponse(composition),
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    };
  });
  t.after(restore);

  const response = await previewCompositionDevelopment({
    composition,
    operation: 'continue',
    output_bars: 8,
    variation_strength: 'balanced',
    development_intent: 'continue',
    candidate_count: 2,
    instruction: 'extend the A section',
    selection: { provider: 'fake', model: 'fake-deterministic' },
  });

  assert.deepEqual(composition, immutable);
  assert.equal(posted.operation, 'continue');
  assert.equal(posted.output_bars, 8);
  assert.equal(posted.candidate_count, 2);
  assert.equal(posted.composition.schema_version, 'composition.v2');
  assert.equal(response.candidates.length, 2);
  assert.equal(response.candidates[0].candidate_id, 'dev-cand-1');
  assert.equal(response.candidates[1].candidate_id, 'dev-cand-2');
});

test('previewCompositionDevelopment rejects vary without source locally', async () => {
  await assert.rejects(
    () => previewCompositionDevelopment({
      composition: canonicalV2Composition(),
      operation: 'vary_section',
      variation_strength: 'balanced',
    }),
    (error) => {
      assert.ok(error instanceof DevelopmentApiError);
      assert.equal(error.code, 'development_source_required');
      return true;
    },
  );
});

test('previewCompositionDevelopment preserves structured backend errors', async (t) => {
  const restore = installAxiosStub(async () => {
    const error = new Error('Request failed');
    error.isAxiosError = true;
    error.response = {
      status: 502,
      data: {
        detail: {
          code: 'development_candidate_exhausted',
          message: 'No valid development candidate survived generation and repair.',
          details: { returned_candidate_count: 0 },
        },
      },
    };
    throw error;
  });
  t.after(restore);

  await assert.rejects(
    () => previewCompositionDevelopment({
      composition: canonicalV2Composition(),
      operation: 'continue',
      output_bars: 8,
      variation_strength: 'balanced',
      candidate_count: 1,
    }),
    (error) => {
      assert.ok(error instanceof DevelopmentApiError);
      assert.equal(error.status, 502);
      assert.equal(error.code, 'development_candidate_exhausted');
      return true;
    },
  );
});

function sampleArrangementCatalog() {
  return {
    catalog_version: ARRANGEMENT_CATALOG_VERSION,
    range_policy_version: ARRANGEMENT_RANGE_POLICY_VERSION,
    fingerprint: 'd'.repeat(64),
    source_path_category: 'packaged',
    instruments: [
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
        suggested_roles: ['melody', 'bass'],
        fingerprint: 'c'.repeat(64),
      },
    ],
    track_roles: [...SUPPORTED_TRACK_ROLES],
  };
}

async function sampleArrangementPreviewResponse(composition, overrides = {}) {
  const candidateComposition = structuredClone(composition);
  const melody = candidateComposition.tracks.find((track) => track.id === 'melody-1')
    || candidateComposition.tracks[0];
  melody.instrument = 'cello';
  melody.midi_program = 42;
  const sourceFp = await compositionEditFingerprint(composition);
  const candidateFp = await compositionEditFingerprint(candidateComposition);
  return {
    edit_source_fingerprint: sourceFp,
    algorithm_version: ARRANGEMENT_ALGORITHM_VERSION,
    catalog_version: ARRANGEMENT_CATALOG_VERSION,
    range_policy_version: ARRANGEMENT_RANGE_POLICY_VERSION,
    catalog_fingerprint: 'd'.repeat(64),
    operation: 'change_instrumentation',
    requested_candidate_count: 1,
    candidates: [
      {
        candidate_id: 'arr-cand-abcdefgh',
        candidate_fingerprint: candidateFp,
        edit_source_fingerprint: sourceFp,
        algorithm_version: ARRANGEMENT_ALGORITHM_VERSION,
        catalog_version: ARRANGEMENT_CATALOG_VERSION,
        range_policy_version: ARRANGEMENT_RANGE_POLICY_VERSION,
        catalog_fingerprint: 'd'.repeat(64),
        target_profile_fingerprints: [
          { instrument_id: 'cello', profile_fingerprint: 'c'.repeat(64) },
        ],
        operation: 'change_instrumentation',
        composition: candidateComposition,
        provider: 'fake',
        model: 'fake-deterministic',
        before_inventory: composition.tracks.map((track) => ({
          track_id: track.id,
          instrument: track.instrument,
          role: track.role,
          midi_program: track.midi_program,
          event_count: (track.events || []).length,
          part_id: null,
        })),
        after_inventory: candidateComposition.tracks.map((track) => ({
          track_id: track.id,
          instrument: track.instrument,
          role: track.role,
          midi_program: track.midi_program,
          event_count: (track.events || []).length,
          part_id: null,
        })),
        manifest: {
          retained_track_ids: candidateComposition.tracks
            .map((track) => track.id)
            .filter((id) => id !== melody.id),
          removed_track_ids: [],
          added_track_ids: [],
          reordered_track_ids: [],
          reinstrumented_track_ids: [melody.id],
          split_track_ids: [],
          merged_track_ids: [],
          source_to_target: [{
            source_track_id: melody.id,
            target_track_id: melody.id,
            relationship: 'reinstrumented',
          }],
        },
        event_counts: {
          copied: 1,
          moved: 0,
          generated: 0,
          removed: 0,
          octave_adjusted: 0,
          unchanged: Math.max(0, candidateComposition.tracks.length - 1),
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
      },
    ],
    rejected_attempts: [],
    warning_codes: [],
    provider: 'fake',
    model: 'fake-deterministic',
    ...overrides,
  };
}

test('loadArrangementInstruments caches normalized catalog and serves cache hits', async (t) => {
  resetArrangementInstrumentCache();
  let hits = 0;
  const restore = installAxiosStub(async (config) => {
    hits += 1;
    assert.equal(String(config.method || 'get').toLowerCase(), 'get');
    assert.equal(config.url, '/composition/arrangement/instruments');
    return {
      data: sampleArrangementCatalog(),
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    };
  });
  t.after(() => {
    restore();
    resetArrangementInstrumentCache();
  });

  const catalog = await loadArrangementInstruments();
  assert.equal(catalog.catalog_version, ARRANGEMENT_CATALOG_VERSION);
  assert.equal(catalog.instruments.length, 2);
  assert.equal(getCachedArrangementCatalog().fingerprint, 'd'.repeat(64));
  for (const role of SUPPORTED_TRACK_ROLES) {
    assert.ok(catalog.track_roles.includes(role));
  }

  const again = await loadArrangementInstruments();
  assert.equal(again.fingerprint, catalog.fingerprint);
  assert.equal(hits, 1);

  await loadArrangementInstruments({ forceRefresh: true });
  assert.equal(hits, 2);

  // Alias still works
  const viaAlias = await fetchArrangementInstruments();
  assert.equal(viaAlias.fingerprint, catalog.fingerprint);
  assert.equal(hits, 2);
});

test('previewCompositionArrangement posts normalized payload and validates candidates', async (t) => {
  clearArrangementCatalogCache();
  const composition = canonicalV2Composition();
  const immutable = structuredClone(composition);
  const trackId = composition.tracks[0].id;
  let posted = null;
  const restore = installAxiosStub(async (config) => {
    assert.equal(String(config.method || 'get').toLowerCase(), 'post');
    assert.equal(config.url, '/composition/arrangement/preview');
    posted = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
    return {
      data: await sampleArrangementPreviewResponse(composition),
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    };
  });
  t.after(() => {
    restore();
    clearArrangementCatalogCache();
  });

  const response = await previewCompositionArrangement({
    composition,
    operation: 'change_instrumentation',
    source_track_ids: [trackId],
    protected_track_ids: [],
    instrumentation: {
      before: [{
        part_id: 'b1',
        instrument_id: 'acoustic_grand_piano',
        role: 'melody',
        source_track_ids: [trackId],
        doubling_policy: 'none',
      }],
      after: [{
        part_id: 'a1',
        instrument_id: 'cello',
        role: 'melody',
        source_track_ids: [trackId],
        doubling_policy: 'none',
      }],
    },
    candidate_count: 1,
    instruction: 'keep the melody recognizable',
    selection: { provider: 'fake', model: 'fake-deterministic' },
  });

  assert.deepEqual(composition, immutable);
  assert.equal(posted.operation, 'change_instrumentation');
  assert.equal(posted.candidate_count, 1);
  assert.equal(posted.composition.schema_version, 'composition.v2');
  assert.equal(response.candidates.length, 1);
  assert.equal(response.candidates[0].candidate_id, 'arr-cand-abcdefgh');
  assert.equal(response.catalog_version, ARRANGEMENT_CATALOG_VERSION);
});

test('previewCompositionArrangement rejects source/protected overlap locally', async () => {
  const composition = canonicalV2Composition();
  const trackId = composition.tracks[0].id;
  await assert.rejects(
    () => previewCompositionArrangement({
      composition,
      operation: 'change_instrumentation',
      source_track_ids: [trackId],
      protected_track_ids: [trackId],
      instrumentation: {
        before: [{
          part_id: 'b1',
          instrument_id: 'acoustic_grand_piano',
          role: 'melody',
          source_track_ids: [trackId],
        }],
        after: [{
          part_id: 'a1',
          instrument_id: 'cello',
          role: 'melody',
          source_track_ids: [trackId],
        }],
      },
    }),
    (error) => {
      assert.ok(error instanceof ArrangementApiError);
      assert.equal(error.code, 'arrangement_source_protected_overlap');
      return true;
    },
  );
});

test('previewCompositionArrangement preserves structured backend errors', async (t) => {
  const composition = canonicalV2Composition();
  const trackId = composition.tracks[0].id;
  const restore = installAxiosStub(async () => {
    const error = new Error('Request failed');
    error.isAxiosError = true;
    error.response = {
      status: 502,
      data: {
        detail: {
          code: 'arrangement_candidate_exhausted',
          message: 'No valid arrangement candidate survived generation and repair.',
          details: { returned_candidate_count: 0 },
        },
      },
    };
    throw error;
  });
  t.after(restore);

  await assert.rejects(
    () => previewCompositionArrangement({
      composition,
      operation: 'change_instrumentation',
      source_track_ids: [trackId],
      instrumentation: {
        before: [{
          part_id: 'b1',
          instrument_id: 'acoustic_grand_piano',
          role: 'melody',
          source_track_ids: [trackId],
        }],
        after: [{
          part_id: 'a1',
          instrument_id: 'cello',
          role: 'melody',
          source_track_ids: [trackId],
        }],
      },
      candidate_count: 1,
    }),
    (error) => {
      assert.ok(error instanceof ArrangementApiError);
      assert.equal(error.status, 502);
      assert.equal(error.code, 'arrangement_candidate_exhausted');
      return true;
    },
  );
});

test('loadArrangementInstruments preserves structured catalog errors', async (t) => {
  resetArrangementInstrumentCache();
  const restore = installAxiosStub(async () => {
    const error = new Error('Request failed');
    error.isAxiosError = true;
    error.response = {
      status: 503,
      data: {
        detail: {
          code: 'arrangement_catalog_unavailable',
          message: 'Arrangement instrument catalog is unavailable or invalid.',
          details: { catalog_code: 'invalid_schema', path_category: 'invalid' },
        },
      },
    };
    throw error;
  });
  t.after(() => {
    restore();
    resetArrangementInstrumentCache();
  });

  await assert.rejects(
    () => loadArrangementInstruments(),
    (error) => {
      assert.ok(error instanceof ArrangementApiError);
      assert.equal(error.status, 503);
      assert.equal(error.code, 'arrangement_catalog_unavailable');
      return true;
    },
  );
});
