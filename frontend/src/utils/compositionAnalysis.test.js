import assert from 'node:assert/strict';
import test from 'node:test';

import {
  ANALYSIS_FINGERPRINT_PROFILE,
  ANALYSIS_MOTIF_STALE_FINGERPRINT,
  ANALYSIS_SCHEMA_VERSION,
  AnalysisScopeError,
  analysisFingerprintMatches,
  analysisRequestKeysEqual,
  buildAnalysisRequestKey,
  buildAnalysisRequestScope,
  compositionSourceFingerprint,
  deriveAnalysisFreshness,
  findSectionByAnalysisKey,
  listAnalysisSectionOptions,
  makeAnalysisSectionKey,
  normalizeAnalysisReport,
  normalizeAnalysisScopeForKey,
  normalizeAnalysisWarnings,
  normalizeMotifFamilies,
  projectDetectedMotifUsages,
  recoverAnalysisSectionKey,
  resolveDetectedNoteReferences,
  sanitizeScopeForLog,
} from './compositionAnalysis.js';
import { projectMotifUsagesForDisplay } from './compositionMotifs.js';

const composition = {
  schema_version: 'composition.v2',
  sections: [
    {
      type: 'intro',
      start_bar: 1,
      bar_count: 4,
      start_tick: 0,
      duration_ticks: 7680,
    },
    {
      id: 'chorus-a',
      type: 'chorus',
      start_bar: 5,
      bar_count: 8,
      start_tick: 7680,
      duration_ticks: 15360,
    },
    {
      type: 'intro',
      start_bar: 13,
      bar_count: 4,
      start_tick: 23040,
      duration_ticks: 7680,
    },
  ],
  tracks: [{ id: 'melody-1', events: [] }, { id: 'bass-1', events: [] }],
};

test('ID-less and id section keys recover after selection', () => {
  const idLessKey = makeAnalysisSectionKey(composition.sections[0], 0);
  const idKey = makeAnalysisSectionKey(composition.sections[1], 1);
  assert.match(idLessKey, /^idx:0:/);
  assert.equal(idKey, 'id:chorus-a');

  const recoveredIdLess = findSectionByAnalysisKey(composition, idLessKey);
  assert.equal(recoveredIdLess.index, 0);
  assert.equal(recoverAnalysisSectionKey(composition, idLessKey), idLessKey);
  assert.equal(recoverAnalysisSectionKey(composition, 'id:missing'), idLessKey);
});

test('repeated ID-less sections stay distinguishable by index identity', () => {
  const first = makeAnalysisSectionKey(composition.sections[0], 0);
  const third = makeAnalysisSectionKey(composition.sections[2], 2);
  assert.notEqual(first, third);
  assert.equal(findSectionByAnalysisKey(composition, first).index, 0);
  assert.equal(findSectionByAnalysisKey(composition, third).index, 2);

  const options = listAnalysisSectionOptions(composition);
  assert.equal(options.length, 3);
  assert.equal(options[0].key, first);
  assert.equal(options[2].key, third);
});

test('selection recovery falls back when section bounds drift', () => {
  const key = makeAnalysisSectionKey(composition.sections[0], 0);
  const drifted = {
    ...composition,
    sections: [
      {
        type: 'bridge',
        start_bar: 99,
        bar_count: 1,
        start_tick: 0,
        duration_ticks: 100,
      },
      composition.sections[1],
    ],
  };
  assert.equal(findSectionByAnalysisKey(drifted, key), null);
  assert.equal(
    recoverAnalysisSectionKey(drifted, key),
    makeAnalysisSectionKey(drifted.sections[0], 0),
  );
  assert.equal(
    recoverAnalysisSectionKey(drifted, 'id:chorus-a'),
    'id:chorus-a',
  );
});

test('request key uses analysis schema + full revision + normalized scope only', () => {
  const scope = buildAnalysisRequestScope({
    analysisScope: 'section',
    composition,
    sectionKey: makeAnalysisSectionKey(composition.sections[0], 0),
  });
  const key = buildAnalysisRequestKey({
    compositionRevision: 'full-revision-token',
    scope,
  });
  const parsed = JSON.parse(key);
  assert.equal(parsed.analysis_schema_version, ANALYSIS_SCHEMA_VERSION);
  assert.equal(parsed.composition_revision, 'full-revision-token');
  assert.equal(parsed.scope.kind, 'section');
  assert.equal(parsed.scope.section_index, 0);
  assert.equal(Object.prototype.hasOwnProperty.call(parsed.scope, 'section_id'), false);

  const trackScope = buildAnalysisRequestScope({
    analysisScope: 'track',
    composition,
    trackId: 'melody-1',
  });
  const trackKey = buildAnalysisRequestKey({
    compositionRevision: 'rev-a',
    scope: trackScope,
  });
  const trackParsed = JSON.parse(trackKey);
  assert.deepEqual(trackParsed.scope, { kind: 'track', track_id: 'melody-1' });
  assert.equal(
    analysisRequestKeysEqual(trackKey, buildAnalysisRequestKey({
      compositionRevision: 'rev-a',
      scope: normalizeAnalysisScopeForKey(trackScope),
    })),
    true,
  );
  assert.equal(
    analysisRequestKeysEqual(trackKey, buildAnalysisRequestKey({
      compositionRevision: 'rev-b',
      scope: trackScope,
    })),
    false,
  );
});

test('buildAnalysisRequestScope rejects missing section and track targets', () => {
  assert.throws(
    () => buildAnalysisRequestScope({
      analysisScope: 'section',
      composition,
      sectionKey: 'id:missing',
    }),
    (error) => error instanceof AnalysisScopeError && error.code === 'analysis_invalid_scope',
  );
  assert.throws(
    () => buildAnalysisRequestScope({
      analysisScope: 'track',
      composition,
      trackId: '',
    }),
    (error) => error instanceof AnalysisScopeError,
  );
});

test('normalizeAnalysisWarnings drops malformed entries and fills defaults', () => {
  const warnings = normalizeAnalysisWarnings([
    { code: 'empty_analysis_scope', severity: 'warning', message: 'empty' },
    null,
    { severity: 'info' },
    { code: 'timing_grid_anomaly' },
  ]);
  assert.equal(warnings.length, 2);
  assert.equal(warnings[0].code, 'empty_analysis_scope');
  assert.equal(warnings[1].code, 'timing_grid_anomaly');
  assert.equal(warnings[1].severity, 'warning');
  assert.equal(warnings[1].category, 'data_quality');
  assert.equal(warnings[1].message, 'timing_grid_anomaly');
});

test('normalizeAnalysisReport enforces contract and optional arrays', () => {
  const report = normalizeAnalysisReport({
    schema_version: ANALYSIS_SCHEMA_VERSION,
    algorithm_version: 'native-v1',
    source_schema_version: 'composition.v2',
    source_fingerprint: 'b'.repeat(32),
    status: 'ok',
    resolved_scope: { kind: 'composition' },
    warnings: [{ code: 'empty_analysis_scope' }, null],
  });
  assert.equal(report.warnings.length, 1);
  assert.deepEqual(report.section_summaries, []);
  assert.throws(
    () => normalizeAnalysisReport({ schema_version: 'nope' }),
    /schema_version/,
  );
});

test('compositionSourceFingerprint matches backend profile and is stable', async () => {
  const composition = {
    schema_version: 'composition.v2',
    tempo: 100,
    key: 'C major',
    time_signature: '4/4',
    ticks_per_quarter: 480,
    bar_count: 2,
    duration_ticks: 3840,
    sections: [{
      type: 'intro',
      start_bar: 1,
      bar_count: 2,
      start_tick: 0,
      duration_ticks: 3840,
    }],
    tracks: [{
      id: 'piano-1',
      name: 'Piano',
      instrument: 'piano',
      role: 'harmony',
      midi_program: 0,
      channel: 1,
      events: [{
        pitch: 'C4',
        start_tick: 0,
        duration_ticks: 480,
        velocity: 80,
        articulations: ['accent'],
      }],
      volume: 100,
    }],
    harmony: [{ bar: 1, chord: 'C' }],
    tempo_changes: [],
    time_signature_changes: [],
    key_changes: [],
    markers: [{ tick: 0, kind: 'rehearsal', label: 'A' }],
  };
  const first = await compositionSourceFingerprint(composition);
  const second = await compositionSourceFingerprint(composition);
  assert.equal(first, second);
  assert.equal(first.length, 64);
  assert.match(first, /^[0-9a-f]{64}$/);
  assert.equal(ANALYSIS_FINGERPRINT_PROFILE, 'analysis.source.v1');
});

test('normalizeAnalysisReport preserves motif families', () => {
  const report = normalizeAnalysisReport({
    schema_version: ANALYSIS_SCHEMA_VERSION,
    algorithm_version: 'native-v1',
    source_schema_version: 'composition.v2',
    source_fingerprint: 'b'.repeat(64),
    status: 'ok',
    resolved_scope: { kind: 'composition' },
    repetition: {
      motifs: [],
      motif_families: [{
        id: 'motif_family:abc',
        note_count: 4,
        relationship_kinds: ['exact'],
        reference: {
          id: 'motif_occ:ref',
          kind: 'exact',
          track_id: 'melody-1',
          start_tick: 0,
          end_tick: 1920,
          note_count: 4,
          identity_score: 1,
          notes: [{ event_ids: ['n1', 'n2', 'n3', 'n4'] }],
        },
        matched_occurrences: [],
      }],
    },
  });
  assert.equal(report.repetition.motif_families.length, 1);
  assert.equal(normalizeMotifFamilies(report.repetition)[0].id, 'motif_family:abc');
});

test('resolveDetectedNoteReferences accepts ids and gated index fallback', () => {
  const composition = {
    schema_version: 'composition.v2',
    tracks: [{
      id: 'melody-1',
      events: [
        { id: 'n1', start_tick: 0, duration_ticks: 480, pitch: 'C4' },
        { id: 'n2', start_tick: 480, duration_ticks: 480, pitch: 'D4' },
      ],
    }],
  };
  const byId = resolveDetectedNoteReferences(composition, [
    { event_ids: ['n1'] },
    { event_ids: ['n2'] },
  ], { fingerprintMatches: false, trackId: 'melody-1' });
  assert.deepEqual(byId.eventIds, ['n1', 'n2']);
  assert.equal(byId.valid, true);

  const staleIndex = resolveDetectedNoteReferences(composition, [
    { event_indexes: [0, 1] },
  ], { fingerprintMatches: false, trackId: 'melody-1' });
  assert.equal(staleIndex.valid, false);
  assert.equal(staleIndex.staleReason, ANALYSIS_MOTIF_STALE_FINGERPRINT);

  const freshIndex = resolveDetectedNoteReferences(composition, [
    { event_indexes: [0, 1] },
  ], { fingerprintMatches: true, trackId: 'melody-1' });
  assert.deepEqual(freshIndex.eventIds, ['n1', 'n2']);
  assert.equal(freshIndex.valid, true);
});

test('projectDetectedMotifUsages and display merge honor fingerprint staleness', async () => {
  const composition = {
    schema_version: 'composition.v2',
    tempo: 100,
    key: 'C major',
    time_signature: '4/4',
    ticks_per_quarter: 480,
    bar_count: 4,
    duration_ticks: 7680,
    sections: [{
      id: 'verse',
      type: 'verse',
      start_bar: 1,
      bar_count: 4,
      start_tick: 0,
      duration_ticks: 7680,
    }],
    tracks: [{
      id: 'melody-1',
      role: 'melody',
      events: [
        { id: 'n1', start_tick: 0, duration_ticks: 480, pitch: 'C4' },
        { id: 'n2', start_tick: 480, duration_ticks: 480, pitch: 'D4' },
        { id: 'n3', start_tick: 960, duration_ticks: 480, pitch: 'E4' },
        { id: 'n4', start_tick: 1920, duration_ticks: 480, pitch: 'G4' },
      ],
    }],
    harmony: [],
    tempo_changes: [],
    time_signature_changes: [],
    key_changes: [],
  };
  const fingerprint = await compositionSourceFingerprint(composition);
  const report = {
    source_fingerprint: fingerprint,
    repetition: {
      motif_families: [{
        id: 'motif_family:test',
        note_count: 3,
        relationship_kinds: ['exact'],
        reference: {
          id: 'motif_occ:ref',
          kind: 'exact',
          track_id: 'melody-1',
          start_tick: 0,
          end_tick: 1440,
          note_count: 3,
          identity_score: 1,
          notes: [{ event_indexes: [0] }, { event_indexes: [1] }, { event_indexes: [2] }],
        },
        matched_occurrences: [{
          id: 'motif_occ:match',
          kind: 'exact',
          track_id: 'melody-1',
          start_tick: 1920,
          end_tick: 2400,
          note_count: 1,
          identity_score: 1,
          notes: [{ event_ids: ['n4'] }],
        }],
      }],
    },
  };
  assert.equal(analysisFingerprintMatches(report, fingerprint), true);
  const detected = projectDetectedMotifUsages(composition, report, { currentFingerprint: fingerprint });
  assert.equal(detected.length, 2);
  assert.equal(detected[0].stale, false);
  assert.deepEqual(detected[0].eventIds, ['n1', 'n2', 'n3']);

  const staleDetected = projectDetectedMotifUsages(composition, report, {
    currentFingerprint: 'stale'.padEnd(64, '0'),
  });
  assert.equal(staleDetected[0].stale, true);

  const merged = projectMotifUsagesForDisplay(composition, { detectedUsages: detected });
  assert.equal(merged.length, 2);
  assert.ok(merged.every((item) => item.source === 'detected'));
});

test('deriveAnalysisFreshness and sanitizeScopeForLog stay safe', () => {
  const desired = buildAnalysisRequestKey({
    compositionRevision: 'rev-1',
    scope: { kind: 'composition' },
  });
  const fresh = deriveAnalysisFreshness({
    analysisResult: { status: 'ok' },
    analysisResultKey: desired,
    desiredRequestKey: desired,
    analysisStatus: 'success',
  });
  assert.equal(fresh.isCurrent, true);
  assert.equal(fresh.isStale, false);

  const stale = deriveAnalysisFreshness({
    analysisResult: { status: 'ok' },
    analysisResultKey: desired,
    desiredRequestKey: buildAnalysisRequestKey({
      compositionRevision: 'rev-2',
      scope: { kind: 'composition' },
    }),
    analysisStatus: 'loading',
  });
  assert.equal(stale.isStale, true);
  assert.equal(stale.isLoading, true);

  assert.deepEqual(sanitizeScopeForLog({ kind: 'composition' }), { kind: 'composition' });
  assert.deepEqual(sanitizeScopeForLog({ kind: 'track', track_id: 'melody-1' }), {
    kind: 'track',
    hasTrackId: true,
  });
  assert.deepEqual(sanitizeScopeForLog({
    kind: 'section',
    section_index: 1,
    section_id: 'chorus-a',
  }), {
    kind: 'section',
    section_index: 1,
    hasSectionId: true,
  });
});
