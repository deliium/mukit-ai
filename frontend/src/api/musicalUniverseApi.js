import axios from 'axios';

import { collaborationHeaders } from '../utils/collaborationAccess.js';

export class MusicalUniverseApiError extends Error {
  constructor(code, status) {
    super(code || 'musical_universe_invalid');
    this.name = 'MusicalUniverseApiError';
    this.code = code || 'musical_universe_invalid';
    this.status = status ?? null;
  }
}

export async function createMusicalUniverse(name, projectId) {
  return request('post', '/musical-universes', { name, project_id: projectId });
}

export async function getProjectMusicalUniverse(projectId) {
  return request('get', `/projects/${encodeURIComponent(projectId)}/musical-universe`);
}

export async function getMusicalUniverse(universeId) {
  return request('get', `/musical-universes/${encodeURIComponent(universeId)}`);
}

export async function addMusicalUniverseMember(universeId, projectId) {
  return request('post', `/musical-universes/${encodeURIComponent(universeId)}/members`, {
    project_id: projectId,
  });
}

export async function reuseMusicalUniverseTheme(universeId, themeId, payload) {
  return request(
    'post',
    `/musical-universes/${encodeURIComponent(universeId)}/themes/${encodeURIComponent(themeId)}/reuse`,
    payload,
  );
}

async function request(method, url, data) {
  try {
    const response = await axios({
      method,
      url,
      data,
      headers: collaborationHeaders(),
    });
    return response.data ?? null;
  } catch (error) {
    const status = error.response?.status ?? null;
    const code = error.response?.data?.detail?.code || error.response?.data?.code || 'musical_universe_invalid';
    throw new MusicalUniverseApiError(code, status);
  }
}
