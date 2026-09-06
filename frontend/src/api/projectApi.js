import axios from 'axios';

export async function listProjects() {
  return request('get', '/projects');
}

export async function createProject(payload) {
  return request('post', '/projects', payload);
}

export async function getProject(projectId) {
  return request('get', `/projects/${encodeURIComponent(projectId)}`);
}

export async function patchProject(projectId, payload) {
  return request('patch', `/projects/${encodeURIComponent(projectId)}`, payload);
}

export async function duplicateProject(projectId) {
  return request('post', `/projects/${encodeURIComponent(projectId)}/duplicate`);
}

export async function deleteProject(projectId) {
  return request('delete', `/projects/${encodeURIComponent(projectId)}`);
}

async function request(method, endpoint, data) {
  console.debug('[projectApi] Request started', { method, endpoint });
  try {
    const response = await axios({ method, url: endpoint, data });
    console.debug('[projectApi] Request completed', {
      method,
      endpoint,
      status: response.status,
    });
    return response.data ?? null;
  } catch (error) {
    const detail = formatDetail(error.response?.data?.detail) || error.message || 'Unknown request failure';
    console.error('[projectApi] Request failed', {
      method,
      endpoint,
      status: error.response?.status,
      detail,
    });
    const err = new Error(detail);
    err.status = error.response?.status;
    throw err;
  }
}

function formatDetail(detail) {
  if (!detail) {
    return '';
  }
  if (typeof detail === 'string') {
    return detail;
  }
  try {
    return JSON.stringify(detail);
  } catch {
    return String(detail);
  }
}
