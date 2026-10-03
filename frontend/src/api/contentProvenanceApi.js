import axios from 'axios';

import { collaborationHeaders } from '../utils/collaborationAccess.js';
import { downloadBlob, filenameFromContentDisposition } from '../utils/downloadFile.js';

export class ContentProvenanceApiError extends Error {
  constructor(code, status) {
    super(code || 'provenance_invalid');
    this.name = 'ContentProvenanceApiError';
    this.code = code || 'provenance_invalid';
    this.status = status ?? null;
  }
}

function artifactPath(projectId, kind, artifactId) {
  return (
    `/content-provenance/projects/${encodeURIComponent(projectId)}`
    + `/artifacts/${encodeURIComponent(kind)}/${encodeURIComponent(artifactId)}`
  );
}

export function compositionRevisionArtifactPath(projectId, revisionId) {
  return artifactPath(projectId, 'composition_revision', revisionId);
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
    const code = (typeof detail === 'object' && detail?.code)
      || error.response?.data?.code
      || 'provenance_invalid';
    throw new ContentProvenanceApiError(code, status);
  }
}

export async function getContentProvenanceStatus() {
  return request('get', '/content-provenance/status');
}

export async function getContentProvenanceChain(projectId, kind, artifactId) {
  return request('get', `${artifactPath(projectId, kind, artifactId)}/chain`);
}

export async function getContentProvenanceManifest(projectId, kind, artifactId) {
  return request('get', `${artifactPath(projectId, kind, artifactId)}/manifest`);
}

export async function downloadContentProvenanceManifest(projectId, kind, artifactId) {
  try {
    const response = await axios({
      method: 'get',
      url: `${artifactPath(projectId, kind, artifactId)}/manifest/download`,
      responseType: 'blob',
      headers: collaborationHeaders(),
    });
    const filename = filenameFromContentDisposition(
      response.headers?.['content-disposition'],
      `provenance_${kind}_${String(artifactId).slice(0, 24)}.json`,
    );
    downloadBlob(response.data, filename);
    return filename;
  } catch (error) {
    const status = error.response?.status ?? null;
    let code = 'provenance_invalid';
    try {
      if (error.response?.data instanceof Blob) {
        const text = await error.response.data.text();
        const parsed = JSON.parse(text);
        code = parsed?.detail?.code || parsed?.code || code;
      }
    } catch {
      // keep default
    }
    throw new ContentProvenanceApiError(code, status);
  }
}
