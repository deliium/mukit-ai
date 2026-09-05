import axios from 'axios';
import { isCanonicalComposition, validateMusicJson } from '../utils/musicJsonValidation.js';

export async function getHealth() {
  return request('get', '/health');
}

export async function getLlmModels() {
  return request('get', '/llm/models');
}

export async function generateLlmMusicJson(payload) {
  const response = await request('post', '/llm/generate-music-json', payload);
  const validation = validateMusicJson(response.music);
  console.debug('[musicApi] LLM music response validation completed', {
    valid: validation.valid,
    schemaVersion: response.music?.schema_version || 'legacy',
    canonical: isCanonicalComposition(response.music),
    warningCount: response.warnings?.length || 0,
  });
  if (!validation.valid) {
    console.error('[musicApi] LLM music response failed validation', { message: validation.message });
    throw new Error(validation.message);
  }
  return response;
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
