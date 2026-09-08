import assert from 'node:assert/strict';
import test from 'node:test';

import {
  ANALYSIS_SCHEMA_VERSION,
  buildAnalysisRequestKey,
  buildAnalysisRequestScope,
  findSectionByAnalysisKey,
  makeAnalysisSectionKey,
  normalizeAnalysisWarnings,
  recoverAnalysisSectionKey,
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
  ],
  tracks: [{ id: 'melody-1', events: [] }],
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
});

test('normalizeAnalysisWarnings drops malformed entries', () => {
  const warnings = normalizeAnalysisWarnings([
    { code: 'empty_analysis_scope', severity: 'warning', message: 'empty' },
    null,
    { severity: 'info' },
  ]);
  assert.equal(warnings.length, 1);
  assert.equal(warnings[0].code, 'empty_analysis_scope');
});
