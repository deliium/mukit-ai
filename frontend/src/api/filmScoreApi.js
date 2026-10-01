import axios from 'axios';

import { collaborationHeaders } from '../utils/collaborationAccess.js';

export class FilmScoreApiError extends Error {
  constructor(code, status) {
    super(code || 'film_score_invalid');
    this.name = 'FilmScoreApiError';
    this.code = code || 'film_score_invalid';
    this.status = status ?? null;
  }
}

function projectPath(projectId, suffix) {
  return `/projects/${encodeURIComponent(projectId)}${suffix}`;
}

export async function previewFilmScore(projectId, payload) {
  return request('post', projectPath(projectId, '/film-score/preview'), payload);
}

export async function commitFilmScore(projectId, payload) {
  return request('post', projectPath(projectId, '/film-score/commit'), payload);
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
    const code = error.response?.data?.detail?.code || error.response?.data?.code || 'film_score_invalid';
    throw new FilmScoreApiError(code, status);
  }
}
