/**
 * Composer Profiles API client — durable musical preference documents.
 * Never logs full profile bodies.
 */

import axios from 'axios';

const STORAGE_KEY = 'composerProfile:v1';

export function loadComposerProfileSessionPreference(storage = globalThis.localStorage) {
  try {
    if (!storage || typeof storage.getItem !== 'function') {
      return { profileId: null, strength: 'off' };
    }
    const raw = storage.getItem(STORAGE_KEY);
    if (!raw) return { profileId: null, strength: 'off' };
    const parsed = JSON.parse(raw);
    return {
      profileId: typeof parsed?.profileId === 'string' ? parsed.profileId : null,
      strength: ['off', 'light', 'normal', 'strong'].includes(parsed?.strength)
        ? parsed.strength
        : 'off',
    };
  } catch {
    return { profileId: null, strength: 'off' };
  }
}

export function saveComposerProfileSessionPreference(
  { profileId = null, strength = 'off' } = {},
  storage = globalThis.localStorage,
) {
  try {
    if (!storage || typeof storage.setItem !== 'function') {
      return;
    }
    storage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        profileId: profileId || null,
        strength: strength || 'off',
      }),
    );
  } catch {
    // ignore quota / private mode
  }
}

export async function listComposerProfiles() {
  return request('get', '/composer-profiles');
}

export async function createComposerProfile(payload) {
  return request('post', '/composer-profiles', payload);
}

export async function getComposerProfile(profileId) {
  return request('get', `/composer-profiles/${encodeURIComponent(profileId)}`);
}

export async function updateComposerProfile(profileId, payload) {
  return request('put', `/composer-profiles/${encodeURIComponent(profileId)}`, payload);
}

export async function resetComposerProfile(profileId, payload) {
  return request('post', `/composer-profiles/${encodeURIComponent(profileId)}/reset`, payload);
}

export async function promoteComposerProfile(profileId, payload) {
  return request('post', `/composer-profiles/${encodeURIComponent(profileId)}/promote`, payload);
}

export async function deleteComposerProfile(profileId) {
  return request('delete', `/composer-profiles/${encodeURIComponent(profileId)}`);
}

export async function deriveComposerProfile(payload) {
  return request('post', '/composer-profiles/derive', payload);
}

export async function previewComposerProfile(profileId, payload) {
  return request('post', `/composer-profiles/${encodeURIComponent(profileId)}/preview`, payload);
}

export async function compareComposerProfiles(payload) {
  return request('post', '/composer-profiles/compare', payload);
}

export async function exportComposerProfile(profileId) {
  return request('get', `/composer-profiles/${encodeURIComponent(profileId)}/export`);
}

export async function importComposerProfile(payload) {
  return request('post', '/composer-profiles/import', payload);
}

async function request(method, endpoint, data) {
  console.debug('[composerProfileApi] Request started', { method, endpoint });
  try {
    const response = await axios({ method, url: endpoint, data });
    console.debug('[composerProfileApi] Request completed', {
      method,
      endpoint,
      status: response.status,
    });
    return response.data ?? null;
  } catch (error) {
    const status = error.response?.status ?? null;
    const rawDetail = error.response?.data?.detail;
    const code = rawDetail && typeof rawDetail === 'object' ? rawDetail.code : null;
    console.warn('[composerProfileApi] Request failed', {
      method,
      endpoint,
      status,
      code,
    });
    const message =
      (rawDetail && typeof rawDetail === 'object' && rawDetail.message)
      || (typeof rawDetail === 'string' ? rawDetail : null)
      || error.message
      || 'Composer profile request failed';
    const err = new Error(message);
    err.status = status;
    err.code = code;
    err.detail = rawDetail;
    throw err;
  }
}
