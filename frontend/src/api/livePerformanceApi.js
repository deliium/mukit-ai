/**
 * Co-performance cold-path predict client.
 * Never awaited inside MIDI/Transport callbacks — callers fire-and-forget.
 * Optional WS streaming deferred until measured HTTP budget fails (Task 8).
 */

import axios from 'axios';
import { createAppLogger } from '../utils/appLogger.js';
import {
  LIVE_PREDICT_REQUEST_SCHEMA,
  validateLivePredictRequest,
} from '../utils/liveSessionContracts.js';

const log = createAppLogger('liveAccompaniment');

/** Stable skip code when AbortController is missing (non-browser / broken env). */
export const LIVE_PREDICT_NO_ABORT = 'live_predict_no_abort';

/**
 * Build an AbortController for cold-path predict without relying on bare
 * `AbortController` (eslint no-undef) or throwing in odd runtimes.
 *
 * @returns {{ ok: true, controller: AbortController, code: null }
 *   | { ok: false, controller: null, code: typeof LIVE_PREDICT_NO_ABORT }}
 */
export function createLivePredictAbortController() {
  const AbortCtl = globalThis.AbortController;
  if (typeof AbortCtl !== 'function') {
    return { ok: false, controller: null, code: LIVE_PREDICT_NO_ABORT };
  }
  return { ok: true, controller: new AbortCtl(), code: null };
}

export class LivePerformanceApiError extends Error {
  constructor(message, { status = null, code = null, detail = null } = {}) {
    super(message);
    this.name = 'LivePerformanceApiError';
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

/**
 * @param {object} body — live.accompaniment.predict.request.v1
 * @param {{ signal?: AbortSignal }} [opts]
 */
export async function predictLiveAccompaniment(body, opts = {}) {
  const payload = {
    schema_version: LIVE_PREDICT_REQUEST_SCHEMA,
    ...body,
  };
  if (payload.schema && !payload.schema_version) {
    payload.schema_version = payload.schema;
  }
  delete payload.schema;

  const validation = validateLivePredictRequest({
    ...payload,
    schema: LIVE_PREDICT_REQUEST_SCHEMA,
  });
  if (!validation.ok) {
    log.warn('predict client reject', { code: validation.code });
    throw new LivePerformanceApiError('Invalid live predict request', {
      status: 422,
      code: validation.code,
      detail: validation.details || null,
    });
  }

  log.info('predict request', {
    request_id: String(payload.request_id || '').slice(0, 12),
    session_id_len: String(payload.session_id || '').length,
  });

  try {
    const response = await axios.post('/live/accompaniment/predict', payload, {
      signal: opts.signal,
    });
    const data = response.data ?? null;
    log.info('predict completed', {
      request_id: String(data?.request_id || '').slice(0, 12),
      event_count: Array.isArray(data?.events) ? data.events.length : 0,
      latency_ms: data?.latency_ms?.generation ?? null,
    });
    return data;
  } catch (error) {
    if (axios.isCancel?.(error) || error.code === 'ERR_CANCELED' || error.name === 'CanceledError') {
      log.warn('predict aborted', { code: 'aborted' });
      throw new LivePerformanceApiError('Predict aborted', { status: null, code: 'aborted' });
    }
    const status = error.response?.status ?? null;
    const rawDetail = error.response?.data?.detail;
    const code = rawDetail && typeof rawDetail === 'object' ? rawDetail.code : null;
    log.warn('predict failed', { status, code });
    throw new LivePerformanceApiError(
      (rawDetail && typeof rawDetail === 'object' && rawDetail.message)
        || (typeof rawDetail === 'string' ? rawDetail : null)
        || error.message
        || 'Live predict failed',
      { status, code, detail: rawDetail },
    );
  }
}
