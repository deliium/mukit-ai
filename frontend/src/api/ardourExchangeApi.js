/** Ardour exchange HTTP client. Never logs MIDI/package payloads. */

import axios from 'axios';

import { createAppLogger } from '../utils/appLogger.js';

const logger = createAppLogger('ardourExchange');

export class ArdourExchangeApiError extends Error {
  constructor(message, { code = null, status = null } = {}) {
    super(message);
    this.name = 'ArdourExchangeApiError';
    this.code = code;
    this.status = status;
  }
}

function failure(error, route) {
  const detail = error?.response?.data?.detail;
  const code = detail && typeof detail === 'object' && typeof detail.code === 'string'
    ? detail.code
    : null;
  const status = error?.response?.status ?? null;
  logger.debug('request failed', { route, code, status });
  const message = detail && typeof detail === 'object' && typeof detail.message === 'string'
    ? detail.message
    : 'Ardour exchange request failed';
  return new ArdourExchangeApiError(message, { code, status });
}

export async function getArdourExchangeStatus() {
  try {
    const response = await axios.get('/ardour/exchange/status');
    logger.debug('status', { enabled: response.data?.enabled });
    return response.data;
  } catch (error) {
    throw failure(error, 'GET /ardour/exchange/status');
  }
}

export async function getArdourExchangeContext() {
  try {
    const response = await axios.get('/ardour/exchange/context');
    return response.data;
  } catch (error) {
    throw failure(error, 'GET /ardour/exchange/context');
  }
}

export async function listArdourExchangePackages() {
  try {
    const response = await axios.get('/ardour/exchange/packages');
    logger.debug('list', { count: response.data?.length });
    return response.data;
  } catch (error) {
    throw failure(error, 'GET /ardour/exchange/packages');
  }
}

export async function ingestArdourExchangePackageId(packageId) {
  try {
    const response = await axios.post('/ardour/exchange/ingest', {
      package_id: String(packageId || '').trim(),
    });
    logger.info('ingest', { package_id: response.data?.package_id });
    return response.data;
  } catch (error) {
    throw failure(error, 'POST /ardour/exchange/ingest');
  }
}

export async function ingestArdourExchangeZip(file) {
  try {
    const form = new FormData();
    form.append('file', file);
    const response = await axios.post('/ardour/exchange/ingest', form);
    logger.info('ingest', { package_id: response.data?.package_id });
    return response.data;
  } catch (error) {
    throw failure(error, 'POST /ardour/exchange/ingest multipart');
  }
}

export async function getArdourExchangePreview() {
  try {
    const response = await axios.get('/ardour/exchange/preview');
    return response.data;
  } catch (error) {
    throw failure(error, 'GET /ardour/exchange/preview');
  }
}

export async function discardArdourExchangePreview() {
  try {
    const response = await axios.delete('/ardour/exchange/preview');
    logger.info('discard', { cleared: true });
    return response.data;
  } catch (error) {
    throw failure(error, 'DELETE /ardour/exchange/preview');
  }
}

export async function applyArdourExchangePreview() {
  try {
    const response = await axios.post('/ardour/exchange/apply');
    logger.info('apply', { package_id: response.data?.manifest?.package_id });
    return response.data;
  } catch (error) {
    throw failure(error, 'POST /ardour/exchange/apply');
  }
}

export async function realizeArdourExchange(intent, { instruction = null, candidateCount = 1 } = {}) {
  try {
    const response = await axios.post('/ardour/exchange/realize', {
      schema_version: 'ardour.exchange.realize_request.v1',
      intent,
      instruction: instruction || null,
      candidate_count: candidateCount,
    });
    logger.info('realize', { intent, candidate_count: response.data?.preview?.candidates?.length });
    return response.data;
  } catch (error) {
    throw failure(error, 'POST /ardour/exchange/realize');
  }
}

export async function prepareArdourExchange(body = {}) {
  try {
    const response = await axios.post('/ardour/exchange/prepare', {
      schema_version: 'ardour.exchange.prepare.v1',
      use_preview_alignment: body.use_preview_alignment !== false,
      track_ids: body.track_ids || [],
      start_bar: body.start_bar ?? null,
      bar_count: body.bar_count ?? null,
      track_name: body.track_name ?? null,
      stem_id: body.stem_id ?? null,
    });
    logger.info('prepare', { package_id: response.data?.package_id });
    return response.data;
  } catch (error) {
    throw failure(error, 'POST /ardour/exchange/prepare');
  }
}

export function ardourExchangeDownloadUrl(packageId) {
  return `/ardour/exchange/packages/${encodeURIComponent(packageId)}/download`;
}
