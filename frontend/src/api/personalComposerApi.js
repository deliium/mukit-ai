/**
 * Personal composer jobs. Listing does not start training.
 */

import axios from 'axios';

export async function listPersonalComposers() {
  return request('get', '/personal-composers');
}

export async function startPersonalComposer(payload) {
  return request('post', '/personal-composers', payload);
}

export async function stopPersonalComposer(adapterId) {
  return request('post', `/personal-composers/${encodeURIComponent(adapterId)}/stop`);
}

export async function resumePersonalComposer(adapterId) {
  return request('post', `/personal-composers/${encodeURIComponent(adapterId)}/resume`);
}

export async function evaluatePersonalComposer(adapterId) {
  return request('post', `/personal-composers/${encodeURIComponent(adapterId)}/evaluate`);
}

export async function deletePersonalComposer(adapterId) {
  return request('delete', `/personal-composers/${encodeURIComponent(adapterId)}`);
}

async function request(method, endpoint, data) {
  console.debug('[personalComposerApi] Request started', { method, endpoint });
  try {
    const response = await axios({ method, url: endpoint, data });
    console.debug('[personalComposerApi] Request completed', {
      method,
      endpoint,
      status: response.status,
    });
    return response.data ?? null;
  } catch (error) {
    const status = error.response?.status ?? null;
    const rawDetail = error.response?.data?.detail;
    const code = rawDetail && typeof rawDetail === 'object' ? rawDetail.code : null;
    console.warn('[personalComposerApi] Request failed', {
      method,
      endpoint,
      status,
      code,
    });
    const message =
      (rawDetail && typeof rawDetail === 'object' && rawDetail.message)
      || (typeof rawDetail === 'string' ? rawDetail : null)
      || error.message
      || 'Personal composer request failed';
    const err = new Error(message);
    err.status = status;
    err.code = code;
    throw err;
  }
}
