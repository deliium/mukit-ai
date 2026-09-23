import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  LIVE_ACCOMPANIMENT_CHUNK_SCHEMA,
  LIVE_EVENTS_FORBIDDEN_IN_REQUEST,
  LIVE_FEATURES_TOO_LARGE,
  LIVE_HORIZON_INVALID,
  LIVE_PREDICT_REQUEST_SCHEMA,
  LIVE_SESSION_SCHEMA,
  createIdleLiveSession,
  findForbiddenPredictKey,
  normalizeLiveAccompanimentChunk,
  readLiveHorizonBounds,
  validateLiveHorizon,
  validateLivePredictFeatures,
  validateLivePredictRequest,
} from './liveSessionContracts.js';

describe('liveSessionContracts', () => {
  it('creates idle live.session.v1 snapshot', () => {
    const session = createIdleLiveSession('sess-1');
    assert.equal(session.schema, LIVE_SESSION_SCHEMA);
    assert.equal(session.phase, 'idle');
    assert.equal(session.session_id, 'sess-1');
    assert.equal(session.degradation.active, false);
  });

  it('rejects forbidden predict keys', () => {
    const result = findForbiddenPredictKey({ features: { events: [] } });
    assert.equal(result.ok, false);
    assert.equal(result.code, LIVE_EVENTS_FORBIDDEN_IN_REQUEST);
  });

  it('validates horizon within defaults', () => {
    const bounds = readLiveHorizonBounds(() => undefined);
    const ok = validateLiveHorizon({ bars: 1, ms: 2000 }, bounds);
    assert.equal(ok.ok, true);
    const bad = validateLiveHorizon({ bars: 9, ms: 2000 }, bounds);
    assert.equal(bad.ok, false);
    assert.equal(bad.code, LIVE_HORIZON_INVALID);
  });

  it('rejects oversized features', () => {
    const bounds = { ...readLiveHorizonBounds(() => undefined), maxFeaturesBytes: 32 };
    const result = validateLivePredictFeatures(
      { density: 0.5, padding: 'x'.repeat(200) },
      bounds,
    );
    assert.equal(result.ok, false);
    assert.equal(result.code, LIVE_FEATURES_TOO_LARGE);
  });

  it('validates predict request without composition/events', () => {
    const body = {
      schema: LIVE_PREDICT_REQUEST_SCHEMA,
      session_id: 's1',
      request_id: 'r1',
      clock: { tick: 0, bar: 1, beat: 1 },
      active_harmony: { symbol: 'Cmaj7' },
      features: { density: 0.4 },
      horizon: { bars: 1, ms: 2000 },
    };
    const ok = validateLivePredictRequest(body, readLiveHorizonBounds(() => undefined));
    assert.equal(ok.ok, true);

    const bad = validateLivePredictRequest(
      { ...body, composition: { tracks: [] } },
      readLiveHorizonBounds(() => undefined),
    );
    assert.equal(bad.ok, false);
    assert.equal(bad.code, LIVE_EVENTS_FORBIDDEN_IN_REQUEST);
  });

  it('normalizes accompaniment chunks and caps events', () => {
    const bounds = { ...readLiveHorizonBounds(() => undefined), maxEventsPerChunk: 2 };
    const ok = normalizeLiveAccompanimentChunk(
      {
        request_id: 'r1',
        session_id: 's1',
        start_tick: 480,
        events: [
          { pitch: 60, start_tick: 480, duration_ticks: 240, velocity: 90 },
        ],
        source: 'fake',
        generated_at_ms: 1,
      },
      bounds,
    );
    assert.equal(ok.ok, true);
    assert.equal(ok.chunk.schema, LIVE_ACCOMPANIMENT_CHUNK_SCHEMA);
    assert.equal(ok.chunk.events.length, 1);

    const tooMany = normalizeLiveAccompanimentChunk(
      {
        request_id: 'r1',
        session_id: 's1',
        start_tick: 0,
        events: [
          { pitch: 60, start_tick: 0, duration_ticks: 1 },
          { pitch: 62, start_tick: 1, duration_ticks: 1 },
          { pitch: 64, start_tick: 2, duration_ticks: 1 },
        ],
        source: 'local_pattern',
        generated_at_ms: 1,
      },
      bounds,
    );
    assert.equal(tooMany.ok, false);
  });
});
