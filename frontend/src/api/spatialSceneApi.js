import axios from 'axios';

import { createAppLogger } from '../utils/appLogger.js';
import { collaborationHeaders } from '../utils/collaborationAccess.js';

const logger = createAppLogger('spatialSceneApi');

function scenesPath(projectId) {
  return `/projects/${encodeURIComponent(projectId)}/spatial-scenes`;
}

function scenePath(projectId, sceneId) {
  return `${scenesPath(projectId)}/${encodeURIComponent(sceneId)}`;
}

async function request(method, pathTemplate, endpoint, data) {
  logger.debug('Spatial scene request', { method, path: pathTemplate });
  try {
    const response = await axios({
      method,
      url: endpoint,
      data,
      headers: collaborationHeaders(),
    });
    logger.debug('Spatial scene request ok', {
      method,
      path: pathTemplate,
      status: response.status,
    });
    return response.data ?? null;
  } catch (error) {
    const status = error.response?.status ?? null;
    const detail = error.response?.data?.detail;
    const code = typeof detail?.code === 'string' ? detail.code : null;
    logger.debug('Spatial scene request failed', {
      method,
      path: pathTemplate,
      status,
      code,
    });
    const err = new Error(
      typeof detail?.message === 'string' ? detail.message : (error.message || 'Request failed'),
    );
    err.status = status;
    err.code = code;
    err.details = detail?.details ?? null;
    throw err;
  }
}

export async function listSpatialScenes(projectId) {
  return request('GET', 'list', scenesPath(projectId));
}

export async function listSpatialPresets(projectId) {
  return request('GET', 'presets', `${scenesPath(projectId)}/presets`);
}

export async function createSpatialScene(projectId, body) {
  return request('POST', 'create', scenesPath(projectId), body);
}

export async function getSpatialScene(projectId, sceneId) {
  return request('GET', 'get', scenePath(projectId, sceneId));
}

export async function updateSpatialScene(projectId, sceneId, body) {
  return request('PUT', 'put', scenePath(projectId, sceneId), body);
}

export async function deleteSpatialScene(projectId, sceneId) {
  return request('DELETE', 'delete', scenePath(projectId, sceneId));
}

/**
 * Compile with request composition / stem metadata (draft-safe; no project write).
 */
export async function compileSpatialScene(projectId, sceneId, body) {
  return request('POST', 'compile', `${scenePath(projectId, sceneId)}/compile`, body);
}
