/**
 * Model Lab research HTTP client.
 * Opening list/catalog never starts training.
 */

import axios from 'axios';

export async function getModelLabStatus() {
  return request('get', '/model-lab/status');
}

export async function listModelLabDatasets() {
  return request('get', '/model-lab/datasets');
}

export async function listModelLabPresets() {
  return request('get', '/model-lab/presets');
}

export async function listModelLabExperiments() {
  return request('get', '/model-lab/experiments');
}

export async function getModelLabExperiment(experimentId) {
  return request('get', `/model-lab/experiments/${encodeURIComponent(experimentId)}`);
}

export async function getModelLabMetrics(experimentId) {
  return request('get', `/model-lab/experiments/${encodeURIComponent(experimentId)}/metrics`);
}

export async function getModelLabCheckpoints(experimentId) {
  return request('get', `/model-lab/experiments/${encodeURIComponent(experimentId)}/checkpoints`);
}

export async function getModelLabListening(experimentId) {
  return request('get', `/model-lab/experiments/${encodeURIComponent(experimentId)}/listening`);
}

export async function createModelLabExperiment(payload) {
  return request('post', '/model-lab/experiments', payload);
}

export async function evaluateModelLabExperiment(experimentId) {
  return request('post', `/model-lab/experiments/${encodeURIComponent(experimentId)}/evaluate`);
}

export async function stopModelLabExperiment(experimentId) {
  return request('post', `/model-lab/experiments/${encodeURIComponent(experimentId)}/stop`);
}

export async function registerModelLabExperiment(experimentId, payload) {
  return request('post', `/model-lab/experiments/${encodeURIComponent(experimentId)}/register`, payload);
}

export async function deleteModelLabExperiment(experimentId) {
  return request('delete', `/model-lab/experiments/${encodeURIComponent(experimentId)}`);
}

export async function compareModelLabExperiments(experimentIds) {
  return request('post', '/model-lab/compare', { experiment_ids: experimentIds });
}

async function request(method, endpoint, data) {
  console.debug('[modelLabApi] Request started', { method, endpoint });
  try {
    const response = await axios({ method, url: endpoint, data });
    console.debug('[modelLabApi] Request completed', {
      method,
      endpoint,
      status: response.status,
    });
    return response.data ?? null;
  } catch (error) {
    const status = error.response?.status ?? null;
    const rawDetail = error.response?.data?.detail;
    const code = rawDetail && typeof rawDetail === 'object' ? rawDetail.code : null;
    console.warn('[modelLabApi] Request failed', {
      method,
      endpoint,
      status,
      code,
    });
    const message =
      (rawDetail && typeof rawDetail === 'object' && rawDetail.message)
      || (typeof rawDetail === 'string' ? rawDetail : null)
      || error.message
      || 'Model Lab request failed';
    const err = new Error(message);
    err.status = status;
    err.code = code;
    throw err;
  }
}
