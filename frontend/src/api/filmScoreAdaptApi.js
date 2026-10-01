import axios from 'axios';

import { collaborationHeaders } from '../utils/collaborationAccess.js';

export class FilmScoreAdaptApiError extends Error {
  constructor(code, status) {
    super(code || 'film_adapt_invalid');
    this.name = 'FilmScoreAdaptApiError';
    this.code = code || 'film_adapt_invalid';
    this.status = status ?? null;
  }
}

function projectPath(projectId, suffix) {
  return `/projects/${encodeURIComponent(projectId)}${suffix}`;
}

export async function previewFilmScoreAdapt(projectId, payload) {
  return request('post', projectPath(projectId, '/film-score/adapt/preview'), payload);
}

export async function commitFilmScoreAdapt(projectId, payload) {
  return request('post', projectPath(projectId, '/film-score/adapt/commit'), payload);
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
    const code = error.response?.data?.detail?.code || error.response?.data?.code || 'film_adapt_invalid';
    throw new FilmScoreAdaptApiError(code, status);
  }
}
