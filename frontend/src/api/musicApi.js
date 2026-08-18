import axios from 'axios';

export async function getHealth() {
  return request('get', '/health');
}

export async function getLlmModels() {
  return request('get', '/llm/models');
}

export async function generateLlmMusicJson(payload) {
  return request('post', '/llm/generate-music-json', payload);
}

async function request(method, endpoint, data, config = {}) {
  console.debug('[musicApi] Request started', { method, endpoint });
  try {
    const response = await axios({ method, url: endpoint, data, ...config });
    console.debug('[musicApi] Request completed', { method, endpoint, status: response.status });
    return response.data;
  } catch (error) {
    const detail = error.response?.data?.detail || error.message || 'Unknown request failure';
    console.error('[musicApi] Request failed', {
      method,
      endpoint,
      status: error.response?.status,
      detail,
    });
    throw new Error(detail);
  }
}
