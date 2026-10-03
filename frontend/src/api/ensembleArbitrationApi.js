/**
 * Ensemble arbitration HTTP client.
 * Opening status/strategies never starts fan-out.
 */

import axios from 'axios';

export class EnsembleArbitrationApiError extends Error {
  constructor(message, { code = null, status = null } = {}) {
    super(message);
    this.name = 'EnsembleArbitrationApiError';
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
  console.debug('[ensembleArbitrationApi] request failed', { route, code, status });
  const message = detail && typeof detail === 'object' && typeof detail.message === 'string'
    ? detail.message
    : 'Ensemble arbitration request failed';
  return new EnsembleArbitrationApiError(message, { code, status });
}

export async function getEnsembleArbitrationStatus() {
  try {
    const response = await axios.get('/ensemble/arbitration/status');
    return response.data;
  } catch (error) {
    throw failure(error, 'GET /ensemble/arbitration/status');
  }
}

export async function getEnsembleArbitrationStrategies() {
  try {
    const response = await axios.get('/ensemble/arbitration/strategies');
    return response.data;
  } catch (error) {
    throw failure(error, 'GET /ensemble/arbitration/strategies');
  }
}

export async function previewEnsembleArbitration(body) {
  try {
    const response = await axios.post('/ensemble/arbitration/preview', body);
    console.debug('[ensembleArbitrationApi] preview ok', {
      survivorCount: Array.isArray(response.data?.candidates)
        ? response.data.candidates.length
        : 0,
      suggestedId: response.data?.suggested_candidate_id || null,
    });
    return response.data;
  } catch (error) {
    throw failure(error, 'POST /ensemble/arbitration/preview');
  }
}
