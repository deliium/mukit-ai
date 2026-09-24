import test from 'node:test';
import assert from 'node:assert/strict';
import {
  MIX_ANALYSIS_HONESTY_COPY,
  MIX_ANALYSIS_SCHEMA_VERSION,
  formatMixAnalysisLocus,
  isMixAnalysisReportStale,
  normalizeMixAnalysisDimensions,
  observationLocusHighlightRanges,
  partitionMixAnalysisLayers,
} from './mixAnalysisUi.js';

test('schema version constant', () => {
  assert.equal(MIX_ANALYSIS_SCHEMA_VERSION, 'mix.analysis.v1');
});

test('partition layers keeps measurements separate from observations and AI', () => {
  const layers = partitionMixAnalysisLayers({
    measurements: [{ code: 'peak_dbfs', value: -1 }],
    observations: [{ code: 'peak_hot', kind: 'observation' }],
    interpretations: [{ code: 'ai_mix_note', kind: 'interpretation' }],
  });
  assert.equal(layers.measurements.length, 1);
  assert.equal(layers.observations.length, 1);
  assert.equal(layers.interpretations.length, 1);
  assert.equal(layers.measurements[0].code, 'peak_dbfs');
  assert.notEqual(layers.observations[0].code, layers.measurements[0].code);
});

test('soft-stale when stem fingerprint diverges', () => {
  assert.equal(
    isMixAnalysisReportStale(
      { source_stem_set_fingerprint: 'aaa', source_composition_fingerprint: 'c1' },
      { stemSetFingerprint: 'bbb', compositionFingerprint: 'c1' },
    ),
    true,
  );
  assert.equal(
    isMixAnalysisReportStale(
      { source_stem_set_fingerprint: 'aaa', source_composition_fingerprint: 'c1' },
      { stemSetFingerprint: 'aaa', compositionFingerprint: 'c1' },
    ),
    false,
  );
});

test('soft-stale when composition fingerprint diverges', () => {
  assert.equal(
    isMixAnalysisReportStale(
      { source_stem_set_fingerprint: 'aaa', source_composition_fingerprint: 'old' },
      { stemSetFingerprint: 'aaa', compositionFingerprint: 'new' },
    ),
    true,
  );
});

test('format locus includes tracks and freq', () => {
  const text = formatMixAnalysisLocus({
    stem_roles: ['bass', 'strings'],
    source_track_ids: ['bass-1', 'cello-1'],
    freq_hz_low: 150,
    freq_hz_high: 400,
    start_bar: 21,
    end_bar: 28,
  });
  assert.match(text, /bass/);
  assert.match(text, /bass-1/);
  assert.match(text, /150–400 Hz/);
  assert.match(text, /bars 21–28/);
});

test('normalize dimensions drops unknown', () => {
  const dims = normalizeMixAnalysisDimensions(['peak', 'nope', 'masking_proxy']);
  assert.deepEqual(dims, ['peak', 'masking_proxy']);
});

test('honesty copy refuses mutation claims', () => {
  assert.match(MIX_ANALYSIS_HONESTY_COPY, /does not modify/i);
  assert.match(MIX_ANALYSIS_HONESTY_COPY, /advisory/i);
});

test('observation locus highlight ranges normalize to 0-1', () => {
  const ranges = observationLocusHighlightRanges(
    [
      { locus: { start_seconds: 1, end_seconds: 3 } },
      { locus: { start_seconds: 0, end_seconds: 10 } },
    ],
    10,
  );
  assert.equal(ranges.length, 2);
  assert.equal(ranges[0].start, 0.1);
  assert.equal(ranges[0].end, 0.3);
  assert.equal(ranges[1].start, 0);
  assert.equal(ranges[1].end, 1);
});
