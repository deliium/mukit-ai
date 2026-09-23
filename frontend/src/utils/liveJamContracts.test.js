import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  JAM_BELIEF_INSIDE_FEATURES,
  JAM_COMMIT_MAP_INCOMPLETE,
  JAM_CONTROLS_INVALID,
  JAM_HARMONY_HOLD,
  JAM_MODE_INVALID,
  JAM_MODES,
  JAM_ROLE_MATRIX,
  LIVE_PERFORMANCE_FEATURES_SCHEMA,
  assertNoBeliefInsideFeatures,
  clampJamControls,
  createDefaultJamControls,
  createEmptyLivePerformanceFeatures,
  createIdleHarmonyBelief,
  isJamMode,
  normalizeHarmonyBelief,
  readLiveJamSettings,
  resolveJamRolePartition,
  scaleHysteresisForResponsiveness,
  validateLivePerformanceFeatures,
} from './liveJamContracts.js';
import { LIVE_FEATURES_TOO_LARGE } from './liveSessionContracts.js';

describe('liveJamContracts', () => {
  it('exposes locked mode → role matrix', () => {
    assert.deepEqual(JAM_ROLE_MATRIX.user_melody.user_roles, ['melody']);
    assert.deepEqual(JAM_ROLE_MATRIX.user_melody.ai_roles, ['bass', 'accompaniment']);
    assert.deepEqual(JAM_ROLE_MATRIX.user_melody.optional_ai, ['texture']);
    assert.deepEqual(JAM_ROLE_MATRIX.user_chords.user_roles, ['harmony']);
    assert.deepEqual(JAM_ROLE_MATRIX.user_chords.ai_roles, ['melody', 'bass', 'texture']);
    assert.ok(JAM_MODES.includes('user_melody'));
    assert.ok(isJamMode('user_chords'));
    assert.equal(isJamMode('duet'), false);
  });

  it('resolves optional texture only when complexity ≥ medium for user_melody', () => {
    const low = resolveJamRolePartition('user_melody', 'low');
    assert.deepEqual(low.ai_roles, ['bass', 'accompaniment']);
    const med = resolveJamRolePartition('user_melody', 'medium');
    assert.deepEqual(med.ai_roles, ['bass', 'accompaniment', 'texture']);
    const chords = resolveJamRolePartition('user_chords', 'high');
    assert.deepEqual(chords.ai_roles, ['melody', 'bass', 'texture']);
    assert.throws(() => resolveJamRolePartition(/** @type {any} */ ('nope')), (err) => {
      assert.equal(err.message, JAM_MODE_INVALID);
      return true;
    });
  });

  it('clamps jam controls enums and instrument_set', () => {
    const ok = clampJamControls({
      complexity: 'high',
      density: 'low',
      style: 'alberti',
      responsiveness: 'high',
      instrument_set: {
        bass: { midi_program: 32, track_id: 'trk-bass' },
        nope: { midi_program: 0 },
        melody: { midi_program: 999 },
      },
    });
    assert.equal(ok.ok, true);
    assert.equal(ok.controls.complexity, 'high');
    assert.equal(ok.controls.style, 'alberti');
    assert.equal(ok.controls.instrument_set.bass.midi_program, 32);
    assert.equal(ok.controls.instrument_set.bass.track_id, 'trk-bass');
    assert.equal(ok.controls.instrument_set.nope, undefined);
    assert.equal(ok.controls.instrument_set.melody.midi_program, undefined);

    const bad = clampJamControls({ complexity: 'ultra', density: 'medium', style: 'block', responsiveness: 'medium' });
    assert.equal(bad.ok, false);
    assert.equal(bad.code, JAM_CONTROLS_INVALID);

    const defaults = createDefaultJamControls();
    assert.equal(defaults.complexity, 'medium');
    assert.equal(defaults.style, 'block');
  });

  it('scales hysteresis dwell by responsiveness within floor', () => {
    const settings = readLiveJamSettings(() => undefined);
    const high = scaleHysteresisForResponsiveness('high', settings);
    const low = scaleHysteresisForResponsiveness('low', settings);
    assert.ok(high.dwellMs < settings.harmonyDwellMs);
    assert.ok(high.dwellMs >= settings.harmonyDwellMsFloor);
    assert.ok(low.dwellMs > settings.harmonyDwellMs);
    assert.ok(high.confidenceMin <= settings.harmonyConfidenceMin);
  });

  it('rejects belief nested inside features', () => {
    const nested = assertNoBeliefInsideFeatures({
      schema: LIVE_PERFORMANCE_FEATURES_SCHEMA,
      belief: { symbol: 'C', confidence: 0.9, held: false },
    });
    assert.equal(nested.ok, false);
    assert.equal(nested.code, JAM_BELIEF_INSIDE_FEATURES);

    const shape = assertNoBeliefInsideFeatures({
      symbol: 'G',
      confidence: 0.8,
      held: true,
    });
    assert.equal(shape.ok, false);
    assert.equal(shape.code, JAM_BELIEF_INSIDE_FEATURES);

    const ok = assertNoBeliefInsideFeatures(
      createEmptyLivePerformanceFeatures({ tick: 0, bar: 1 }),
    );
    assert.equal(ok.ok, true);
  });

  it('validates live.performance.features.v1 and rejects oversized / forbidden', () => {
    const empty = createEmptyLivePerformanceFeatures({ bar: 2, tick: 480 });
    const ok = validateLivePerformanceFeatures(empty);
    assert.equal(ok.ok, true);
    assert.equal(ok.features.schema, LIVE_PERFORMANCE_FEATURES_SCHEMA);
    assert.equal(ok.features.beat.bar, 2);

    const withBelief = validateLivePerformanceFeatures({
      ...empty,
      belief: { symbol: 'Am', confidence: 0.7, held: false },
    });
    assert.equal(withBelief.ok, false);
    assert.equal(withBelief.code, JAM_BELIEF_INSIDE_FEATURES);

    const huge = validateLivePerformanceFeatures(
      { ...empty, padding: 'x'.repeat(5000) },
      { maxFeaturesBytes: 64 },
    );
    assert.equal(huge.ok, false);
    assert.equal(huge.code, LIVE_FEATURES_TOO_LARGE);
  });

  it('normalizes belief separately from features', () => {
    const belief = normalizeHarmonyBelief({
      symbol: 'Dm7',
      confidence: 0.72,
      held: true,
      reason_code: JAM_HARMONY_HOLD,
    });
    assert.equal(belief.symbol, 'Dm7');
    assert.equal(belief.held, true);
    assert.equal(belief.reason_code, JAM_HARMONY_HOLD);
    assert.equal(createIdleHarmonyBelief().symbol, null);
    assert.equal(JAM_COMMIT_MAP_INCOMPLETE, 'jam_commit_map_incomplete');
  });
});
