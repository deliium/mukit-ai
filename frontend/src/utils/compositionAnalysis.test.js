import assert from 'node:assert/strict';
import test from 'node:test';

import {
  ANALYSIS_SCHEMA_VERSION,
  AnalysisScopeError,
  analysisRequestKeysEqual,
  buildAnalysisRequestKey,
  buildAnalysisRequestScope,
  deriveAnalysisFreshness,
  findSectionByAnalysisKey,
  listAnalysisSectionOptions,
  makeAnalysisSectionKey,
  normalizeAnalysisReport,
  normalizeAnalysisScopeForKey,
  normalizeAnalysisWarnings,
  recoverAnalysisSectionKey,
  sanitizeScopeForLog,
} from './compositionAnalysis.js';

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
