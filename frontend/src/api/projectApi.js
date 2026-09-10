import axios from 'axios';

export class ProjectRevisionConflictError extends Error {
  constructor(message, detail = {}) {
    super(message);
    this.name = 'ProjectRevisionConflictError';
    this.status = 409;
    this.code = detail.code || 'project_revision_conflict';
    this.conflict = sanitizeConflictDetail(detail);
  }
}

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

export async function listRevisions(projectId, { branchId, limit, beforeSequence } = {}) {
  const params = new URLSearchParams();
  if (branchId != null && branchId !== '') {
    params.set('branch_id', String(branchId));
  }
  if (limit != null) {
    params.set('limit', String(limit));
  }
  if (beforeSequence != null) {
    params.set('before_sequence', String(beforeSequence));
  }
  const query = params.toString();
  const suffix = query ? `?${query}` : '';
  return request(
    'get',
    `/projects/${encodeURIComponent(projectId)}/revisions${suffix}`,
  );
}

export async function getRevision(projectId, revisionId) {
  return request(
    'get',
    `/projects/${encodeURIComponent(projectId)}/revisions/${encodeURIComponent(revisionId)}`,
  );
}

export async function commitRevision(projectId, payload) {
  return request(
    'post',
    `/projects/${encodeURIComponent(projectId)}/revisions`,
    payload,
  );
}

export async function nameRevision(projectId, revisionId, payload) {
  return request(
    'patch',
    `/projects/${encodeURIComponent(projectId)}/revisions/${encodeURIComponent(revisionId)}`,
    payload,
  );
}

export async function restoreRevision(projectId, revisionId, payload) {
  return request(
    'post',
    `/projects/${encodeURIComponent(projectId)}/revisions/${encodeURIComponent(revisionId)}/restore`,
    payload,
  );
}

export async function listBranches(projectId) {
  return request('get', `/projects/${encodeURIComponent(projectId)}/branches`);
}

export async function createBranch(projectId, payload) {
  return request(
    'post',
    `/projects/${encodeURIComponent(projectId)}/branches`,
    payload,
  );
}

export async function renameBranch(projectId, branchId, payload) {
  return request(
    'patch',
    `/projects/${encodeURIComponent(projectId)}/branches/${encodeURIComponent(branchId)}`,
    payload,
  );
}

export async function checkoutBranch(projectId, branchId, payload) {
  return request(
    'post',
    `/projects/${encodeURIComponent(projectId)}/branches/${encodeURIComponent(branchId)}/checkout`,
    payload,
  );
}

export async function applyAsBranch(projectId, payload) {
  return request(
    'post',
    `/projects/${encodeURIComponent(projectId)}/branches/apply-as-branch`,
    payload,
  );
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
    const status = error.response?.status ?? null;
    const rawDetail = error.response?.data?.detail;
    if (status === 409 && isProjectRevisionConflict(rawDetail)) {
      const conflict = sanitizeConflictDetail(rawDetail);
      console.warn('[projectApi] Revision conflict', {
        method,
        endpoint,
        status,
        code: conflict.code,
      });
      throw new ProjectRevisionConflictError(
        'Project revision conflict',
        conflict,
      );
    }
    const detail = formatDetail(rawDetail) || error.message || 'Unknown request failure';
    console.warn('[projectApi] Request failed', {
      method,
      endpoint,
      status,
    });
    const err = new Error(detail);
    err.status = status;
    throw err;
  }
}

function isProjectRevisionConflict(detail) {
  return Boolean(
    detail
    && typeof detail === 'object'
    && !Array.isArray(detail)
    && detail.code === 'project_revision_conflict',
  );
}

function sanitizeConflictDetail(detail) {
  if (!detail || typeof detail !== 'object' || Array.isArray(detail)) {
    return { code: 'project_revision_conflict' };
  }
  return {
    code: detail.code || 'project_revision_conflict',
    project_id: detail.project_id ?? null,
    expected_active_branch_id: detail.expected_active_branch_id ?? null,
    current_active_branch_id: detail.current_active_branch_id ?? null,
    expected_working_version: detail.expected_working_version ?? null,
    current_working_version: detail.current_working_version ?? null,
    expected_head_revision_id: detail.expected_head_revision_id ?? null,
    current_head_revision_id: detail.current_head_revision_id ?? null,
    expected_source_fingerprint_prefix: detail.expected_source_fingerprint_prefix ?? null,
    current_working_fingerprint_prefix: detail.current_working_fingerprint_prefix ?? null,
    current_sequence: detail.current_sequence ?? null,
  };
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
