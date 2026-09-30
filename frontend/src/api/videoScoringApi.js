import axios from 'axios';

import { collaborationHeaders } from '../utils/collaborationAccess.js';

export class VideoScoringApiError extends Error {
  constructor(code, status) {
    super(code || 'video_scoring_invalid');
    this.name = 'VideoScoringApiError';
    this.code = code || 'video_scoring_invalid';
    this.status = status ?? null;
  }
}

function projectPath(projectId, suffix) {
  return `/projects/${encodeURIComponent(projectId)}${suffix}`;
}

export function videoAssetMediaUrl(projectId) {
  return projectPath(projectId, '/video-asset/media');
}

export async function getVideoAsset(projectId) {
  return request('get', projectPath(projectId, '/video-asset'));
}

export async function uploadVideoAsset(projectId, file) {
  const body = new FormData();
  body.append('file', file);
  return request('post', projectPath(projectId, '/video-asset'), body);
}

export async function deleteVideoAsset(projectId) {
  return request('delete', projectPath(projectId, '/video-asset'));
}

export async function getVideoScoring(projectId) {
  return request('get', projectPath(projectId, '/video-scoring'));
}

export async function putVideoScoring(projectId, payload) {
  return request('put', projectPath(projectId, '/video-scoring'), payload);
}

export async function getVideoScoringMap(projectId, params) {
  const search = new URLSearchParams();
  if (params.videoSeconds != null) {
    search.set('video_seconds', String(params.videoSeconds));
  }
  if (params.tick != null) {
    search.set('tick', String(params.tick));
  }
  if (params.bar != null) {
    search.set('bar', String(params.bar));
  }
  return request('get', `${projectPath(projectId, '/video-scoring/map')}?${search.toString()}`);
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
    const detail = error.response?.data?.detail;
    const code = detail && typeof detail === 'object' ? detail.code : null;
    throw new VideoScoringApiError(code, status);
  }
}
