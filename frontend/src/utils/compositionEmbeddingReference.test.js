import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildCurrentEmbedScope,
  buildSectionEmbedScope,
  buildSimilarityQueryPayload,
  buildStyleReferenceFromMusicalReference,
  cosineSimilarity,
  fingerprintPrefix,
  formatSimilarityHitLabel,
  formatSimilarityScore,
  normalizeEmbedScope,
  normalizeMusicalReferenceSession,
  normalizeSimilarityHit,
  normalizeSimilarityHits,
  normalizeStyleReference,
} from './compositionEmbeddingReference.js';

function minimalComposition() {
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
        id: 'intro',
        type: 'intro',
        label: null,
        start_bar: 1,
        bar_count: 2,
        start_tick: 0,
        duration_ticks: 3840,
      },
    ],
    tracks: [],
    harmony: [],
    motifs: [],
  };
}

test('normalizeEmbedScope accepts section and bar_range', () => {
  const section = normalizeEmbedScope({
    kind: 'section',
    section_index: 0,
    section_id: 'intro',
    expected_start_bar: 1,
    expected_bar_count: 2,
  });
  assert.equal(section.ok, true);
  assert.deepEqual(section.scope, {
    kind: 'section',
    section_index: 0,
    section_id: 'intro',
    expected_start_bar: 1,
    expected_bar_count: 2,
  });

  const bars = normalizeEmbedScope({ kind: 'bar_range', start_bar: 3, end_bar: 8 });
  assert.equal(bars.ok, true);
  assert.deepEqual(bars.scope, { kind: 'bar_range', start_bar: 3, end_bar: 8 });

  const bad = normalizeEmbedScope({ kind: 'bar_range', start_bar: 5, end_bar: 2 });
  assert.equal(bad.ok, false);
  assert.equal(bad.code, 'embed_scope_invalid');
});

test('buildSectionEmbedScope and buildCurrentEmbedScope prefer bar range', () => {
  const option = buildSectionEmbedScope({
    index: 0,
    id: 'intro',
    start_bar: 1,
    bar_count: 2,
  });
  assert.equal(option.ok, true);
  assert.equal(option.scope.kind, 'section');

  const current = buildCurrentEmbedScope({
    composition: minimalComposition(),
    sourceStartBar: 1,
    sourceEndBar: 4,
  });
  assert.equal(current.ok, true);
  assert.deepEqual(current.scope, { kind: 'bar_range', start_bar: 1, end_bar: 4 });

  const fromSection = buildCurrentEmbedScope({
    composition: minimalComposition(),
  });
  assert.equal(fromSection.ok, true);
  assert.equal(fromSection.scope.kind, 'section');
});

test('normalizeStyleReference requires project or composition', () => {
  const missing = normalizeStyleReference({ scope: { kind: 'composition' } });
  assert.equal(missing.ok, false);

  const ok = normalizeStyleReference({
    project_id: 'proj-1',
    scope: { kind: 'section', section_index: 1 },
    mode: 'prompt_features',
    expected_fingerprint: 'a'.repeat(32),
  });
  assert.equal(ok.ok, true);
  assert.equal(ok.styleReference.project_id, 'proj-1');
  assert.equal(ok.styleReference.expected_fingerprint.length, 32);
  assert.equal(ok.styleReference.mode, 'prompt_features');
});

test('buildStyleReferenceFromMusicalReference maps session fields', () => {
  const empty = buildStyleReferenceFromMusicalReference(null);
  assert.equal(empty.ok, true);
  assert.equal(empty.styleReference, null);

  const built = buildStyleReferenceFromMusicalReference({
    projectId: 'p1',
    scope: { kind: 'composition' },
    sourceFingerprint: 'b'.repeat(40),
  });
  assert.equal(built.ok, true);
  assert.equal(built.styleReference.project_id, 'p1');
  assert.equal(built.styleReference.expected_fingerprint, 'b'.repeat(40));
});

test('normalizeSimilarityHits strips invalid entries and caps top-k', () => {
  const hits = normalizeSimilarityHits([
    {
      rank: 1,
      score: 0.91,
      distance: 0.09,
      model_id: 'symbolic.features.v1',
      target: {
        project_id: 'other',
        scope: { kind: 'section', section_index: 0 },
        source_fingerprint: 'c'.repeat(32),
        corpus: 'projects',
      },
    },
    { rank: 2, score: 0.5 },
    null,
  ], { topK: 1 });
  assert.equal(hits.length, 1);
  assert.equal(hits[0].projectId, 'other');
  assert.equal(hits[0].musicalQualityClaim, false);
  assert.equal(hits[0].fingerprintPrefix, 'c'.repeat(12));
  assert.equal(normalizeSimilarityHit(null), null);
});

test('normalizeMusicalReferenceSession and score formatting', () => {
  const session = normalizeMusicalReferenceSession({
    projectId: 'proj',
    projectName: 'Demo',
    sectionIndex: 0,
    sectionLabel: 'intro',
    scope: { kind: 'section', section_index: 0 },
    sourceFingerprint: 'd'.repeat(24),
    scoreVsCurrent: 0.77,
  });
  assert.equal(session.projectId, 'proj');
  assert.equal(session.fingerprintPrefix, 'd'.repeat(12));
  assert.equal(session.scoreVsCurrent, 0.77);
  assert.equal(formatSimilarityScore(0.77), '77% affinity');
  assert.equal(formatSimilarityScore(null), '—');
  assert.equal(fingerprintPrefix('abcdef'), 'abcdef');
});

test('cosineSimilarity and similarity query payload', () => {
  assert.equal(cosineSimilarity([1, 0], [1, 0]), 1);
  assert.ok(Math.abs(cosineSimilarity([1, 0], [0, 1])) < 1e-9);
  assert.equal(cosineSimilarity([1], [1, 2]), null);

  const payload = buildSimilarityQueryPayload({
    composition: minimalComposition(),
    scope: { kind: 'composition' },
    topK: 5,
    excludeProjectId: 'self',
  });
  assert.equal(payload.ok, true);
  assert.equal(payload.query.top_k, 5);
  assert.equal(payload.query.exclude_project_id, 'self');
  assert.equal(payload.query.corpus.kind, 'projects');
});

test('formatSimilarityHitLabel stays artist-free', () => {
  const label = formatSimilarityHitLabel({
    projectId: 'abc12345xxxx',
    scope: { kind: 'section', section_index: 2, section_id: 'bridge' },
  }, { projectNameById: { abc12345xxxx: 'Night Sketch' } });
  assert.match(label, /Night Sketch/);
  assert.match(label, /bridge/);
  assert.doesNotMatch(label, /style of/i);
  assert.doesNotMatch(label, /artist/i);
});
