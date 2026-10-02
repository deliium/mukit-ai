/** Preference learning HTTP helpers.

Failures log the route code only. Feature vectors are not logged.
 */

import axios from 'axios';

export class PreferenceApiError extends Error {
  constructor(message, { code = null, status = null } = {}) {
    super(message);
    this.name = 'PreferenceApiError';
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
  console.debug('[preferenceApi] request failed', { route, code, status });
  const message = detail && typeof detail === 'object' && typeof detail.message === 'string'
    ? detail.message
    : 'Preference request failed';
  return new PreferenceApiError(message, { code, status });
}

export async function getPreferenceSettings() {
  try {
    const response = await axios.get('/preferences/settings');
    return response.data;
  } catch (error) {
    throw failure(error, 'GET /preferences/settings');
  }
}

export async function putPreferenceSettings(body) {
  try {
    const response = await axios.put('/preferences/settings', {
      collection_enabled: Boolean(body.collection_enabled),
      ranking_enabled: Boolean(body.ranking_enabled),
    });
    return response.data;
  } catch (error) {
    throw failure(error, 'PUT /preferences/settings');
  }
}

export async function listPreferenceChoices(limit = 20) {
  try {
    const response = await axios.get('/preferences/choices', { params: { limit } });
    return Array.isArray(response.data) ? response.data : [];
  } catch (error) {
    throw failure(error, 'GET /preferences/choices');
  }
}

export async function resetPreferenceData() {
  try {
    const response = await axios.delete('/preferences/data');
    return response.data;
  } catch (error) {
    throw failure(error, 'DELETE /preferences/data');
  }
}

export async function recordPreferenceChoice({ surface, chosen_candidate_id: chosenCandidateId }) {
  try {
    const response = await axios.post('/preferences/choices', {
      surface,
      chosen_candidate_id: chosenCandidateId,
    });
    return response.data;
  } catch (error) {
    throw failure(error, 'POST /preferences/choices');
  }
}

export async function rankPreferenceCandidates({ surface, candidate_ids: candidateIds }) {
  try {
    const response = await axios.post('/preferences/rank', {
      surface,
      candidate_ids: candidateIds,
    });
    return response.data;
  } catch (error) {
    throw failure(error, 'POST /preferences/rank');
  }
}
